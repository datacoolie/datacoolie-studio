"""Canonical filtered destination signals, calculated before SQL pagination."""
from __future__ import annotations

import pyarrow as pa

from datacoolie_studio.core.request_timing import timed_stage
from datacoolie_studio.domains.analytics.serving_facts import MONITORING_DATAFLOW_FACTS_TABLE
from datacoolie_studio.domains.assets.destination_layout import DestinationLayoutContext
from datacoolie_studio.domains.monitoring.read_models.common import json_value


def prepare_layout_flags(conn, scope_sql, params, identity, latest_order):
    """Normalize slim canonical latest contexts in bounded batches, once per request."""
    scope_sql = scope_sql.split(', destination_latest AS (', 1)[0]
    latest = scope_sql + f""", latest AS (
      SELECT * FROM destination_scope
      QUALIFY ROW_NUMBER() OVER(PARTITION BY target ORDER BY {latest_order})=1
    )"""
    with timed_stage('destination_layout_flags'):
        conn.execute('CREATE OR REPLACE TEMP TABLE _monitoring_destination_layout_latest AS '+latest+' SELECT * FROM latest',params)
        has_resolved = conn.execute("SELECT EXISTS(SELECT 1 FROM _monitoring_destination_layout_latest WHERE identity_status='resolved')").fetchone()[0]
        context = DestinationLayoutContext.prepare(
            identity.session, identity.environment_id,
            metadata_revision=identity.mapping.metadata_revision, include_metadata=has_resolved,
        )
        cursor = conn.execute(f""" SELECT l.target,l.identity_status,l.workspace_id,
          l.dataflow_id,l.dataflow_name,l.destination_id,l.destination_name,l.destination_connection_type,
          l.destination_format,l.destination_load_type,l.event_time AS latest_time,l.dataflow_run_id,
          f.destination_partition_columns
          FROM _monitoring_destination_layout_latest l JOIN {MONITORING_DATAFLOW_FACTS_TABLE} f
          ON f._source_id=l._source_id AND f._file_uri=l._file_uri AND f._record_line=l._record_line
          WHERE l.identity_status='resolved' OR NULLIF(TRIM(f.destination_partition_columns),'') IS NOT NULL""")
        columns = [column[0] for column in cursor.description]
        flags = []
        while batch := cursor.fetchmany(500):
            records = [{key: json_value(value) for key, value in zip(columns, values)} for values in batch]
            context.enrich(records)
            for record in records:
                layout = record['layout']
                conflicts = sorted({item['field'] for item in layout['conflicts']})
                invalid = sorted(set(layout['invalid_fields']))
                if conflicts or invalid:
                    flags.append(dict(target=record['target'], conflict_fields=conflicts, invalid_fields=invalid))
        schema = pa.schema([('target', pa.string()), ('conflict_fields', pa.list_(pa.string())), ('invalid_fields', pa.list_(pa.string()))])
        conn.register('_monitoring_layout_flags_input', pa.Table.from_pylist(flags, schema=schema))
        try:
            conn.execute('CREATE OR REPLACE TEMP TABLE _monitoring_destination_layout_flags AS SELECT * FROM _monitoring_layout_flags_input')
        finally:
            conn.unregister('_monitoring_layout_flags_input')
        return context


# Measurement coverage is independent of legacy total/asterisk semantics.
_COVERAGE = (
    ('workload_rows_read', 'source_rows_read', False, 'Rows read'),
    ('workload_rows_written', 'destination_rows_written', False, 'Rows written'),
    ('files_added', 'destination_files_added', True, 'Files added'),
    ('files_removed', 'destination_files_removed', True, 'Files removed'),
    ('bytes_added', 'destination_bytes_added', True, 'Bytes added'),
    ('bytes_removed', 'destination_bytes_removed', True, 'Bytes removed'),
)


def signals_sql():
    affected = []
    coverage = []
    counts = []
    for key, column, physical, label in _COVERAGE:
        eligible = 'd.physical_effect_eligible' if physical else 'TRUE'
        present = f'COALESCE(d.{column}>=0,FALSE)'
        affected.append(f'({eligible} AND NOT {present})')
        counts.extend((f'COUNT(*) FILTER(WHERE {eligible}) AS {key}_signal_eligible',
                       f'COUNT(*) FILTER(WHERE {eligible} AND {present}) AS {key}_signal_measured'))
        coverage.append(f"CASE WHEN i.{key}_signal_measured<i.{key}_signal_eligible THEN CONCAT('{label}: ',i.{key}_signal_measured,' / ',i.{key}_signal_eligible,' eligible reports measured') END")
    # Code, severity, category, predicate, affected count, exact explanation.
    definitions = (
        ('destination_failed', 'error', 'operation', 'destination_failure_only_runs>0', 'destination_failure_only_runs', "['Destination phase reported failure/error; cancellations are counted separately']"),
        ('run_failed_destination_unknown', 'error', 'operation', 'run_failed_destination_unknown_runs>0', 'run_failed_destination_unknown_runs', "['Root run failed and destination phase status was not recorded; failure location cannot be determined']"),
        ('destination_cancelled', 'review', 'operation', 'destination_cancelled_runs>0', 'destination_cancelled_runs', "['Destination phase reported cancelled/canceled; separate from destination failures']"),
        ('identity_conflict', 'review', 'evidence', "identity_status='conflict'", '1', '[identity_reason]'),
        ('layout_conflict', 'review', 'evidence', 'LENGTH(conflict_fields)>0', '1', "[CONCAT('Conflicting captured/published configuration fields: ',ARRAY_TO_STRING(conflict_fields,', ')), 'Latest captured values take precedence; see Write layout for origins']"),
        ('layout_invalid', 'review', 'evidence', 'LENGTH(invalid_fields)>0', '1', "[CONCAT('Invalid configuration fields: ',ARRAY_TO_STRING(invalid_fields,', ')), 'Invalid captured configuration is not replaced by metadata']"),
        ('identity_unresolved', 'missing', 'evidence', "identity_status='unresolved'", '1', '[identity_reason]'),
        ('incomplete_metrics', 'missing', 'evidence', 'incomplete_metric_runs>0', 'incomplete_metric_runs', 'incomplete_metric_details'),
        ('effect_unconfirmed', 'missing', 'evidence', 'unqualified_effect_runs>0', 'unqualified_effect_runs', "['Reports lack confirmed non-dry-run destination success; physical changes are unconfirmed, independently of measurement coverage']"),
        ('repeated_reports', 'missing', 'evidence', 'matching_run_reports>0', 'matching_run_reports', "['Reports sharing workspace/session/job/run identity were retained; totals are report-based, not deduplicated']"),
        ('outcome_unknown', 'missing', 'operation', 'unknown>0', 'unknown', "['Root run outcome is missing or unrecognized']"),
        ('running', 'info', 'operation', 'running>0', 'running', "['Root runs reported Running within the current filters; activity information, not a failure']"),
        ('pending', 'info', 'operation', 'pending>0', 'pending', "['Root runs reported Pending within the current filters; activity information, not a failure']"),
        ('format_changed', 'info', 'evidence', 'LENGTH(signal_format_variants)>1', 'LENGTH(signal_format_variants)', "[CONCAT('Known formats recorded in filter: ',ARRAY_TO_STRING(signal_format_variants,', ')), 'Historical variation alone does not establish a configuration conflict']"),
    )
    ranks = dict(error=4, review=3, missing=2, info=1)
    branches = [f"SELECT target,'{code}' AS code,'{severity}' AS severity,{ranks[severity]} AS severity_rank,'{category}' AS category,CAST({count} AS HUGEINT) AS affected_count,LIST_FILTER({details},item->item IS NOT NULL AND item<>'') AS details FROM destination_signal_context WHERE {condition}"
                for code, severity, category, condition, count, details in definitions]
    return f"""
    , destination_incomplete_metrics AS (
      SELECT d.target,COUNT(*) FILTER(WHERE {' OR '.join(affected)}) AS incomplete_metric_runs,
        {','.join(counts)}
      FROM destination_scope d GROUP BY d.target
    ), destination_signal_context AS (
      SELECT r.*,i.incomplete_metric_runs,
        LIST_FILTER(r.format_variants,item->item NOT IN ('unknown','null','none')) AS signal_format_variants,
        LIST_FILTER([{','.join(coverage)}],item->item IS NOT NULL) AS incomplete_metric_details,
        COALESCE(f.conflict_fields,[]) AS conflict_fields,COALESCE(f.invalid_fields,[]) AS invalid_fields
      FROM destination_registry r JOIN destination_incomplete_metrics i USING(target)
      LEFT JOIN _monitoring_destination_layout_flags f USING(target)
    ), destination_signal_items AS ({' UNION ALL '.join(branches)}), destination_signal_lists AS (
      SELECT target,LIST(STRUCT_PACK(code:=code,severity:=severity,category:=category,count:=affected_count,details:=details)
        ORDER BY severity_rank DESC,affected_count DESC,code) AS destination_signals,
        MAX(severity_rank) AS signal_severity
      FROM destination_signal_items GROUP BY target
    ), destination_signal_ranks AS (
      SELECT target,MAX(affected_count) AS signal_affected_count FROM destination_signal_items
      JOIN destination_signal_lists USING(target) WHERE severity_rank=signal_severity GROUP BY target
    ), destination_registry_with_signals AS (
      SELECT r.* EXCLUDE(attention_priority,attention_reason),COALESCE(s.destination_signals,[]) AS destination_signals,
        COALESCE(s.signal_severity,0) AS attention_priority,COALESCE(a.signal_affected_count,0) AS signal_affected_count,
        COALESCE(ARRAY_TO_STRING(LIST_TRANSFORM(s.destination_signals,item->item.code),' · '),'') AS attention_reason
      FROM destination_registry r LEFT JOIN destination_signal_lists s USING(target)
      LEFT JOIN destination_signal_ranks a USING(target)
    )
    """

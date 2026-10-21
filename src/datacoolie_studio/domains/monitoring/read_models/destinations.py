"""Filtered destination activity and coherent observations, DuckDB dialect."""
from __future__ import annotations

import json
import re
from typing import Any

from datacoolie_studio.domains.analytics.serving_facts import MONITORING_JOB_FACTS_TABLE
from datacoolie_studio.domains.analytics.serving_facts import MONITORING_DATAFLOW_FACTS_TABLE
from datacoolie_studio.core.request_timing import timed_stage
from datacoolie_studio.domains.assets.endpoint_identity import destination_signature_sql
from datacoolie_studio.domains.assets.identity_mapping import IDENTITY_MAP_TABLE
from datacoolie_studio.domains.assets.format_policy import canonical_format_sql
from datacoolie_studio.domains.monitoring.read_models.destination_state import state_ctes, attach_recorded_state

from datacoolie_studio.domains.monitoring.metrics.destination_identity import (
    contributing_dataflow_key_sql, destination_declared_sql,
)
from datacoolie_studio.domains.monitoring.read_models.common import (
    AnalyticsContext, filtered_ctes, one, paged_rows, reader_context, rows,
)
from datacoolie_studio.domains.monitoring.read_models.maintenance import (
    _IS_MAINTENANCE, _LATEST_RUN_ORDER, _NO_OP, _POSITIVE_EFFECT, _EVIDENCE_AGGREGATES,
)
from datacoolie_studio.domains.monitoring.read_models.destination_signals import prepare_layout_flags, signals_sql

_METRICS = {name: f"destination_{name}" for name in (
    "rows_written", "rows_inserted", "rows_updated", "rows_deleted",
    "files_added", "files_removed", "bytes_added", "bytes_removed",
)}
_DATAFLOW_COLUMNS = (
    "_source_id", "_file_uri", "_record_line", "workspace_id", "job_id", "dataflow_id",
    "dataflow_run_id", "dataflow_name", "stage", "status", "message", "error_message",
    "start_time", "end_time", "duration_seconds", "operation_type", "processing_mode",
    "source_name", "source_full_table", "source_table", "source_path", "source_rows_read",
    "destination_id", "destination_name", "destination_connection_type", "destination_format",
    "destination_catalog", "destination_database", "destination_schema", "destination_table",
    "destination_full_table", "destination_path", "destination_load_type", "destination_configure",
    "destination_operation_type", "destination_status", "destination_duration_seconds",
    "destination_bytes_saved",
    * _METRICS.values(),
)
_JOB_COLUMNS = ("_source_id", "job_id")



def _metric_select() -> str:
    fields: list[str] = []
    for name, column in _METRICS.items():
        fields.extend((
            f"SUM({column}) FILTER(WHERE physical_effect_eligible AND {column} >= 0) AS {name}_observed",
            f"COUNT({column}) FILTER(WHERE physical_effect_eligible AND {column} >= 0) AS {name}_measured_runs",
            f"AVG({column}) FILTER(WHERE physical_effect_eligible AND {column} >= 0) AS {name}_avg_per_run",
            f"CASE WHEN EXISTS(SELECT 1 FROM all_log_versions WHERE log_schema_version>=7) OR COUNT({column}) FILTER(WHERE physical_effect_eligible AND {column}>=0)=COUNT(*) THEN SUM({column}) FILTER(WHERE physical_effect_eligible AND {column}>=0) END AS {name}",
        ))
    for metric, column in (("rows", "destination_rows_written"), ("files", "destination_files_added"), ("bytes", "destination_bytes_added"), ("files_removed", "destination_files_removed"), ("bytes_removed", "destination_bytes_removed")):
        eligible = f"physical_effect_eligible AND {column} >= 0 AND duration_seconds >= 0"
        paired_key = f"paired_{metric}{'' if metric.endswith('_removed') else ('_written' if metric=='rows' else '_added')}"
        complete = f"EXISTS(SELECT 1 FROM all_log_versions WHERE log_schema_version>=7) OR COUNT(*) FILTER(WHERE {eligible})=COUNT(*)"
        fields.extend((
            f"SUM({column}) FILTER(WHERE {eligible}) AS {paired_key}_observed",
            f"CASE WHEN {complete} THEN SUM({column}) FILTER(WHERE {eligible}) END AS {paired_key}",
            f"SUM(duration_seconds) FILTER(WHERE {eligible}) AS paired_{metric}_runtime_observed",
            f"CASE WHEN {complete} THEN SUM(duration_seconds) FILTER(WHERE {eligible}) END AS paired_{metric}_runtime",
            f"COUNT(*) FILTER(WHERE {eligible}) AS paired_{metric}_runs",
        ))
    return ',\n'.join(fields)


def _workload_select(*, include_average: bool = False, include_files_bytes: bool = False) -> str:
    fields: list[str] = []
    metrics = [("workload_rows_read", "source_rows_read"), ("workload_rows_written", "destination_rows_written")]
    if include_files_bytes:
        metrics.extend((f"workload_{unit}_{effect}", f"destination_{unit}_{effect}")
                       for unit in ('files','bytes') for effect in ('added','removed'))
    for metric, column in metrics:
        measured = f"COUNT({column}) FILTER(WHERE {column} >= 0)"
        observed = f"SUM({column}) FILTER(WHERE {column} >= 0)"
        fields.extend((
            f"{observed} AS {metric}_observed",
            f"{measured} AS {metric}_measured_runs",
            f"COUNT(*) AS {metric}_eligible_runs",
            f"CASE WHEN EXISTS(SELECT 1 FROM all_log_versions WHERE log_schema_version>=7) "
            f"OR {measured}=COUNT(*) THEN {observed} END AS {metric}",
        ))
        if include_average:
            fields.append(f"AVG({column}) FILTER(WHERE {column} >= 0) AS {metric}_avg_per_run")
    return ',\n'.join(fields)


_FORMAT_SQL = canonical_format_sql('destination_format')
_MAINTENANCE_TARGETS_SQL = f'''targets AS (
 SELECT target,
   BOOL_OR(COALESCE({_FORMAT_SQL} IN ('delta','iceberg'),FALSE)) AS is_lakehouse,
   BOOL_OR(is_maintenance) AS attempted,
   BOOL_OR(is_maintenance AND physical_effect_eligible) AS successful
 FROM _monitoring_destination_scope GROUP BY target
)'''

_MAINTENANCE_COVERAGE_SQL = 'WITH ' + _MAINTENANCE_TARGETS_SQL + '''
SELECT COUNT(*) FILTER(WHERE is_lakehouse) AS maintenance_lakehouse_destinations,
 COUNT(*) FILTER(WHERE is_lakehouse AND attempted) AS maintenance_attempted_destinations,
 COUNT(*) FILTER(WHERE is_lakehouse AND successful) AS maintenance_successful_destinations,
 COUNT(*) FILTER(WHERE is_lakehouse AND NOT attempted) AS maintenance_unrecorded_destinations,
 100.0 * COUNT(*) FILTER(WHERE is_lakehouse AND successful) /
   NULLIF(COUNT(*) FILTER(WHERE is_lakehouse),0) AS maintenance_coverage_rate
FROM targets'''

# One classifier for the chart and each destination's latest maintenance.
# Inputs must describe the same canonically selected maintenance row.
_MAINTENANCE_OUTCOME = """CASE
 WHEN job_dry_run=TRUE THEN 'dry_run'
 WHEN normalized_status IN ('failed','skipped','running','pending') THEN normalized_status
 WHEN normalized_status='succeeded' AND physical_effect_eligible THEN 'succeeded'
 ELSE 'unknown' END"""

_MAINTENANCE_STATUS_SQL = 'WITH ' + _MAINTENANCE_TARGETS_SQL + f''',
latest AS (
 SELECT target,normalized_status,job_dry_run,physical_effect_eligible
 FROM _monitoring_destination_scope WHERE is_maintenance
 QUALIFY ROW_NUMBER() OVER(PARTITION BY target ORDER BY {_LATEST_RUN_ORDER})=1
), classified AS (
 SELECT CASE WHEN NOT t.attempted THEN 'not_recorded'
   ELSE {_MAINTENANCE_OUTCOME} END AS status
 FROM targets t LEFT JOIN latest l USING(target) WHERE t.is_lakehouse
)
SELECT status,COUNT(*) AS count,SUM(COUNT(*)) OVER() AS population_count
FROM classified GROUP BY status
ORDER BY CASE status WHEN 'succeeded' THEN 0 WHEN 'failed' THEN 1
 WHEN 'skipped' THEN 2 WHEN 'running' THEN 3 WHEN 'pending' THEN 4
 WHEN 'dry_run' THEN 5 WHEN 'unknown' THEN 6 ELSE 7 END'''


_BASE = f"""
, destination_scope_base AS (
 SELECT d.* EXCLUDE(destination_catalog,destination_database,destination_schema,destination_table,destination_full_table,destination_path),
 m.destination_catalog,m.destination_database,m.destination_schema,m.destination_table,m.destination_full_table,m.destination_path,
 m.target,m.identity_status,m.identity_reason,m.identity_version,m.identity_provenance,m.identity_metadata_sources,
 m.identity_status='resolved' AS identity_resolved,
 {contributing_dataflow_key_sql('d')} AS contributor_key,
 {_IS_MAINTENANCE} AS is_maintenance,
 j.dry_run AS job_dry_run,
 COALESCE(j.dry_run = FALSE, FALSE) AND
 LOWER(COALESCE(destination_status,'')) IN ('succeeded','success') AS physical_effect_eligible,
 COUNT(*) OVER(PARTITION BY d.workspace_id,d.log_session_id,d.job_id,d.dataflow_run_id) AS matching_run_reports
 FROM filtered_dataflows d
 JOIN {IDENTITY_MAP_TABLE} m ON m.signature={destination_signature_sql('d')}
 LEFT JOIN {MONITORING_JOB_FACTS_TABLE} jf ON jf._source_id=d._source_id
 AND jf.job_id=d.job_id AND jf.log_session_id IS NOT DISTINCT FROM d.log_session_id
 LEFT JOIN etl_job_runs j ON j._source_id=jf._source_id AND j._file_uri=jf._file_uri
 AND j._record_line=jf._record_line
 WHERE {destination_declared_sql('d')} /* DESTINATION_INVESTIGATION */
), destination_scope AS (
 SELECT *, NULL::JSON AS operation_details FROM destination_scope_base
)
"""

_ROLLUP = f"""
, destination_latest AS (
 SELECT * FROM destination_scope
 QUALIFY ROW_NUMBER() OVER(PARTITION BY target ORDER BY {_LATEST_RUN_ORDER})=1
), destination_workload_rollup AS (
 SELECT target,{_workload_select(include_average=True)} FROM destination_scope GROUP BY target
), destination_latest_maintenance AS (
 SELECT target,{_MAINTENANCE_OUTCOME} AS latest_maintenance_outcome
 FROM destination_scope WHERE is_maintenance
 QUALIFY ROW_NUMBER() OVER(PARTITION BY target ORDER BY {_LATEST_RUN_ORDER})=1
), destination_rollup AS (
 SELECT target, COUNT(*) AS run_count,
 COUNT(*) FILTER(WHERE normalized_status='succeeded') AS succeeded,
 COUNT(*) FILTER(WHERE normalized_status='failed') AS failed,
 COUNT(*) FILTER(WHERE normalized_status='skipped') AS skipped,
 COUNT(*) FILTER(WHERE normalized_status='running') AS running,
 COUNT(*) FILTER(WHERE normalized_status='pending') AS pending,
 COUNT(*) FILTER(WHERE normalized_status NOT IN ('succeeded','failed','skipped','running','pending')) AS unknown,
 COUNT(*) FILTER(WHERE LOWER(COALESCE(destination_status,'')) IN ('failed','failure','error','cancelled','canceled')) AS destination_failed_runs,
 COUNT(*) FILTER(WHERE LOWER(TRIM(COALESCE(destination_status,''))) IN ('failed','failure','error')) AS destination_failure_only_runs,
 COUNT(*) FILTER(WHERE LOWER(TRIM(COALESCE(destination_status,''))) IN ('cancelled','canceled')) AS destination_cancelled_runs,
 COUNT(*) FILTER(WHERE normalized_status='failed' AND NULLIF(TRIM(destination_status),'') IS NULL) AS run_failed_destination_unknown_runs,
 COUNT(*) FILTER(WHERE normalized_status='failed' AND destination_status IS NULL) AS upstream_failed_runs,
 COUNT(*) FILTER(WHERE NOT physical_effect_eligible) AS unqualified_effect_runs,
 COUNT(*) FILTER(WHERE matching_run_reports>1) AS matching_run_reports,
 COUNT(DISTINCT contributor_key) AS contributing_dataflow_count,
 COUNT(DISTINCT COALESCE({_FORMAT_SQL},'unknown')) AS format_count,
 COALESCE(LIST_SORT(LIST(DISTINCT {_FORMAT_SQL}) FILTER(WHERE NULLIF(TRIM(destination_format),'') IS NOT NULL)),[]) AS format_variants,
 COALESCE(LIST_SORT(LIST(DISTINCT LOWER(TRIM(destination_load_type))) FILTER(WHERE LOWER(TRIM(destination_load_type)) NOT IN ('','unknown','null','none'))),[]) AS load_type_variants,
 COUNT(*) FILTER(WHERE NULLIF(TRIM(destination_format),'') IS NULL) AS format_unknown_runs,
 BOOL_OR({_FORMAT_SQL} IN ('delta','iceberg')) AS has_lakehouse_format,
 COUNT(DISTINCT _source_id) AS log_source_count,
 COUNT(*) FILTER(WHERE is_maintenance) AS maintenance_run_count,
 COUNT(*) FILTER(WHERE is_maintenance AND normalized_status='succeeded') AS maintenance_succeeded,
 COUNT(*) FILTER(WHERE is_maintenance AND normalized_status='failed') AS maintenance_failed,
 COUNT(*) FILTER(WHERE is_maintenance AND normalized_status='skipped') AS maintenance_skipped,
 COUNT(*) FILTER(WHERE is_maintenance AND normalized_status='running') AS maintenance_running,
 COUNT(*) FILTER(WHERE is_maintenance AND normalized_status='pending') AS maintenance_pending,
 COUNT(*) FILTER(WHERE is_maintenance AND normalized_status NOT IN ('succeeded','failed','skipped','running','pending')) AS maintenance_unknown,
 MAX(event_time) FILTER(WHERE is_maintenance) AS latest_maintenance_time,
 MAX(event_time) FILTER(WHERE is_maintenance AND normalized_status='succeeded') AS latest_successful_maintenance_time,
 COUNT(*) FILTER(WHERE is_maintenance AND normalized_status='succeeded' AND physical_effect_eligible) AS maintenance_qualified_success_runs,
 MAX(event_time) FILTER(WHERE is_maintenance AND normalized_status='succeeded' AND physical_effect_eligible) AS latest_qualified_successful_maintenance_time,
 MAX(event_time) FILTER(WHERE LOWER(operation_type)='etl' AND physical_effect_eligible AND GREATEST(COALESCE(destination_rows_written,0),COALESCE(destination_rows_inserted,0),COALESCE(destination_rows_updated,0),COALESCE(destination_rows_deleted,0),COALESCE(destination_files_added,0),COALESCE(destination_bytes_added,0))>0) AS latest_etl_write_time,
 BOOL_OR(physical_effect_eligible AND GREATEST(COALESCE(destination_rows_written,0),COALESCE(destination_rows_inserted,0),COALESCE(destination_rows_updated,0),COALESCE(destination_rows_deleted,0),COALESCE(destination_files_added,0),COALESCE(destination_files_removed,0),COALESCE(destination_bytes_added,0),COALESCE(destination_bytes_removed,0))>0) AS active_destination,
 SUM(duration_seconds) FILTER(WHERE duration_seconds>=0) AS duration_observed,
 CASE WHEN COUNT(duration_seconds) FILTER(WHERE duration_seconds>=0)=COUNT(*) THEN SUM(duration_seconds) END AS duration_seconds,
 COUNT(duration_seconds) FILTER(WHERE duration_seconds>=0) AS duration_measured_runs,
 AVG(duration_seconds) FILTER(WHERE duration_seconds>=0) AS avg_duration_seconds,
 quantile_disc(duration_seconds,0.95) FILTER(WHERE duration_seconds>=0) AS p95_duration_seconds,
 SUM(destination_duration_seconds) FILTER(WHERE destination_duration_seconds>=0) AS destination_duration_seconds,
 COUNT(*) FILTER(WHERE destination_duration_seconds>=0) AS destination_duration_measured_runs,
 AVG(destination_duration_seconds) FILTER(WHERE destination_duration_seconds>=0) AS avg_destination_duration_seconds,
 SUM(destination_bytes_added-destination_bytes_removed) FILTER(WHERE physical_effect_eligible AND destination_bytes_added>=0 AND destination_bytes_removed>=0) AS net_bytes_observed,
 CASE WHEN EXISTS(SELECT 1 FROM all_log_versions WHERE log_schema_version>=7) OR COUNT(*) FILTER(WHERE physical_effect_eligible AND destination_bytes_added>=0 AND destination_bytes_removed>=0)=COUNT(*)
 THEN SUM(destination_bytes_added-destination_bytes_removed) FILTER(WHERE physical_effect_eligible AND destination_bytes_added>=0 AND destination_bytes_removed>=0) END AS net_bytes,
 COUNT(*) FILTER(WHERE physical_effect_eligible AND destination_bytes_added>=0 AND destination_bytes_removed>=0) AS net_bytes_paired_runs,
 {_metric_select()}
 FROM destination_scope GROUP BY target
), destination_contributors AS (
 SELECT target,contributor_key,
 ARG_MAX(STRUCT_PACK(dataflow_id:=dataflow_id,dataflow_name:=dataflow_name,stage:=stage,source_name:=source_name,
 source_full_table:=source_full_table,source_table:=source_table,source_path:=source_path,
 operation_type:=operation_type,destination_operation_type:=destination_operation_type,
 latest_status:=normalized_status,latest_time:=event_time,_source_id:=_source_id,
 log_session_id:=log_session_id,job_id:=job_id,dataflow_run_id:=dataflow_run_id),
 STRUCT_PACK(t:=event_time,r:=dataflow_run_id,s:=_source_id)) AS latest,
 COUNT(*) AS run_count
 FROM destination_scope GROUP BY target,contributor_key
), destination_contributor_lists AS (
 SELECT target,LIST(STRUCT_PACK(dataflow_id:=latest.dataflow_id,dataflow_name:=latest.dataflow_name,
 stage:=latest.stage,source_name:=latest.source_name,source_full_table:=latest.source_full_table,
 source_table:=latest.source_table,source_path:=latest.source_path,operation_type:=latest.operation_type,
 destination_operation_type:=latest.destination_operation_type,latest_status:=latest.latest_status,
 latest_time:=latest.latest_time,_source_id:=latest._source_id,log_session_id:=latest.log_session_id,
 job_id:=latest.job_id,dataflow_run_id:=latest.dataflow_run_id,run_count:=run_count,
 contributor_key:=contributor_key) ORDER BY run_count DESC,contributor_key)[:100] AS upstream_dataflows
 FROM destination_contributors GROUP BY target
), destination_registry AS (
 SELECT r.*,w.* EXCLUDE(target),lm.latest_maintenance_outcome,l.destination_id,l.destination_name,l.destination_connection_type,l.destination_format,
 l.destination_table,l.destination_full_table,l.destination_path,
 l.destination_catalog,l.destination_database,l.destination_schema,
 l.destination_load_type,
 l.workspace_id,l.dataflow_id,l.dataflow_name,
 COALESCE(NULLIF(l.destination_full_table,''),NULLIF(l.destination_table,''),NULLIF(l.destination_path,''),'Unknown destination') AS target_display,
 l.identity_status,l.identity_reason,l.identity_version,l.identity_provenance,l.identity_metadata_sources,
 l.event_time AS latest_time,l.normalized_status AS latest_status,l.normalized_status AS status,
 l.operation_type AS root_operation_type,l.destination_operation_type,l.destination_status,
 l.log_session_id,l.job_id,l.dataflow_run_id,l._source_id AS latest_source_id,
 l._file_uri AS latest_file_uri,l._file_kind AS latest_file_kind,l._record_line AS latest_record_line,
 l.message AS latest_message,l.error_message AS latest_error_message,
 CASE WHEN maintenance_run_count>0 THEN 'recorded' ELSE 'unknown' END AS maintenance_applicability,
 'unknown' AS maintenance_capability,
 CASE WHEN maintenance_run_count>0 THEN 'recorded' ELSE 'not_recorded' END AS maintenance_evidence_state,
 CASE WHEN destination_failed_runs>0 THEN 'has_issues' ELSE 'no_evidence' END AS table_health,
 CONCAT_WS(' · ',CASE WHEN destination_failed_runs>0 THEN CONCAT(destination_failed_runs,' destination failures') END,
 CASE WHEN upstream_failed_runs>0 THEN CONCAT(upstream_failed_runs,' failures before destination') END,
 CASE WHEN running+pending>0 THEN CONCAT(running+pending,' active runs') END,
 CASE WHEN unknown>0 THEN CONCAT(unknown,' unknown outcomes') END,
 CASE WHEN NOT l.identity_resolved THEN 'Unresolved binding' END,
 CASE WHEN LENGTH(format_variants)>1 THEN 'Format changed in filter' END,
 CASE WHEN r.matching_run_reports>0 THEN 'Repeated run identity; reports retained' END,
 CASE WHEN unqualified_effect_runs>0 THEN CONCAT(unqualified_effect_runs,' runs without qualified physical effects') END) AS attention_reason,
 CASE WHEN destination_failed_runs>0 THEN 100 WHEN upstream_failed_runs>0 THEN 80
 WHEN running+pending>0 THEN 70 WHEN NOT l.identity_resolved OR unknown>0 THEN 10 ELSE 0 END AS attention_priority,
 COALESCE(c.upstream_dataflows,[]) AS upstream_dataflows
 FROM destination_rollup r JOIN destination_latest l USING(target)
 JOIN destination_workload_rollup w USING(target)
 LEFT JOIN destination_contributor_lists c USING(target)
 LEFT JOIN destination_latest_maintenance lm USING(target)
)
"""

_SNAPSHOTS = state_ctes()

_MAINTENANCE = f"""
, destination_maintenance_parsed AS (
 SELECT d.* EXCLUDE(operation_details,{','.join(_METRICS.values())},destination_bytes_saved),
 {','.join(f'CASE WHEN physical_effect_eligible THEN {column} END AS {column}' for column in (*_METRICS.values(),'destination_bytes_saved'))},
 CASE WHEN physical_effect_eligible THEN d.destination_operation_details END AS operation_details
 FROM destination_scope d
 WHERE is_maintenance
), destination_maintenance_effects AS (
 SELECT *, {_NO_OP} AS is_no_op FROM destination_maintenance_parsed
), destination_maintenance_comparable AS (
 SELECT *, COALESCE(is_no_op OR (normalized_status='succeeded' AND ({_POSITIVE_EFFECT})),FALSE) AS is_comparable
 FROM destination_maintenance_effects
), destination_maintenance_rollup AS (
 SELECT target, COUNT(*) FILTER(WHERE is_no_op) AS maintenance_no_op_runs,
 FIRST(normalized_status ORDER BY {_LATEST_RUN_ORDER}) AS latest_maintenance_status,
 {','.join(f'{sql} AS maintenance_{key}' for key,sql in _EVIDENCE_AGGREGATES.items())}
 FROM destination_maintenance_comparable GROUP BY target
)
"""


def _query(source_ids: list[int], filters: dict[str, str], *, snapshots: bool = False) -> tuple[str, list[Any]]:
    columns = (*_DATAFLOW_COLUMNS, "destination_operation_details") if snapshots else _DATAFLOW_COLUMNS
    ordinary_filters = dict(filters)
    target = None
    if filters.get('investigateKind') == 'physical_destination':
        target = filters.get('investigateValue')
        ordinary_filters.pop('investigateKind', None)
        ordinary_filters.pop('investigateValue', None)
    if filters.get('_destinationTarget'):
        target=filters['_destinationTarget']
    ctes, params = filtered_ctes(source_ids, ordinary_filters, dataflow_columns=columns, job_columns=_JOB_COLUMNS)
    if target is not None:
        # Keep canonical external filters, but prune roots to the selected
        # Assets target before fetching native phase details and aggregating.
        # The same identity-map predicate remains in the bound scope below.
        head,tail=ctes.split('filtered_dataflows AS (',1)
        tail=tail.replace(f'FROM {MONITORING_DATAFLOW_FACTS_TABLE} d\n',f'''FROM (SELECT d.* FROM {MONITORING_DATAFLOW_FACTS_TABLE} d
          JOIN {IDENTITY_MAP_TABLE} selected ON selected.signature={destination_signature_sql('d')}
          WHERE selected.target=?) d
''',1)
        ctes=head+'filtered_dataflows AS ('+tail
        params.insert(2*len(source_ids),target)
    base = _BASE.replace('/* DESTINATION_INVESTIGATION */', ' AND m.target=?' if target is not None else '')
    if target is not None:
        params.append(target)
    sql = ctes + base + _ROLLUP + (_SNAPSHOTS if snapshots else "")
    if snapshots:
        # Snapshot observations must themselves lie within the external range,
        # rather than importing older table state from an in-range root run.
        start = filters.get('startTime') if filters.get('range')=='custom' else filters.get('_relativeStartTime')
        end = filters.get('endTime') if filters.get('range')=='custom' else None
        if start:
            restriction = ' AND COALESCE(snapshot_time,event_time) >= ?::TIMESTAMPTZ'
            params.append(start)
            if end:
                restriction += ' AND COALESCE(snapshot_time,event_time) <= ?::TIMESTAMPTZ'
                params.append(end)
            sql = sql.replace('/* SNAPSHOT_RANGE */', restriction)
        elif end:
            sql = sql.replace('/* SNAPSHOT_RANGE */',' AND COALESCE(snapshot_time,event_time) <= ?::TIMESTAMPTZ')
            params.append(end)
    return sql + (_MAINTENANCE if snapshots else ''), params


def destination_read_model(paths: list[Any], filters: dict[str,str], *, grain: str, timezone_name: str, analytics_context: AnalyticsContext|None=None, identity_request=None) -> dict[str,Any]:
    with reader_context(paths,analytics_context,snapshot=True) as (conn,ids,generation):
        if conn is None or not ids:
            return dict(generation=generation,summary={},operation_type_counts=[],maintenance_status_counts=[],table_outcome=[],table_efficiency_points=[],status_by_date=[],reclaim_by_date=[],format_comparison=[],bytes_reclaimed_by_date=[])
        identity = _prepare_identity(paths, (conn,ids,generation), identity_request)
        version = identity.version
        # Apply filters and the signature join once; all page aggregates share
        # the same pinned typed TEMP scope.
        scope_sql, scope_params = _query(ids,filters)
        with timed_stage('destination_scope'):
            conn.execute('CREATE OR REPLACE TEMP TABLE _monitoring_destination_scope AS '+scope_sql+' SELECT * FROM destination_scope',scope_params)
        placeholders=','.join('?' for _ in ids)
        sql=(f'WITH all_log_versions AS (SELECT log_schema_version FROM {MONITORING_DATAFLOW_FACTS_TABLE} WHERE _source_id IN ({placeholders}) UNION ALL SELECT log_schema_version FROM {MONITORING_JOB_FACTS_TABLE} WHERE _source_id IN ({placeholders})), '
             'destination_scope AS (SELECT * FROM _monitoring_destination_scope)'
             + _ROLLUP)
        params=[*ids,*ids]
        # Charts consume effect aggregates only. Avoid computing/hydrating the
        # drawer's workload, layout and maintenance presentation on this path.
        registry_fields=('target','target_display','identity_version','destination_name','destination_connection_type',
          'destination_schema','destination_table','destination_full_table','destination_path','destination_format','run_count','succeeded','latest_status','duration_observed',
          'table_health','attention_reason',*_METRICS,*[f'{m}_observed' for m in _METRICS],
          *[f'paired_{m}_{suffix}' for m in ('rows','files','bytes') for suffix in (('written' if m=='rows' else 'added'),'runtime','runs')],
          *[f'paired_{m}{suffix}' for m in ('files_removed','bytes_removed') for suffix in ('','_runtime','_runs')])
        registry=rows(conn.execute(sql+' SELECT '+','.join(registry_fields)+' FROM destination_registry',params))
        summary=one(conn.execute(sql+f''' SELECT COUNT(*) AS operation_count,
          MIN(event_time) AS date_min,MAX(event_time) AS date_max,MAX(event_time) AS latest_log_at,
          MAX(event_time) AS latest_dataflow_log_at,
          COUNT(DISTINCT target) AS destination_count,
          COUNT(DISTINCT target) FILTER(WHERE physical_effect_eligible AND GREATEST(COALESCE(destination_rows_written,0),COALESCE(destination_rows_inserted,0),COALESCE(destination_rows_updated,0),COALESCE(destination_rows_deleted,0),COALESCE(destination_files_added,0),COALESCE(destination_files_removed,0),COALESCE(destination_bytes_added,0),COALESCE(destination_bytes_removed,0))>0) AS active_destinations,
          COUNT(DISTINCT target) FILTER(WHERE LOWER(COALESCE(destination_status,'')) IN ('failed','failure','error','cancelled','canceled')) AS destinations_with_failures,
          COUNT(DISTINCT target) FILTER(WHERE NOT identity_resolved) AS unresolved_destinations,
          COUNT(*) FILTER(WHERE normalized_status='succeeded') AS succeeded,
          COUNT(*) FILTER(WHERE normalized_status='failed') AS failed,
          COUNT(*) FILTER(WHERE normalized_status='skipped') AS skipped,
          {_metric_select()}, {_workload_select(include_average=True, include_files_bytes=True)} FROM destination_scope''',params))
        summary.update(one(conn.execute(_MAINTENANCE_COVERAGE_SQL)))
        maintenance_status_counts = rows(conn.execute(_MAINTENANCE_STATUS_SQL))
        # Classify each destination once from the same latest filtered identity
        # used by the registry. Keep the existing combined unresolved count.
        summary.update(one(conn.execute(sql+''' SELECT
          COUNT(*) FILTER(WHERE identity_status='resolved') AS resolved_destinations,
          COUNT(*) FILTER(WHERE identity_status='unresolved') AS unresolved_binding_destinations,
          COUNT(*) FILTER(WHERE identity_status='conflict') AS conflicting_destinations
          FROM destination_latest''',params)))
        operation_type_counts = rows(conn.execute(''' SELECT * FROM (SELECT
          COALESCE(NULLIF(LOWER(TRIM(operation_type)),''),'unknown') AS operation_type,
          COUNT(*) AS count
          FROM _monitoring_destination_scope GROUP BY 1) operation_counts
          ORDER BY CASE operation_type WHEN 'etl' THEN 0 WHEN 'maintenance' THEN 1
            WHEN 'replay' THEN 2 WHEN 'unknown' THEN 4 ELSE 3 END, operation_type'''))
        trend=rows(conn.execute(sql+''' SELECT date_trunc(?,timezone(?,event_time)) AS bucket_start,
          COUNT(*) AS total, COUNT(*) FILTER(WHERE normalized_status='succeeded') AS succeeded,
          COUNT(*) FILTER(WHERE normalized_status='failed') AS failed,COUNT(*) FILTER(WHERE normalized_status='skipped') AS skipped,
          COUNT(*) FILTER(WHERE normalized_status='running') AS running,COUNT(*) FILTER(WHERE normalized_status='pending') AS pending,
          COUNT(*) FILTER(WHERE normalized_status NOT IN ('succeeded','failed','skipped','running','pending')) AS unknown,
          COUNT(*) FILTER(WHERE LOWER(operation_type)='etl') AS etl,
          COUNT(*) FILTER(WHERE is_maintenance) AS maintenance,
          COUNT(*) FILTER(WHERE LOWER(operation_type)='replay') AS replay
          FROM destination_scope WHERE event_time IS NOT NULL GROUP BY bucket_start ORDER BY bucket_start''',[*params,grain,timezone_name]))
        changes=rows(conn.execute(sql+f''' SELECT date_trunc(?,timezone(?,event_time)) AS bucket_start,
          COUNT(*) AS runs,{_metric_select()} FROM destination_scope
          WHERE event_time IS NOT NULL GROUP BY bucket_start ORDER BY bucket_start''',[*params,grain,timezone_name]))
        identity.assert_current()
    for sequence in (trend,changes):
        for row in sequence:
            row.update(date=row['bucket_start'],bucket=row['bucket_start'],grain=grain,bucket_end=None)
    # Exact integer ranking before per-metric limits. Registry arrays are not
    # published wholesale; independent metric candidate sets remain bounded.
    chart_fields=('target','target_display','identity_version','destination_name','destination_connection_type',
      'destination_schema','destination_table','destination_full_table','destination_path','destination_format','run_count','succeeded','latest_status','duration_observed',
                  'table_health','attention_reason',*_METRICS,*[f'{m}_observed' for m in _METRICS],
                  *[f'paired_{m}_{suffix}' for m in ('rows','files','bytes') for suffix in (('written' if m=='rows' else 'added'),'runtime','runs')],
                  *[f'paired_{m}{suffix}' for m in ('files_removed','bytes_removed') for suffix in ('','_runtime','_runs')])
    def candidates(efficiency: bool) -> list[dict[str,Any]]:
        selected: dict[str,dict[str,Any]]={}
        for metric, effect in (('rows','written'),('files','added'),('bytes','added'),('files','removed'),('bytes','removed')):
            key=f"paired_{metric}_{effect}" if efficiency else f"{metric}_{effect}_observed"
            runtime_key=f"paired_{metric}_removed_runtime" if effect=='removed' else f"paired_{metric}_runtime"
            eligible=[r for r in registry if r.get(key) is not None and r[key]>=0 and ((r.get(runtime_key) or 0)>0 if efficiency else r[key]>0)]
            for row in sorted(eligible,key=lambda r:(-r[key],r['target']))[:800 if efficiency else 200]:
                selected[row['target']]={k:row.get(k) for k in chart_fields}
        return list(selected.values())
    summary['identity_version'] = version
    return dict(generation=generation,summary=summary,operation_type_counts=operation_type_counts,maintenance_status_counts=maintenance_status_counts,table_outcome=candidates(False),table_efficiency_points=candidates(True),
                status_by_date=trend,reclaim_by_date=changes,bytes_reclaimed_by_date=[],format_comparison=[])


def destination_evidence_read_model(paths: list[Any], filters: dict[str,str], *, limit:int,offset:int,sort_by:str,sort_dir:str,view:str='detail',analytics_context:AnalyticsContext|None=None,identity_request=None) -> dict[str,Any]:
    history=view=='destination-runs'
    sorts={key:key for key in ('target','target_display','contributing_dataflow_count','run_count','latest_time','latest_status','maintenance_run_count','attention_priority')}
    sorts.update({key:f'{key}_observed' for key in _METRICS})
    sorts.update({f'workload_rows_{name}':f'workload_rows_{name}_observed' for name in ('read','written')})
    sorts['latest_status']='latest_status'
    history_sorts={key:key for key in ('event_time','start_time','end_time','dataflow_name','status','operation_type','duration_seconds','source_name','message','destination_rows_written')}
    order=(history_sorts if history else sorts).get(sort_by,'event_time' if history else 'attention_priority')
    direction='ASC' if sort_dir=='asc' else 'DESC'
    with reader_context(paths,analytics_context,snapshot=True) as (conn,ids,generation):
        if conn is None or not ids:
            return dict(generation=generation,records=[],total_records=0)
        identity = _prepare_identity(paths, (conn,ids,generation), identity_request)
        version = identity.version
        sql,params=_query(ids,filters,snapshots=not history and view!='registry')
        if not history:
            # Maintenance evidence also repeats the admitted schema decision.
            # Read it once on this pinned snapshot and simplify all scalar
            # expressions before DuckDB plans the aggregate/evidence query.
            placeholders=','.join('?' for _ in ids)
            observed=conn.execute(f'''SELECT EXISTS(
              SELECT 1 FROM {MONITORING_DATAFLOW_FACTS_TABLE} WHERE _source_id IN ({placeholders}) AND log_schema_version>=7
              UNION ALL SELECT 1 FROM {MONITORING_JOB_FACTS_TABLE} WHERE _source_id IN ({placeholders}) AND log_schema_version>=7)''',[*ids,*ids]).fetchone()[0]
            sql=re.sub(r'EXISTS\s*\(SELECT 1 FROM all_log_versions WHERE log_schema_version\s*>=\s*7\)',
                       'TRUE' if observed else 'FALSE',sql)
            # Reuse the admitted/filter/identity join for sorting, layout flags
            # and evidence aggregates rather than scanning it for each stage.
            scope_sql,remaining=sql.split(', destination_latest AS (',1)
            _,scope_params=_query(ids,filters)
            with timed_stage('destination_evidence_scope'):
                conn.execute('CREATE OR REPLACE TEMP TABLE _monitoring_destination_evidence_scope AS '+scope_sql+' SELECT * FROM destination_scope',params[:len(scope_params)])
            sql='WITH destination_scope AS (SELECT * FROM _monitoring_destination_evidence_scope), destination_latest AS ('+remaining
            params=params[len(scope_params):]
            layout_context=prepare_layout_flags(conn,sql,[],identity,_LATEST_RUN_ORDER)
            sql+=signals_sql()
        if history:
            query='SELECT * EXCLUDE(job_dry_run,matching_run_reports,operation_details,destination_configure) FROM destination_scope'
            stable='dataflow_run_id ASC, _source_id ASC, _file_uri ASC, _record_line ASC'
        else:
            if view=='registry':
                fields=('target','target_display','identity_version','identity_status','identity_reason','identity_provenance','identity_metadata_sources',
                    'destination_id','destination_name','destination_connection_type','destination_format','destination_catalog','destination_database','destination_schema','destination_table','destination_full_table','destination_path','destination_load_type',
                    'workspace_id','dataflow_id','dataflow_name','latest_source_id','latest_file_uri','latest_record_line','dataflow_run_id','job_id','log_session_id',
                    'run_count','succeeded','failed','skipped','running','pending','unknown','contributing_dataflow_count','latest_time','latest_status','status','root_operation_type','destination_operation_type','destination_status',
                    'maintenance_run_count','maintenance_succeeded','maintenance_failed','maintenance_skipped','maintenance_running','maintenance_pending','maintenance_unknown','latest_maintenance_time','latest_successful_maintenance_time','latest_maintenance_outcome','maintenance_applicability','maintenance_capability','maintenance_evidence_state',
                    'format_variants','load_type_variants','format_unknown_runs','table_health','attention_reason','attention_priority','signal_affected_count','destination_signals','destination_failed_runs','destination_failure_only_runs','destination_cancelled_runs','run_failed_destination_unknown_runs','upstream_failed_runs','unqualified_effect_runs','matching_run_reports',
                    *[f'{base}{suffix}' for base in ('workload_rows_read','workload_rows_written') for suffix in ('','_observed','_measured_runs','_eligible_runs')],
                    *[f'{base}{suffix}' for base in _METRICS for suffix in ('','_observed','_measured_runs')])
                query='SELECT '+','.join(fields)+' FROM destination_registry_with_signals'
            else:
                query='''SELECT r.*,m.* EXCLUDE(target),legacy.observation_time AS snapshot_time,
              legacy.snapshot_version,legacy.snapshot_rows,legacy.snapshot_files,legacy.snapshot_bytes,legacy.snapshot_conflict,
              legacy._source_id AS snapshot_source_id,legacy.log_session_id AS snapshot_log_session_id,
              legacy.job_id AS snapshot_job_id,legacy.dataflow_run_id AS snapshot_dataflow_run_id,
              s.observation_time AS state_observed_at,s.snapshot_time IS NOT NULL AS state_has_timestamp,
              s.kind AS state_kind,s.snapshot_version AS state_revision,s.snapshot_conflict AS state_conflict,
              s.snapshot_rows AS state_rows,s.snapshot_files AS state_files,s.snapshot_bytes AS state_bytes,
              s.rows_meaning AS state_rows_meaning,s.files_meaning AS state_files_meaning,s.bytes_meaning AS state_bytes_meaning,
              s._source_id AS state_source_id,s.log_session_id AS state_log_session_id,s.job_id AS state_job_id,
              s.dataflow_run_id AS state_dataflow_run_id,s._file_uri AS state_file_uri,s._record_line AS state_record_line,s.entry_path AS state_entry_path,
              CASE WHEN r.latest_etl_write_time IS NOT NULL AND r.latest_successful_maintenance_time IS NOT NULL
              THEN GREATEST(0,epoch(r.latest_etl_write_time-r.latest_successful_maintenance_time)) END AS maintenance_lag_seconds
              FROM destination_registry_with_signals r LEFT JOIN snapshot_ranked s USING(target)
              LEFT JOIN legacy_snapshot_ranked legacy USING(target)
              LEFT JOIN destination_maintenance_rollup m USING(target)'''
            stable='target ASC'
            if order=='attention_priority':
                stable=f'signal_affected_count {direction},target ASC'
        records,total=paged_rows(conn.execute(sql+f' SELECT e.*,COUNT(*) OVER() AS __total_records FROM ({query}) e ORDER BY {order} {direction} NULLS LAST,{stable} LIMIT ? OFFSET ?',[*params,limit,offset]))
        if not records and offset:
            total=int(conn.execute(sql+f'SELECT COUNT(*) FROM {"destination_scope" if history else "destination_registry"}',params).fetchone()[0])
        if not history and records:
            # Layout lists are needed only for the bounded selected roots.
            # Keep them out of the full-population aggregate/window relation.
            selected=','.join('(?,?,?)' for _ in records)
            locators=[value for record in records for value in (record['latest_source_id'],record['latest_file_uri'],record['latest_record_line'])]
            partitions=rows(conn.execute(f'''WITH selected(_source_id,_file_uri,_record_line) AS (VALUES {selected})
              SELECT f._source_id,f._file_uri,f._record_line,f.destination_partition_columns
              FROM {MONITORING_DATAFLOW_FACTS_TABLE} f JOIN selected s USING(_source_id,_file_uri,_record_line)''',locators))
            indexed={(r['_source_id'],r['_file_uri'],r['_record_line']):r['destination_partition_columns'] for r in partitions}
            for record in records:
                record['destination_partition_columns']=indexed.get((record['latest_source_id'],record['latest_file_uri'],record['latest_record_line']))
            layout_context.enrich(records)
        identity.assert_current()
    for record in records:
        if not history and view != 'registry':
            attach_recorded_state(record)
        for field in ('identity_provenance','identity_metadata_sources'):
            if isinstance(record.get(field), str):
                record[field] = json.loads(record[field])
    return dict(generation=generation,identity_version=version,records=records,total_records=total)


def _prepare_identity(paths, analytics_context, identity_request):
    if identity_request is not None:
        identity_request.prepare()
        return identity_request
    # Direct domain callers still use the same published identity service.
    from datacoolie_studio.db.session import create_session
    from datacoolie_studio.domains.monitoring.identity_context import DestinationIdentityRequest
    environment_id = getattr(paths[0], 'environment_id', None) if paths else None
    if environment_id is None:
        raise ValueError('Destination identity requires an environment scope')
    with create_session() as session:
        request = DestinationIdentityRequest.capture(session, environment_id, analytics_context)
        request.prepare()
        request.assert_current()
        return request

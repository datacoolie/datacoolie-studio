"""Explicit captured object-state adapters. Never interpret changes as totals."""
from __future__ import annotations

from datacoolie_studio.domains.assets.format_policy import canonical_format_sql
from datacoolie_studio.domains.monitoring.read_models.maintenance import _LATEST_RUN_ORDER

MEANINGS = {
    'rows': ('live_rows', 'live_data_file_records_before_deletes'),
    'files': ('live_data_files', 'live_files', 'live_files_including_delete_files'),
    'bytes': ('storage_bytes', 'live_files_bytes_including_delete_files'),
}


def _integer(expression: str) -> str:
    # DuckDB integer casts round fractions; validate the token first.
    return f"CASE WHEN regexp_full_match({expression},'[0-9]+') THEN TRY_CAST({expression} AS HUGEINT) END"


def state_ctes() -> str:
    fields = []
    for unit in MEANINGS:
        iceberg_path = {'rows': 'total-records', 'files': 'total-data-files', 'bytes': 'total-files-size'}[unit]
        iceberg_meaning = {'rows': 'live_data_file_records_before_deletes', 'files': 'live_data_files', 'bytes': 'live_files_bytes_including_delete_files'}[unit]
        iceberg_value = _integer(f"json_extract_string(value,'$.summary.{iceberg_path}')")
        generic_value = _integer(f"json_extract_string(value,'$.measurements.{unit}.value')")
        allowed = ','.join(f"'{meaning}'" for meaning in MEANINGS[unit])
        valid_generic = f"kind='object_state' AND json_extract_string(value,'$.measurements.{unit}.unit')='{unit}' AND json_extract_string(value,'$.measurements.{unit}.meaning') IN ({allowed}) AND {generic_value} IS NOT NULL"
        delta_value = 'NULL' if unit == 'rows' else _integer(f"json_extract_string(value,'$.{'numFiles' if unit=='files' else 'sizeInBytes'}')")
        delta_meaning = 'NULL' if unit == 'rows' else f"'{'live_data_files' if unit=='files' else 'storage_bytes'}'"
        fields.extend((
            f"CASE WHEN kind='iceberg_snapshot' THEN {iceberg_value} WHEN kind='delta_table_detail' THEN {delta_value} WHEN {valid_generic} THEN {generic_value} END AS snapshot_{unit}",
            f"CASE WHEN kind='iceberg_snapshot' THEN '{iceberg_meaning}' WHEN kind='delta_table_detail' THEN {delta_meaning} WHEN {valid_generic} THEN json_extract_string(value,'$.measurements.{unit}.meaning') END AS {unit}_meaning",
        ))
    fmt = canonical_format_sql('destination_format')
    return f"""
    , state_entries AS (
      SELECT d.* EXCLUDE(destination_operation_details),e.value,
        CASE WHEN json_type(d.destination_operation_details)='OBJECT' THEN '$' ELSE CONCAT('$[',e.key,']') END AS entry_path
      FROM destination_scope d,
      json_each(CASE WHEN json_type(d.destination_operation_details)='ARRAY' THEN d.destination_operation_details
        WHEN json_type(d.destination_operation_details)='OBJECT' THEN json_array(d.destination_operation_details) ELSE '[]'::JSON END) e
      WHERE d.physical_effect_eligible
    ), state_documents AS (
      SELECT * FROM state_entries
      UNION ALL
      SELECT e.* EXCLUDE(value,entry_path),h.value,CONCAT(e.entry_path,'.data[',h.key,']') AS entry_path
      FROM state_entries e,json_each(CASE WHEN LOWER(json_extract_string(e.value,'$.operation'))='history'
        AND json_type(e.value,'$.data')='ARRAY' THEN json_extract(e.value,'$.data') ELSE '[]'::JSON END) h
    ), state_adapted AS (
      SELECT * EXCLUDE(value,entry_path),
        CASE WHEN LOWER(json_extract_string(value,'$.operation'))='table_detail' AND json_type(value,'$.data')='OBJECT'
          THEN json_extract(value,'$.data') ELSE value END AS value,
        CASE WHEN LOWER(json_extract_string(value,'$.operation'))='table_detail' AND json_type(value,'$.data')='OBJECT'
          THEN CONCAT(entry_path,'.data') ELSE entry_path END AS entry_path,
        CASE WHEN LOWER(json_extract_string(value,'$.operation'))='object_state' THEN 'object_state'
          WHEN {fmt}='delta' AND LOWER(json_extract_string(value,'$.operation'))='table_detail' THEN 'delta_table_detail'
          WHEN {fmt}='iceberg' AND json_type(value,'$.summary')='OBJECT'
            AND COALESCE(json_extract_string(value,'$.snapshot_id'),json_extract_string(value,'$.version')) IS NOT NULL THEN 'iceberg_snapshot' END AS kind,
        TRY_CAST(json_extract_string(value,'$.timestamp') AS TIMESTAMPTZ) AS wrapper_time,
        COALESCE(json_extract_string(value,'$.revision'),json_extract_string(value,'$.version')) AS wrapper_revision,
        json_extract_string(value,'$.timestamp') IS NOT NULL AND TRY_CAST(json_extract_string(value,'$.timestamp') AS TIMESTAMPTZ) IS NULL AS invalid_wrapper_time
      FROM state_documents
    ), snapshot_candidates AS (
      SELECT * EXCLUDE(value),
        COALESCE(TRY_CAST(json_extract_string(value,'$.timestamp') AS TIMESTAMPTZ),wrapper_time) AS snapshot_time,
        COALESCE(json_extract_string(value,'$.snapshot_id'),json_extract_string(value,'$.revision'),json_extract_string(value,'$.version'),wrapper_revision) AS snapshot_version,
        {','.join(fields)}
      FROM state_adapted WHERE kind IS NOT NULL AND NOT invalid_wrapper_time
        AND NOT (json_extract_string(value,'$.timestamp') IS NOT NULL AND TRY_CAST(json_extract_string(value,'$.timestamp') AS TIMESTAMPTZ) IS NULL)
    ), snapshot_valid AS (
      SELECT *,COALESCE(snapshot_time,event_time) AS observation_time
      FROM snapshot_candidates
      WHERE (snapshot_rows IS NOT NULL OR snapshot_files IS NOT NULL OR snapshot_bytes IS NOT NULL)
        AND (snapshot_time IS NULL OR snapshot_time<=COALESCE(end_time,event_time))
        /* SNAPSHOT_RANGE */
    ), snapshot_ranked AS (
      SELECT *,COUNT(DISTINCT CAST(json_array(kind,snapshot_version,snapshot_rows,snapshot_files,snapshot_bytes,rows_meaning,files_meaning,bytes_meaning) AS VARCHAR))
        OVER(PARTITION BY target,observation_time)>1 AS snapshot_conflict
      FROM snapshot_valid
      QUALIFY ROW_NUMBER() OVER(PARTITION BY target ORDER BY observation_time DESC NULLS LAST,{_LATEST_RUN_ORDER},entry_path DESC)=1
    ), legacy_snapshot_ranked AS (
      SELECT *,COUNT(DISTINCT CAST(json_array(snapshot_version,snapshot_rows,snapshot_files,snapshot_bytes) AS VARCHAR))
        OVER(PARTITION BY target,observation_time)>1 AS snapshot_conflict
      FROM snapshot_valid WHERE kind='iceberg_snapshot'
      QUALIFY ROW_NUMBER() OVER(PARTITION BY target ORDER BY observation_time DESC NULLS LAST,{_LATEST_RUN_ORDER},entry_path DESC)=1
    )
    """


def attach_recorded_state(record: dict) -> None:
    """Compose only bounded SQL-selected scalar evidence, never native log JSON."""
    measurements = {}
    for unit in MEANINGS:
        value = record.get(f'state_{unit}')
        meaning = record.get(f'state_{unit}_meaning')
        if value is not None and meaning:
            measurements[unit] = dict(value=str(value), unit=unit, meaning=meaning)
    record['recorded_state'] = dict(
        availability='recorded' if len(measurements)==3 else 'partial' if measurements else 'not_recorded',
        observed_at=record.get('state_observed_at'), revision=record.get('state_revision'),
        kind=record.get('state_kind'), origin='captured' if measurements else 'unknown',
        timestamp_origin='observation' if record.get('state_has_timestamp') else 'run' if measurements else 'unknown',
        conflict=bool(record.get('state_conflict')), measurements=measurements,
        provenance={name: record.get(f'state_{name}') for name in ('source_id','log_session_id','job_id','dataflow_run_id','file_uri','record_line','entry_path')},
    )
    for key in list(record):
        if key.startswith('state_'):
            record.pop(key)

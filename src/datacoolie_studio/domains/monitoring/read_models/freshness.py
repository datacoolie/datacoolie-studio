from __future__ import annotations

from datetime import timezone

from datacoolie_studio.domains.monitoring.dimensions import known_dimension_sql

from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.domains.monitoring.metrics.v6_semantics import register_freshness_functions
from datacoolie_studio.domains.monitoring.read_models.common import (
    AnalyticsContext,
    discrete_percentile,
    filtered_ctes,
    one,
    paged_rows,
    reader_context,
    standalone_derived_query,
)


_DATAFLOW_COLUMNS = (
    "_source_id", "_file_uri", "_record_line", "raw_record_json", "log_schema_version", "attempts", "read_request",
    "log_session_id", "job_id", "dataflow_id", "dataflow_run_id", "workspace_id",
    "dataflow_name", "dataflow_description", "stage", "group_number",
    "execution_order", "processing_mode", "is_active", "configure",
    "status", "message", "error_message", "start_time", "end_time", "operation_type",
    "source_name", "source_id", "source_format", "source_connection_type",
    "source_catalog", "source_database", "source_schema", "source_table",
    "source_full_table", "source_path", "source_query", "source_python_function",
    "source_watermark_columns", "source_filter_expression", "source_configure",
    "datacoolie_version",
    "source_action", "transform_deduplicate_columns", "transform_latest_data_columns",
    "transform_filter_expression", "transform_additional_columns",
    "transform_schema_hints", "transform_select_columns", "transform_drop_columns",
    "transform_rename_columns", "transform_value_rules", "transform_hash_columns",
    "transform_masking_rules", "transform_configure", "destination_name",
    "destination_id", "destination_format", "destination_connection_type",
    "destination_catalog", "destination_database", "destination_schema",
    "destination_table", "destination_full_table", "destination_path",
    "destination_load_type", "destination_merge_keys", "destination_partition_columns",
    "destination_configure",
)
_JOB_COLUMNS = ("_source_id", "job_id")


def freshness_read_model(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    *,
    grain: str,
    timezone_name: str,
    analytics_context: AnalyticsContext | None = None,
) -> dict[str, Any]:
    with reader_context(paths, analytics_context) as (conn, source_ids, generation):
        if conn is None or not source_ids:
            return _empty(generation)
        register_freshness_functions(conn)
        ctes, params = filtered_ctes(
            source_ids, filters,
            dataflow_columns=_DATAFLOW_COLUMNS,
            job_columns=_JOB_COLUMNS,
        )
        freshness_ctes = f"{ctes} {_DERIVED_CTES}"
        bundle = one(conn.execute(
            f"{freshness_ctes} {_BUNDLE_SQL}",
            [*params, grain, timezone_name],
        ))
    return {
        "generation": generation, "summary": bundle.get("summary") or {},
        "age_by_dataflow": bundle.get("age_by_dataflow") or [],
        "watermark_movement_by_date": [
            _trend_row(row, grain) for row in bundle.get("movement_trend") or []
        ],
        "age_distribution": bundle.get("age_distribution") or [],
        "watermark_coverage_by_stage": bundle.get("stage_coverage") or [],
        "skipped_streak_distribution": bundle.get("streak_distribution") or [],
        "read_boundary_adjustment_by_dataflow": bundle.get("read_boundary_adjustment") or [],
    }


def freshness_evidence_read_model(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    *,
    limit: int,
    offset: int,
    sort_by: str,
    sort_dir: str,
    view: str = "detail",
    analytics_context: AnalyticsContext | None = None,
) -> dict[str, Any]:
    if view not in {"detail", "registry"}:
        raise ValueError(f"Unsupported Freshness evidence view: {view}")
    sort_columns = {
        "dataflow_name": "dataflow_name", "stage": "stage",
        "source_name": "source_name", "destination_name": "destination_name",
        "destination_load_type": "destination_load_type",
        "latest_freshness_at": "latest_freshness_at", "age_days": "age_days",
        "latest_run_status": "latest_run_status", "movement_state": "movement_state",
        "latest_success_watermark": "latest_success_watermark",
    }
    order_column = sort_columns.get(sort_by, "latest_freshness_at")
    direction = "ASC" if sort_dir == "asc" else "DESC"
    with reader_context(paths, analytics_context) as (conn, source_ids, generation):
        if conn is None or not source_ids:
            return {"generation": generation, "records": [], "total_records": 0}
        register_freshness_functions(conn)
        ctes, params = filtered_ctes(
            source_ids, filters,
            dataflow_columns=_DATAFLOW_COLUMNS,
            job_columns=_JOB_COLUMNS,
        )
        freshness_ctes = f"{ctes} {_DERIVED_CTES}"
        registry_sql = _REGISTRY_SQL if view == "detail" else _REGISTRY_TABLE_SQL
        records, total = paged_rows(conn.execute(
            f"SELECT evidence.*, COUNT(*) OVER () AS __total_records FROM ("
            f"{freshness_ctes} {registry_sql}) evidence "
            f"ORDER BY {order_column} {direction} NULLS LAST, "
            "_source_id ASC NULLS LAST, log_session_id ASC NULLS LAST, "
            "dataflow_id ASC NULLS LAST, latest_run_id ASC NULLS LAST "
            "LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ))
        if not records and offset:
            total = int(conn.execute(f"SELECT COUNT(*) FROM ({freshness_ctes} {registry_sql}) evidence", params).fetchone()[0])
    return {"generation": generation, "records": records, "total_records": total}


def freshness_attention_sql() -> str:
    # Keep the overview attention subquery free of window functions. DuckDB
    # rejects the full ranked/latest registry when it is inlined into a
    # scalar struct subquery, while attention only needs one aggregate per
    # source/dataflow (sessions are consolidated in the registry).
    return standalone_derived_query(_ATTENTION_DERIVED_CTES, _ATTENTION_SQL)


def _empty(generation: str) -> dict[str, Any]:
    return {
        "generation": generation, "summary": {}, "age_by_dataflow": [],
        "watermark_movement_by_date": [], "age_distribution": [],
        "watermark_coverage_by_stage": [], "skipped_streak_distribution": [],
        "read_boundary_adjustment_by_dataflow": [],
    }


def _trend_row(row: dict[str, Any], grain: str) -> dict[str, Any]:
    return {
        "date": row.get("bucket_start"), "bucket": row.get("bucket_start"),
        "bucket_start": row.get("bucket_start"), "bucket_end": None, "grain": grain,
        **{key: value for key, value in row.items() if key != "bucket_start"},
    }


# Select only the native final-attempt members needed by freshness.  Dynamic
# watermark values remain JSON leaves and are cast to VARCHAR only for the
# Decimal-aware comparison UDF below.
_LATEST_RUN_ORDER = (
    "event_time DESC NULLS LAST, dataflow_run_id DESC NULLS LAST, "
    "_source_id DESC NULLS LAST, log_session_id DESC NULLS LAST, job_id DESC NULLS LAST, "
    "normalized_status DESC NULLS LAST, COALESCE(message, error_message) DESC NULLS LAST, "
    "error_message DESC NULLS LAST, _file_uri DESC NULLS LAST, _record_line DESC NULLS LAST"
)
_DATAFLOW_REGISTRY_RUN_ORDER = (
    "event_time DESC NULLS LAST, TRY_CAST(observed_at AS TIMESTAMPTZ) DESC NULLS LAST, "
    "dataflow_run_id ASC NULLS LAST, _source_id ASC NULLS LAST, _file_uri ASC NULLS LAST, "
    "_record_line ASC NULLS LAST, log_session_id ASC NULLS LAST"
)
_V6_EMPTY_EVIDENCE = """to_json(struct_pack(
  operation_type := operation_type,
  status := normalized_status,
  attempt_present := len(fd.attempts) > 0,
  attempt_status := fd.attempts[-1].status,
  source_status := fd.attempts[-1].source.status,
  source_rows_read := fd.attempts[-1].source.rows_read,
  selection := fd.attempts[-1].source.selection,
  read_request := fd.read_request,
  watermark := fd.attempts[-1].watermark,
  source_watermark_columns := source_watermark_columns,
  source_configure := source_configure,
  source_format := source_format,
  source_connection_type := source_connection_type
))"""
_V6_EVIDENCE_CTE = f"""
, v6_evidence AS (
  SELECT fd.*,
         fd.read_request AS v6_read_request,
         fd.attempts[-1].source.selection AS v6_selection,
         {_V6_EMPTY_EVIDENCE} AS v6_empty_evidence,
         NULLIF(CAST(fd.attempts[-1].watermark.loaded_watermark AS VARCHAR), 'null') AS v6_loaded_watermark,
         CAST(fd.attempts[-1].watermark.load_status AS VARCHAR) AS v6_load_status,
         NULLIF(CAST(fd.attempts[-1].watermark.merge_base AS VARCHAR), 'null') AS v6_merge_base,
         NULLIF(CAST(fd.attempts[-1].watermark.candidate.values AS VARCHAR), 'null') AS v6_candidate,
         CAST(fd.attempts[-1].watermark.candidate.kind AS VARCHAR) AS v6_candidate_kind,
         NULLIF(CAST(fd.attempts[-1].watermark.watermark_to_save AS VARCHAR), 'null') AS v6_watermark_to_save,
         CAST(fd.attempts[-1].watermark.save_status AS VARCHAR) AS v6_save_status,
         NULLIF(CAST(fd.attempts[-1].watermark.saved_watermark AS VARCHAR), 'null') AS v6_saved_watermark,
         CAST(fd.attempts[-1].source.status AS VARCHAR) AS v6_source_status,
         fd.attempts[-1].watermark IS NOT NULL AS v6_watermark_present,
         (CAST(fd.log_schema_version AS VARCHAR) IN ('6', '7')
          AND fd.attempts[-1].watermark IS NOT NULL) AS v6_merge_base_present,
         CAST(fd.attempts[-1].watermark.saved_watermark AS VARCHAR) IS NOT NULL AS v6_saved_watermark_present,
         fd.attempts[-1].watermark.candidate IS NOT NULL AS v6_candidate_present,
         len(fd.attempts) > 0 AS v6_attempt_present,
         studio_v6_request_has_watermark_configuration(
           NULLIF(CAST(fd.read_request.watermark_start AS VARCHAR), 'null'),
           NULLIF(CAST(fd.read_request.watermark_end AS VARCHAR), 'null'),
           fd.read_request.watermark_start_operator,
           fd.read_request.watermark_end_operator) AS v6_request_configured
  FROM filtered_dataflows fd
)
"""

_V6_CLASSIFICATION_CTES = """
, v6_classification_payload AS (
  SELECT *, studio_v6_freshness_classification(v6_empty_evidence) AS v6_classification
  FROM v6_evidence
), v6_classified_evidence AS (
  SELECT *,
         CAST(json_extract_string(v6_classification, '$.request_configured') AS BOOLEAN)
           AS v6_class_request_configured,
         CAST(json_extract_string(v6_classification, '$.watermark_enabled') AS BOOLEAN)
           AS v6_class_watermark_enabled,
         json_extract_string(v6_classification, '$.persisted_movement_state')
           AS v6_class_persisted_movement_state,
         json_extract_string(v6_classification, '$.persistence_state')
           AS v6_class_persistence_state,
         json_extract_string(v6_classification, '$.movement_state')
           AS v6_class_movement_state,
         json_extract_string(v6_classification, '$.watermark_issue_state')
           AS v6_class_watermark_issue_state,
         json_extract_string(v6_classification, '$.adjustment_state')
           AS v6_class_adjustment_state,
         json_extract_string(v6_classification, '$.adjustment_basis')
           AS v6_class_adjustment_basis,
         json_extract_string(v6_classification, '$.adjustment_reason')
           AS v6_class_adjustment_reason,
         json_extract_string(v6_classification, '$.adjustment_input_json')
           AS v6_class_adjustment_input_json,
         json_extract_string(v6_classification, '$.adjustment_effective_json')
           AS v6_class_adjustment_effective_json
  FROM v6_classification_payload
)
"""
_V6_REGISTRY_EMPTY_EVIDENCE = _V6_EMPTY_EVIDENCE.replace(
    "  watermark := fd.attempts[-1].watermark,\n",
    """  watermark := fd.attempts[-1].watermark,
  watermark_present := fd.attempts[-1].watermark IS NOT NULL,
  merge_base_present := (CAST(fd.log_schema_version AS VARCHAR) IN ('6', '7')
                         AND fd.attempts[-1].watermark IS NOT NULL),
  saved_watermark_present := CAST(fd.attempts[-1].watermark.saved_watermark AS VARCHAR) IS NOT NULL,
  candidate_present := fd.attempts[-1].watermark.candidate IS NOT NULL,
""",
)
_V6_REGISTRY_EVIDENCE_CTE = _V6_EVIDENCE_CTE.replace(
    _V6_EMPTY_EVIDENCE, _V6_REGISTRY_EMPTY_EVIDENCE,
)

_V6_HAS_WATERMARK = """studio_v6_watermark_is_configured(
  v6_request_configured, CAST(source_watermark_columns AS VARCHAR),
  v6_watermark_present, v6_load_status, v6_save_status,
  CAST(v6_candidate AS VARCHAR), CAST(v6_loaded_watermark AS VARCHAR),
  CAST(v6_watermark_to_save AS VARCHAR), CAST(v6_saved_watermark AS VARCHAR),
  CAST(v6_merge_base AS VARCHAR))"""
_IS_V6 = "(CAST(log_schema_version AS VARCHAR) IN ('6', '7'))"
_V6_MOVEMENT = f"""studio_v6_persisted_movement_with_presence_kind_and_candidate(
  CAST(v6_merge_base AS VARCHAR), CAST(v6_saved_watermark AS VARCHAR),
  v6_save_status, {_V6_HAS_WATERMARK}, v6_merge_base_present,
  v6_candidate_kind, CAST(v6_candidate AS VARCHAR))"""
_V6_PERSISTENCE = """studio_v6_persistence_state(
  v6_save_status, v6_watermark_present, CAST(v6_saved_watermark AS VARCHAR),
  v6_candidate_present, CAST(v6_candidate AS VARCHAR), v6_attempt_present,
  normalized_status)"""
_V6_CLASSIFIED_MOVEMENT = """studio_v6_watermark_movement(
  persisted_movement_state, v6_empty_evidence,
  watermark_enabled, persistence_state)"""
# Every filtered ETL run participates, including runs without watermark configuration.
_WATERMARK_RATE_DENOMINATOR = "COUNT(*)"
_WATERMARK_ADVANCED_RATE = (
    "ROUND(100.0 * COUNT(*) FILTER (WHERE movement_state = 'advanced') "
    f"/ NULLIF({_WATERMARK_RATE_DENOMINATOR}, 0), 2)"
)
_CHECK_ELIGIBLE = """(normalized_status IN ('succeeded', 'skipped') AND
  v6_attempt_present AND v6_source_status IN ('succeeded', 'skipped'))"""

_DERIVED_CTES = f"""
{_V6_EVIDENCE_CTE}
, etl_rows AS (
  SELECT *, {_V6_HAS_WATERMARK} AS watermark_enabled,
         {_V6_MOVEMENT} AS persisted_movement_state,
         CASE WHEN v6_merge_base_present AND v6_merge_base IS NULL THEN 'merge_base_null'
              WHEN v6_merge_base_present THEN 'merge_base'
              ELSE 'missing_merge_base' END AS comparison_basis,
         {_V6_PERSISTENCE} AS persistence_state,
         CASE WHEN v6_selection IS NOT NULL THEN 'selected'
              WHEN v6_attempt_present THEN 'not_selected' ELSE 'unknown' END AS selection_state,
         CASE WHEN {_V6_HAS_WATERMARK} THEN 'configured' ELSE 'not_configured' END AS configuration_state
  FROM v6_evidence
  WHERE LOWER(COALESCE(operation_type, 'unknown')) = 'etl'
), classified_etl_rows AS (
  SELECT *, {_V6_CLASSIFIED_MOVEMENT} AS movement_state,
         studio_v6_read_boundary_adjustment(v6_empty_evidence) AS read_boundary_adjustment_json
  FROM etl_rows
), adjustment_etl_rows AS (
  SELECT *,
         json_extract_string(read_boundary_adjustment_json, '$.adjustment_state') AS read_boundary_adjustment_state,
         json_extract_string(read_boundary_adjustment_json, '$.adjustment_basis') AS read_boundary_adjustment_basis,
         json_extract_string(read_boundary_adjustment_json, '$.adjustment_reason') AS read_boundary_adjustment_reason,
         json_extract_string(read_boundary_adjustment_json, '$.adjustment_input_json') AS read_boundary_adjustment_input_json,
         json_extract_string(read_boundary_adjustment_json, '$.adjustment_effective_json') AS read_boundary_adjustment_effective_json
  FROM classified_etl_rows
), issue_etl_rows AS (
  SELECT *, studio_v6_watermark_issue_state(
           watermark_enabled, movement_state, persistence_state,
           v6_load_status, v6_save_status) AS watermark_issue_state
  FROM adjustment_etl_rows
), ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY _source_id, dataflow_id ORDER BY {_LATEST_RUN_ORDER}) AS latest_rank,
         ROW_NUMBER() OVER (PARTITION BY _source_id, dataflow_id ORDER BY {_LATEST_RUN_ORDER}) AS run_rank,
         SUM(CASE WHEN normalized_status = 'skipped' THEN 0 ELSE 1 END) OVER (
           PARTITION BY _source_id, dataflow_id
           ORDER BY {_LATEST_RUN_ORDER}
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
         ) AS non_skipped_seen
  FROM issue_etl_rows
  WHERE dataflow_id IS NOT NULL
), per_dataflow AS (
  SELECT _source_id, dataflow_id,
         ARG_MAX(NULLIF(workspace_id, ''), event_time) AS workspace_id,
         ARG_MAX(NULLIF(dataflow_name, ''), event_time) AS dataflow_name,
         ARG_MAX(NULLIF(dataflow_description, ''), event_time) AS dataflow_description,
         ARG_MAX(NULLIF(stage, ''), event_time) AS stage,
         ARG_MAX(group_number, event_time) AS group_number,
         ARG_MAX(execution_order, event_time) AS execution_order,
         ARG_MAX(NULLIF(processing_mode, ''), event_time) AS processing_mode,
         ARG_MAX(is_active, event_time) AS is_active,
         ARG_MAX(NULLIF(configure, ''), event_time) AS configure,
         ARG_MAX(NULLIF(operation_type, ''), event_time) AS operation_type,
         ARG_MAX(NULLIF(source_name, ''), event_time) AS source_name,
         ARG_MAX(NULLIF(destination_name, ''), event_time) AS destination_name,
         ARG_MAX(NULLIF(source_format, ''), event_time) AS source_format,
         ARG_MAX(NULLIF(destination_format, ''), event_time) AS destination_format,
         ARG_MAX(NULLIF(source_connection_type, ''), event_time) AS source_connection_type,
         ARG_MAX(NULLIF(destination_connection_type, ''), event_time) AS destination_connection_type,
         ARG_MAX(NULLIF(source_id, ''), event_time) AS source_id,
         ARG_MAX(NULLIF(source_catalog, ''), event_time) AS source_catalog,
         ARG_MAX(NULLIF(source_database, ''), event_time) AS source_database,
         ARG_MAX(NULLIF(source_schema, ''), event_time) AS source_schema,
         ARG_MAX(NULLIF(source_table, ''), event_time) AS source_table,
         ARG_MAX(NULLIF(source_full_table, ''), event_time) AS source_full_table,
         ARG_MAX(NULLIF(source_path, ''), event_time) AS source_path,
         ARG_MAX(NULLIF(source_query, ''), event_time) AS source_query,
         ARG_MAX(NULLIF(source_python_function, ''), event_time) AS source_python_function,
         ARG_MAX(NULLIF(source_watermark_columns, ''), event_time) AS source_watermark_columns,
         ARG_MAX_NULL(datacoolie_version, event_time) AS datacoolie_version,
         ARG_MAX(NULLIF(source_filter_expression, ''), event_time) AS source_filter_expression,
         ARG_MAX(NULLIF(source_configure, ''), event_time) AS source_configure,
         ARG_MAX(NULLIF(source_action, ''), event_time) AS source_action,
         ARG_MAX(NULLIF(transform_deduplicate_columns, ''), event_time) AS transform_deduplicate_columns,
         ARG_MAX(NULLIF(transform_latest_data_columns, ''), event_time) AS transform_latest_data_columns,
         ARG_MAX(NULLIF(transform_filter_expression, ''), event_time) AS transform_filter_expression,
         ARG_MAX(NULLIF(transform_additional_columns, ''), event_time) AS transform_additional_columns,
         ARG_MAX(NULLIF(transform_schema_hints, ''), event_time) AS transform_schema_hints,
         ARG_MAX(NULLIF(transform_select_columns, ''), event_time) AS transform_select_columns,
         ARG_MAX(NULLIF(transform_drop_columns, ''), event_time) AS transform_drop_columns,
         ARG_MAX(NULLIF(transform_rename_columns, ''), event_time) AS transform_rename_columns,
         ARG_MAX(NULLIF(transform_value_rules, ''), event_time) AS transform_value_rules,
         ARG_MAX(NULLIF(transform_hash_columns, ''), event_time) AS transform_hash_columns,
         ARG_MAX(NULLIF(transform_masking_rules, ''), event_time) AS transform_masking_rules,
         ARG_MAX(NULLIF(transform_configure, ''), event_time) AS transform_configure,
         ARG_MAX(NULLIF(destination_id, ''), event_time) AS destination_id,
         ARG_MAX(NULLIF(destination_catalog, ''), event_time) AS destination_catalog,
         ARG_MAX(NULLIF(destination_database, ''), event_time) AS destination_database,
         ARG_MAX(NULLIF(destination_schema, ''), event_time) AS destination_schema,
         ARG_MAX(NULLIF(destination_table, ''), event_time) AS destination_table,
         ARG_MAX(NULLIF(destination_full_table, ''), event_time) AS destination_full_table,
         ARG_MAX(NULLIF(destination_path, ''), event_time) AS destination_path,
         ARG_MAX(NULLIF(destination_load_type, ''), event_time) AS destination_load_type,
         ARG_MAX(NULLIF(destination_merge_keys, ''), event_time) AS destination_merge_keys,
         ARG_MAX(NULLIF(destination_partition_columns, ''), event_time) AS destination_partition_columns,
         ARG_MAX(NULLIF(destination_configure, ''), event_time) AS destination_configure,
         FIRST(log_session_id ORDER BY {_LATEST_RUN_ORDER}) AS log_session_id,
         FIRST(normalized_status ORDER BY {_LATEST_RUN_ORDER}) AS latest_status,
         FIRST(event_time ORDER BY {_LATEST_RUN_ORDER}) AS latest_run_at,
         FIRST(dataflow_run_id ORDER BY {_LATEST_RUN_ORDER}) AS latest_run_id,
         FIRST(COALESCE(message, error_message) ORDER BY {_LATEST_RUN_ORDER}) AS latest_message,
         FIRST(error_message ORDER BY {_LATEST_RUN_ORDER}) AS latest_error_message,
         FIRST(movement_state ORDER BY {_LATEST_RUN_ORDER}) AS latest_movement_state,
         FIRST(watermark_issue_state ORDER BY {_LATEST_RUN_ORDER}) AS latest_watermark_issue_state,
         FIRST(comparison_basis ORDER BY {_LATEST_RUN_ORDER}) AS latest_comparison_basis,
         FIRST(persistence_state ORDER BY {_LATEST_RUN_ORDER}) AS latest_persistence_state,
         FIRST(configuration_state ORDER BY {_LATEST_RUN_ORDER}) AS latest_configuration_state,
         FIRST(selection_state ORDER BY {_LATEST_RUN_ORDER}) AS latest_selection_state,
         FIRST(read_boundary_adjustment_state ORDER BY {_LATEST_RUN_ORDER}) AS latest_read_boundary_adjustment_state,
         FIRST(read_boundary_adjustment_basis ORDER BY {_LATEST_RUN_ORDER}) AS latest_read_boundary_adjustment_basis,
         FIRST(read_boundary_adjustment_reason ORDER BY {_LATEST_RUN_ORDER}) AS latest_read_boundary_adjustment_reason,
         FIRST(read_boundary_adjustment_input_json ORDER BY {_LATEST_RUN_ORDER}) AS latest_read_boundary_adjustment_input_json,
         FIRST(read_boundary_adjustment_effective_json ORDER BY {_LATEST_RUN_ORDER}) AS latest_read_boundary_adjustment_effective_json,
         COUNT(*) FILTER (WHERE read_boundary_adjustment_state = 'adjusted') AS adjustment_adjusted_runs,
         COUNT(*) FILTER (WHERE read_boundary_adjustment_state = 'unchanged') AS adjustment_unchanged_runs,
         COUNT(*) FILTER (WHERE read_boundary_adjustment_state = 'unknown') AS adjustment_unknown_runs,
         COUNT(*) FILTER (WHERE read_boundary_adjustment_state = 'not_applied') AS adjustment_not_applied_runs,
         COUNT(*) FILTER (WHERE read_boundary_adjustment_state IN ('adjusted', 'unchanged')) AS adjustment_comparable_runs,
         COUNT(*) AS adjustment_total_runs,
         FIRST(event_time ORDER BY {_LATEST_RUN_ORDER}) FILTER (WHERE watermark_enabled) AS watermark_time,
         FIRST(event_time ORDER BY {_LATEST_RUN_ORDER}) FILTER (WHERE {_CHECK_ELIGIBLE}) AS latest_freshness_at,
         FIRST(normalized_status ORDER BY {_LATEST_RUN_ORDER})
           FILTER (WHERE {_CHECK_ELIGIBLE}) AS latest_freshness_status,
         MAX(event_time) FILTER (WHERE normalized_status = 'skipped') AS latest_skipped_at,
         FIRST(CAST(v6_saved_watermark AS VARCHAR) ORDER BY {_LATEST_RUN_ORDER})
           FILTER (WHERE v6_save_status = 'saved') AS latest_success_watermark,
         FIRST(event_time ORDER BY {_LATEST_RUN_ORDER})
           FILTER (WHERE v6_save_status = 'saved') AS latest_success_watermark_at,
         FIRST(dataflow_run_id ORDER BY {_LATEST_RUN_ORDER})
           FILTER (WHERE v6_save_status = 'saved') AS latest_success_watermark_run_id,
         FIRST(_source_id ORDER BY {_LATEST_RUN_ORDER})
           FILTER (WHERE v6_save_status = 'saved') AS latest_success_watermark_source_id,
         LIST(STRUCT_PACK(
           status := normalized_status,
           "time" := event_time,
           dataflow_run_id := dataflow_run_id
         ) ORDER BY {_LATEST_RUN_ORDER})
           FILTER (WHERE run_rank <= 5) AS last_statuses,
         BOOL_OR(watermark_enabled) AS watermark_enabled,
         COUNT(*) AS run_count,
         COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded_runs,
         COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed_runs,
         COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped_runs,
         COUNT(*) FILTER (WHERE normalized_status = 'running') AS running_runs,
         COUNT(*) FILTER (WHERE normalized_status = 'pending') AS pending_runs,
         COUNT(*) FILTER (WHERE normalized_status = 'skipped' AND non_skipped_seen = 0) AS skipped_streak,
         FIRST(v6_read_request ORDER BY {_LATEST_RUN_ORDER}) AS read_request,
         FIRST(v6_selection ORDER BY {_LATEST_RUN_ORDER}) AS selection,
         FIRST(v6_selection.upper_origin ORDER BY {_LATEST_RUN_ORDER}) AS selection_upper_origin,
         FIRST(CAST(v6_loaded_watermark AS VARCHAR) ORDER BY {_LATEST_RUN_ORDER}) AS loaded_watermark,
         FIRST(v6_load_status ORDER BY {_LATEST_RUN_ORDER}) AS load_status,
         FIRST(CAST(v6_merge_base AS VARCHAR) ORDER BY {_LATEST_RUN_ORDER}) AS merge_base,
         FIRST(v6_merge_base_present ORDER BY {_LATEST_RUN_ORDER}) AS merge_base_present,
         FIRST(CAST(v6_candidate AS VARCHAR) ORDER BY {_LATEST_RUN_ORDER}) AS candidate,
         FIRST(v6_candidate_present ORDER BY {_LATEST_RUN_ORDER}) AS candidate_present,
         FIRST(v6_candidate_kind ORDER BY {_LATEST_RUN_ORDER}) AS candidate_kind,
         FIRST(CAST(v6_watermark_to_save AS VARCHAR) ORDER BY {_LATEST_RUN_ORDER}) AS watermark_to_save,
         FIRST(v6_save_status ORDER BY {_LATEST_RUN_ORDER}) AS save_status,
         FIRST(CAST(v6_saved_watermark AS VARCHAR) ORDER BY {_LATEST_RUN_ORDER}) AS saved_watermark,
         FIRST(v6_saved_watermark_present ORDER BY {_LATEST_RUN_ORDER}) AS saved_watermark_present,
         FIRST(_source_id ORDER BY {_LATEST_RUN_ORDER}) AS latest_source_id,
         FIRST(_file_uri ORDER BY {_LATEST_RUN_ORDER}) AS latest_file_uri,
         FIRST(_file_kind ORDER BY {_LATEST_RUN_ORDER}) AS latest_file_kind,
         FIRST(_record_line ORDER BY {_LATEST_RUN_ORDER}) AS latest_record_line,
         NULL::VARCHAR AS raw_record_json
  FROM ranked GROUP BY _source_id, dataflow_id
), registry AS (
  SELECT *,
         CASE WHEN latest_freshness_at IS NULL THEN NULL ELSE GREATEST(0, epoch(current_timestamp - latest_freshness_at)) END AS age_seconds,
         CASE WHEN latest_freshness_at IS NULL THEN NULL ELSE GREATEST(0, epoch(current_timestamp - latest_freshness_at)) / 86400.0 END AS age_days
  FROM per_dataflow
)
"""

_REGISTRY_SQL = """
SELECT _source_id, log_session_id, dataflow_id, workspace_id, COALESCE(dataflow_name, dataflow_id) AS dataflow_name,
       dataflow_description, COALESCE(stage, 'unknown') AS stage, group_number,
       execution_order, processing_mode, is_active, configure, operation_type,
       COALESCE(source_name, 'unknown') AS source_name,
       COALESCE(destination_name, 'unknown') AS destination_name,
       COALESCE(source_format, source_connection_type, 'unknown') AS source_format,
       COALESCE(destination_format, destination_connection_type, 'unknown') AS destination_format,
       COALESCE(source_connection_type, 'unknown') AS source_connection_type,
       COALESCE(destination_connection_type, 'unknown') AS destination_connection_type,
       source_id, source_catalog, source_database, source_schema, source_table,
       source_full_table, source_path, source_query, source_python_function,
       source_watermark_columns, source_filter_expression, source_configure, source_action,
       datacoolie_version,
       transform_deduplicate_columns, transform_latest_data_columns,
       transform_filter_expression, transform_additional_columns,
       transform_schema_hints, transform_select_columns, transform_drop_columns,
       transform_rename_columns, transform_value_rules, transform_hash_columns,
       transform_masking_rules, transform_configure,
       destination_id, destination_catalog, destination_database, destination_schema,
       destination_table, destination_full_table, destination_path,
       COALESCE(destination_load_type, 'unknown') AS destination_load_type,
       destination_merge_keys, destination_partition_columns, destination_configure,
       latest_status AS status, latest_status AS latest_run_status, latest_run_at,
       latest_run_id, latest_message, latest_error_message, latest_freshness_at,
       latest_freshness_status,
       latest_skipped_at, age_seconds, age_days, watermark_enabled,
       CASE WHEN watermark_enabled THEN 'configured' ELSE 'not_configured' END AS coverage_state,
       latest_movement_state AS movement_state,
       latest_read_boundary_adjustment_state AS adjustment_state,
       latest_read_boundary_adjustment_basis AS adjustment_basis,
       latest_read_boundary_adjustment_reason AS adjustment_reason,
       latest_read_boundary_adjustment_input_json AS adjustment_input_json,
       latest_read_boundary_adjustment_effective_json AS adjustment_effective_json,
       adjustment_adjusted_runs,
       adjustment_unchanged_runs,
       adjustment_unknown_runs,
       adjustment_not_applied_runs,
       adjustment_comparable_runs,
       adjustment_total_runs,
       latest_watermark_issue_state AS watermark_issue_state,
       latest_comparison_basis AS comparison_basis,
       latest_persistence_state AS persistence_state,
       latest_configuration_state AS configuration_state,
       latest_selection_state AS selection_state,
       CONCAT(latest_configuration_state, '_', latest_selection_state) AS configuration_selection_state,
       watermark_time,
       NULL::VARCHAR AS source_watermark_before,
       NULL::VARCHAR AS source_watermark_after,
       NULL::VARCHAR AS source_watermark_effective,
       latest_success_watermark, last_statuses,
       latest_success_watermark_at, latest_success_watermark_run_id,
       latest_success_watermark_source_id,
       read_request, selection, loaded_watermark, load_status, merge_base, merge_base_present,
       candidate, candidate_present, candidate_kind, watermark_to_save,
       save_status, saved_watermark, saved_watermark_present, selection_upper_origin,
       latest_source_id, latest_file_uri, latest_file_kind, latest_record_line,
       NULL::VARCHAR AS raw_record_json,
       skipped_streak, run_count,
       succeeded_runs AS succeeded_count, failed_runs AS failed_count,
       skipped_runs AS skipped_count, running_runs AS running_count, pending_runs AS pending_count,
       CASE WHEN age_days > 7 THEN 1 ELSE 0 END AS is_stale
FROM registry
"""

# The registry table does not need configuration, diagnostic captures, or
# native execution evidence. Keeping those fields out of the outer projection
# lets DuckDB prune their aggregate materialization while preserving the same
# filtered population, latest-row ordering, and table/display fallbacks.
_REGISTRY_TABLE_SQL = """
SELECT _source_id, log_session_id, dataflow_id, latest_run_id,
       COALESCE(dataflow_name, dataflow_id) AS dataflow_name,
       COALESCE(stage, 'unknown') AS stage,
       COALESCE(source_name, 'unknown') AS source_name,
       COALESCE(destination_name, 'unknown') AS destination_name,
       COALESCE(source_format, source_connection_type, 'unknown') AS source_format,
       COALESCE(destination_format, destination_connection_type, 'unknown') AS destination_format,
       COALESCE(source_connection_type, 'unknown') AS source_connection_type,
       COALESCE(destination_connection_type, 'unknown') AS destination_connection_type,
       source_table, source_full_table, source_path, source_query, source_python_function,
       destination_table, destination_full_table, destination_path,
       COALESCE(destination_load_type, 'unknown') AS destination_load_type,
       latest_status AS status, latest_status AS latest_run_status, latest_run_at,
       latest_freshness_at, latest_freshness_status,
       age_seconds, age_days, watermark_enabled,
       CASE WHEN watermark_enabled THEN 'configured' ELSE 'not_configured' END AS coverage_state,
       latest_movement_state AS movement_state,
       latest_success_watermark, last_statuses,
       latest_success_watermark_at, latest_success_watermark_run_id,
       latest_success_watermark_source_id,
       loaded_watermark, load_status, candidate, candidate_present,
       watermark_to_save, save_status, saved_watermark, saved_watermark_present
FROM registry
"""


def freshness_derived_ctes(
    *,
    base_relation: str = "filtered_dataflows",
    partition_by: str = "_source_id, dataflow_id",
) -> str:
    """Return the shared Freshness classifier over a selected run relation.

    The normal page reader keeps its historical source/dataflow partition. The
    dataflow registry passes its canonical run relation and partitions only by
    ``dataflow_id`` so duplicate physical copies cannot change movement or
    saved-watermark evidence. The expressions and UDFs remain owned here.
    """

    if not base_relation.replace("_", "").isalnum():
        raise ValueError("Unsupported Freshness base relation")
    if not partition_by.replace("_", "").replace(",", "").replace(" ", "").isalnum():
        raise ValueError("Unsupported Freshness partition")
    derived = _DERIVED_CTES.replace(
        "FROM filtered_dataflows fd",
        f"FROM {base_relation} fd",
    ).replace(
        "PARTITION BY _source_id, dataflow_id",
        f"PARTITION BY {partition_by}",
    )
    if partition_by == "dataflow_id":
        # ``per_dataflow`` normally preserves one row per source/dataflow for
        # the existing Freshness page. The shared registry has already chosen
        # one admitted row per run, so it must collapse all sources into one
        # entity and retain the canonical latest source as provenance.
        derived = derived.replace(
            "SELECT _source_id, dataflow_id,",
            f"SELECT FIRST(_source_id ORDER BY {_LATEST_RUN_ORDER}) AS _source_id, dataflow_id,",
        ).replace(
            "GROUP BY _source_id, dataflow_id",
            "GROUP BY dataflow_id",
        )
    return derived


def freshness_registry_sql(
    *,
    base_relation: str = "filtered_dataflows",
    compact: bool = True,
    partition_by: str = "_source_id, dataflow_id",
) -> str:
    """Build a registry projection using the canonical Freshness classifier.

    Callers prepend their own filtered/canonical run CTE and execute this
    projection on the same DuckDB connection and generation.
    """

    projection = _REGISTRY_TABLE_SQL if compact else _REGISTRY_SQL
    return f"{freshness_derived_ctes(base_relation=base_relation, partition_by=partition_by)} {projection}"


def dataflow_registry_freshness_ctes(
    *,
    base_relation: str = "filtered_dataflows",
    as_of: str | None = None,
    narrow_projection: bool = False,
) -> str:
    """Build the narrow Freshness summary used by the shared dataflow registry.

    This retains Freshness's classifier and eligibility expressions while
    avoiding ordered aggregates over native attempt/configuration values.
    Latest values come from whole scalar rows selected by canonical rank. Metadata and native attempt values are fetched separately for
    the one selected run when a drawer section requests them.
    """

    if not base_relation.replace("_", "").isalnum():
        raise ValueError("Unsupported Freshness base relation")
    timestamp = parse_utc_datetime(as_of)
    if timestamp is None:
        as_of_sql = "current_timestamp"
    else:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        stamp = timestamp.astimezone(timezone.utc).isoformat()
        as_of_sql = f"TIMESTAMPTZ '{stamp}'"

    source_relation = base_relation
    source_cte = ""
    if narrow_projection:
        source_relation = "registry_freshness_inputs"
        input_columns = (
            "dataflow_id", "dataflow_run_id", "_source_id", "_file_uri",
            "_record_line", "log_session_id", "observed_at", "event_time",
            "log_schema_version", "normalized_status", "operation_type", "attempts", "source_rows_read",
            "read_request", "source_watermark_columns", "source_configure",
            "source_format", "source_connection_type",
        )
        source_cte = (
            ", registry_freshness_inputs AS (SELECT "
            + ", ".join(input_columns)
            + f" FROM {base_relation})"
        )
    evidence = _V6_REGISTRY_EVIDENCE_CTE.replace(
        "FROM filtered_dataflows fd", f"FROM {source_relation} fd",
    ).replace("v6_evidence", "registry_v6_evidence")
    classification = (
        _V6_CLASSIFICATION_CTES
        .replace("v6_evidence", "registry_v6_evidence")
        .replace("v6_classification_payload", "registry_v6_classification_payload")
        .replace("v6_classified_evidence", "registry_v6_classified_evidence")
    )
    order = _DATAFLOW_REGISTRY_RUN_ORDER
    return f"""
{source_cte}
{evidence}
{classification}
, registry_snapshot_inputs AS (
  SELECT *, v6_class_watermark_enabled AS watermark_enabled,
         v6_class_persisted_movement_state AS persisted_movement_state,
         v6_class_persistence_state AS persistence_state
  FROM registry_v6_classified_evidence
  WHERE dataflow_id IS NOT NULL
), registry_snapshot_classified AS (
  SELECT *, v6_class_movement_state AS snapshot_movement_state
  FROM registry_snapshot_inputs
), registry_latest_snapshot AS (
  SELECT dataflow_id, snapshot_movement_state
  FROM registry_snapshot_classified
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY dataflow_id ORDER BY {order}
  ) = 1
)
, registry_etl_rows AS (
  SELECT *, v6_class_watermark_enabled AS watermark_enabled,
         v6_class_persisted_movement_state AS persisted_movement_state,
         CASE WHEN v6_merge_base_present AND v6_merge_base IS NULL THEN 'merge_base_null'
              WHEN v6_merge_base_present THEN 'merge_base'
              ELSE 'missing_merge_base' END AS comparison_basis,
         v6_class_persistence_state AS persistence_state,
         CASE WHEN v6_selection IS NOT NULL THEN 'selected'
              WHEN v6_attempt_present THEN 'not_selected' ELSE 'unknown' END AS selection_state,
         CASE WHEN v6_class_watermark_enabled THEN 'configured' ELSE 'not_configured' END AS configuration_state
  FROM registry_v6_classified_evidence
  WHERE LOWER(COALESCE(operation_type, 'unknown')) = 'etl'
), registry_classified_rows AS (
  SELECT *, v6_class_movement_state AS movement_state
  FROM registry_etl_rows
), registry_adjustment_rows AS (
  SELECT *,
         v6_class_adjustment_state AS read_boundary_adjustment_state,
         v6_class_adjustment_basis AS read_boundary_adjustment_basis,
         v6_class_adjustment_reason AS read_boundary_adjustment_reason,
         v6_class_adjustment_input_json AS read_boundary_adjustment_input_json,
         v6_class_adjustment_effective_json AS read_boundary_adjustment_effective_json
  FROM registry_classified_rows
), registry_issue_rows AS (
  SELECT *, v6_class_watermark_issue_state AS watermark_issue_state
  FROM registry_adjustment_rows
), registry_ranked_rows AS (
  SELECT *,
         ROW_NUMBER() OVER (PARTITION BY dataflow_id ORDER BY {order}) AS latest_rank,
         ROW_NUMBER() OVER (PARTITION BY dataflow_id ORDER BY {order}) AS run_rank,
         SUM(CASE WHEN normalized_status = 'skipped' THEN 0 ELSE 1 END) OVER (
           PARTITION BY dataflow_id ORDER BY {order}
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
         ) AS non_skipped_seen
  FROM registry_issue_rows
  WHERE dataflow_id IS NOT NULL
), registry_scalar_ranked AS (
  SELECT _file_uri, _record_line, _source_id, comparison_basis,
         configuration_state, dataflow_id, dataflow_run_id, event_time,
         latest_rank, log_session_id, movement_state, non_skipped_seen,
         normalized_status, observed_at, persisted_movement_state, persistence_state,
         read_boundary_adjustment_basis, read_boundary_adjustment_effective_json, read_boundary_adjustment_input_json, read_boundary_adjustment_reason,
         read_boundary_adjustment_state, run_rank, selection_state, v6_attempt_present,
         v6_candidate, v6_candidate_kind, v6_candidate_present, v6_load_status,
         v6_merge_base_present, v6_save_status, v6_saved_watermark, v6_saved_watermark_present,
         v6_source_status, watermark_enabled, watermark_issue_state
  FROM registry_ranked_rows
), registry_freshness_counts AS (
  SELECT dataflow_id,
         MAX(event_time) FILTER (WHERE normalized_status = 'skipped') AS latest_skipped_at,
         COUNT(*) FILTER (WHERE movement_state = 'advanced') AS watermark_advanced_runs,
         COUNT(*) FILTER (WHERE movement_state = 'initialized') AS watermark_initialized_runs,
         COUNT(*) FILTER (WHERE movement_state = 'unchanged') AS watermark_unchanged_runs,
         COUNT(*) FILTER (WHERE movement_state = 'changed') AS watermark_changed_runs,
         COUNT(*) FILTER (WHERE movement_state = 'unknown') AS watermark_unknown_runs,
         COUNT(*) FILTER (WHERE movement_state = 'empty') AS watermark_empty_runs,
         {_WATERMARK_RATE_DENOMINATOR} AS watermark_rate_runs,
         {_WATERMARK_ADVANCED_RATE} AS watermark_advanced_rate,
         BOOL_OR(watermark_enabled) AS watermark_enabled,
         COUNT(*) FILTER (WHERE read_boundary_adjustment_state = 'adjusted') AS adjustment_adjusted_runs,
         COUNT(*) FILTER (WHERE read_boundary_adjustment_state = 'unchanged') AS adjustment_unchanged_runs,
         COUNT(*) FILTER (WHERE read_boundary_adjustment_state = 'unknown') AS adjustment_unknown_runs,
         COUNT(*) FILTER (WHERE read_boundary_adjustment_state = 'not_applied') AS adjustment_not_applied_runs,
         COUNT(*) FILTER (WHERE read_boundary_adjustment_state IN ('adjusted','unchanged')) AS adjustment_comparable_runs,
         COUNT(*) AS adjustment_total_runs,
         COUNT(*) AS freshness_run_count,
         COUNT(*) FILTER (WHERE {_CHECK_ELIGIBLE}) AS freshness_checks_eligible,
         COUNT(*) FILTER (WHERE watermark_enabled) AS watermark_enabled_runs,
         COUNT(*) FILTER (WHERE normalized_status = 'skipped' AND non_skipped_seen = 0) AS skipped_streak,
         LIST(STRUCT_PACK(status := normalized_status, "time" := event_time,
                          dataflow_run_id := dataflow_run_id) ORDER BY {order})
           FILTER (WHERE run_rank <= 5) AS last_statuses
  FROM registry_scalar_ranked
  GROUP BY dataflow_id
), registry_freshness_latest AS (
  SELECT dataflow_id, _source_id AS freshness_source_id,
         dataflow_run_id AS freshness_latest_run_id,
         persisted_movement_state AS persisted_movement_state,
         watermark_issue_state AS watermark_issue_state,
         comparison_basis AS comparison_basis,
         persistence_state AS persistence_state,
         configuration_state AS configuration_state,
         selection_state AS selection_state,
         read_boundary_adjustment_state AS adjustment_state,
         read_boundary_adjustment_basis AS adjustment_basis,
         read_boundary_adjustment_reason AS adjustment_reason,
         read_boundary_adjustment_input_json AS adjustment_input_json,
         read_boundary_adjustment_effective_json AS adjustment_effective_json,
         v6_load_status AS load_status,
         v6_save_status AS save_status,
         CAST(v6_candidate AS VARCHAR) AS candidate,
         v6_candidate_kind AS candidate_kind,
         v6_merge_base_present AS merge_base_present,
         v6_candidate_present AS candidate_present,
         v6_saved_watermark_present AS saved_watermark_present
  FROM registry_scalar_ranked
  WHERE latest_rank = 1
)
, registry_freshness_check AS (
  SELECT dataflow_id, event_time AS latest_freshness_at,
         normalized_status AS latest_freshness_status,
         movement_state AS movement_state
  FROM registry_scalar_ranked
  WHERE {_CHECK_ELIGIBLE}
  QUALIFY ROW_NUMBER() OVER (PARTITION BY dataflow_id ORDER BY {order}) = 1
)
, registry_freshness_saved AS (
  SELECT dataflow_id, CAST(v6_saved_watermark AS VARCHAR) AS latest_success_watermark,
         event_time AS latest_success_watermark_at,
         dataflow_run_id AS latest_success_watermark_run_id,
         _source_id AS latest_success_watermark_source_id
  FROM registry_scalar_ranked
  WHERE v6_save_status = 'saved'
  QUALIFY ROW_NUMBER() OVER (PARTITION BY dataflow_id ORDER BY {order}) = 1
)
, registry_freshness_configured AS (
  SELECT dataflow_id, event_time AS watermark_time
  FROM registry_scalar_ranked
  WHERE watermark_enabled
  QUALIFY ROW_NUMBER() OVER (PARTITION BY dataflow_id ORDER BY {order}) = 1
)
, registry_freshness AS (
  SELECT counts.*,
         registry_freshness_latest.freshness_source_id,
         registry_freshness_latest.freshness_latest_run_id,
         registry_freshness_latest.persisted_movement_state,
         registry_freshness_latest.watermark_issue_state,
         registry_freshness_latest.comparison_basis,
         registry_freshness_latest.persistence_state,
         registry_freshness_latest.configuration_state,
         registry_freshness_latest.selection_state,
         registry_freshness_latest.adjustment_state,
         registry_freshness_latest.adjustment_basis,
         registry_freshness_latest.adjustment_reason,
         registry_freshness_latest.adjustment_input_json,
         registry_freshness_latest.adjustment_effective_json,
         registry_freshness_latest.load_status,
         registry_freshness_latest.save_status,
         registry_freshness_latest.candidate,
         registry_freshness_latest.candidate_kind,
         registry_freshness_latest.merge_base_present,
         registry_freshness_latest.candidate_present,
         registry_freshness_latest.saved_watermark_present,
         registry_freshness_check.latest_freshness_at,
         registry_freshness_check.latest_freshness_status,
         registry_freshness_check.movement_state,
         registry_freshness_saved.latest_success_watermark,
         registry_freshness_saved.latest_success_watermark_at,
         registry_freshness_saved.latest_success_watermark_run_id,
         registry_freshness_saved.latest_success_watermark_source_id,
         registry_freshness_configured.watermark_time
  FROM registry_freshness_counts counts
  LEFT JOIN registry_freshness_latest USING (dataflow_id)
  LEFT JOIN registry_freshness_check USING (dataflow_id)
  LEFT JOIN registry_freshness_saved USING (dataflow_id)
  LEFT JOIN registry_freshness_configured USING (dataflow_id)
)
, registry_entity_scope AS (
  SELECT DISTINCT dataflow_id FROM {source_relation}
  WHERE dataflow_id IS NOT NULL
), registry_freshness_summary AS (
  SELECT entity.dataflow_id,
         freshness.freshness_source_id,
         freshness.freshness_latest_run_id,
         freshness.latest_freshness_at,
         freshness.latest_freshness_status,
         freshness.latest_success_watermark AS latest_success_watermark_sort_value,
         CASE WHEN length(freshness.latest_success_watermark) > 512
              THEN LEFT(freshness.latest_success_watermark, 511) || '…'
              ELSE freshness.latest_success_watermark END AS latest_success_watermark_preview,
         COALESCE(length(freshness.latest_success_watermark) > 512, FALSE)
           AS latest_success_watermark_truncated,
         freshness.latest_success_watermark_at,
         freshness.latest_success_watermark_run_id,
         freshness.latest_success_watermark_source_id,
         freshness.latest_skipped_at,
         freshness.movement_state,
         snapshot.snapshot_movement_state,
         COALESCE(freshness.watermark_advanced_runs, 0) AS watermark_advanced_runs,
         COALESCE(freshness.watermark_initialized_runs, 0) AS watermark_initialized_runs,
         COALESCE(freshness.watermark_unchanged_runs, 0) AS watermark_unchanged_runs,
         COALESCE(freshness.watermark_changed_runs, 0) AS watermark_changed_runs,
         COALESCE(freshness.watermark_unknown_runs, 0) AS watermark_unknown_runs,
         COALESCE(freshness.watermark_empty_runs, 0) AS watermark_empty_runs,
         COALESCE(freshness.watermark_rate_runs, 0) AS watermark_rate_runs,
         freshness.watermark_advanced_rate,
         freshness.watermark_enabled,
         freshness.persisted_movement_state,
         freshness.watermark_issue_state,
         freshness.comparison_basis,
         freshness.persistence_state,
         freshness.configuration_state,
         freshness.selection_state,
         freshness.adjustment_state,
         freshness.adjustment_basis,
         freshness.adjustment_reason,
         freshness.adjustment_input_json,
         freshness.adjustment_effective_json,
         COALESCE(freshness.adjustment_adjusted_runs, 0) AS adjustment_adjusted_runs,
         COALESCE(freshness.adjustment_unchanged_runs, 0) AS adjustment_unchanged_runs,
         COALESCE(freshness.adjustment_unknown_runs, 0) AS adjustment_unknown_runs,
         COALESCE(freshness.adjustment_not_applied_runs, 0) AS adjustment_not_applied_runs,
         COALESCE(freshness.adjustment_comparable_runs, 0) AS adjustment_comparable_runs,
         COALESCE(freshness.adjustment_total_runs, 0) AS adjustment_total_runs,
         freshness.watermark_time,
         freshness.load_status,
         freshness.save_status,
         freshness.candidate,
         freshness.candidate_kind,
         freshness.merge_base_present,
         freshness.candidate_present,
         freshness.saved_watermark_present,
         COALESCE(freshness.freshness_run_count, 0) AS freshness_run_count,
         COALESCE(freshness.freshness_checks_eligible, 0) AS freshness_checks_eligible,
         COALESCE(freshness.watermark_enabled_runs, 0) AS watermark_enabled_runs,
         COALESCE(freshness.skipped_streak, 0) AS skipped_streak,
         freshness.last_statuses,
         CASE WHEN freshness.latest_freshness_at IS NULL THEN NULL
              ELSE GREATEST(0, epoch({as_of_sql} - freshness.latest_freshness_at)) END AS age_seconds,
         CASE WHEN freshness.latest_freshness_at IS NULL THEN NULL
              ELSE GREATEST(0, epoch({as_of_sql} - freshness.latest_freshness_at)) / 86400.0 END AS age_days
  FROM registry_entity_scope entity
  LEFT JOIN registry_freshness freshness USING (dataflow_id)
  LEFT JOIN registry_latest_snapshot snapshot USING (dataflow_id)
)
"""

_AGE_BY_DATAFLOW_SQL = """
SELECT _source_id, log_session_id, dataflow_id, latest_run_id,
       COALESCE(dataflow_name, dataflow_id) AS dataflow_name,
       CONCAT(COALESCE(source_name, 'unknown'), ' -> ', COALESCE(destination_name, 'unknown')) AS target,
       latest_freshness_at, latest_freshness_status, latest_status AS latest_run_status,
       latest_skipped_at, age_seconds, age_days
FROM registry
WHERE age_days IS NOT NULL
ORDER BY age_days DESC NULLS LAST, dataflow_name ASC NULLS LAST,
         _source_id ASC NULLS LAST, log_session_id ASC NULLS LAST,
         dataflow_id ASC NULLS LAST, latest_run_id ASC NULLS LAST
LIMIT 200
"""

_SUMMARY_SQL = f"""
WITH filtered_dataflows_summary AS (
  SELECT COUNT(*) AS dataflow_records,
         MIN(run_date) AS date_min,
         MAX(run_date) AS date_max,
         MAX(event_time) AS latest_dataflow_log_at,
         COUNT(DISTINCT {known_dimension_sql('engine_name')}) AS active_engines,
         COUNT(DISTINCT {known_dimension_sql('metadata_provider_name')}) AS active_metadata_providers
  FROM filtered_dataflows
), filtered_jobs_summary AS (
  SELECT COUNT(*) AS job_records,
         MAX(event_time) AS latest_job_log_at
  FROM filtered_jobs
), etl_rows_summary AS (
  SELECT COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS successful_runs,
         COUNT(*) FILTER (WHERE {_CHECK_ELIGIBLE}) AS freshness_runs,
         COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed_runs,
         COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped_runs,
         COUNT(*) FILTER (WHERE dataflow_id IS NULL OR TRIM(dataflow_id) = '') AS missing_dataflow_id_runs,
         COUNT(*) FILTER (WHERE movement_state = 'advanced') AS watermark_advanced_runs,
         COUNT(*) FILTER (WHERE movement_state = 'initialized') AS watermark_initialized_runs,
         COUNT(*) FILTER (WHERE movement_state = 'unchanged') AS watermark_unchanged_runs,
         COUNT(*) FILTER (WHERE movement_state = 'changed') AS watermark_changed_runs,
         COUNT(*) FILTER (WHERE movement_state = 'unknown') AS watermark_unknown_runs,
         COUNT(*) FILTER (WHERE movement_state = 'empty') AS watermark_empty_runs,
         NULL::BIGINT AS watermark_incomplete_runs,
         NULL::BIGINT AS watermark_invalid_runs,
         NULL::BIGINT AS watermark_adjusted_runs,
         COUNT(*) FILTER (WHERE persistence_state = 'not_requested') AS watermark_not_requested_runs,
         COUNT(*) FILTER (WHERE persistence_state = 'not_attempted') AS watermark_not_attempted_runs,
         COUNT(*) FILTER (WHERE persistence_state = 'no_candidate') AS watermark_no_candidate_runs,
         COUNT(*) FILTER (WHERE persistence_state = 'save_error') AS watermark_save_error_runs,
         {_WATERMARK_RATE_DENOMINATOR} AS watermark_rate_runs,
         COUNT(*) FILTER (WHERE movement_state <> 'not_configured') AS watermark_enabled_runs
  FROM issue_etl_rows
), registry_summary AS (
  SELECT COUNT(*) AS observed_dataflows,
         COUNT(*) FILTER (WHERE latest_freshness_at IS NOT NULL) AS dataflows_with_freshness_evidence,
         COUNT(*) FILTER (WHERE latest_status NOT IN ('succeeded','skipped')) AS latest_status_issue_dataflows,
         COUNT(*) FILTER (WHERE latest_freshness_at IS NULL) AS latest_check_missing_dataflows,
         COUNT(*) FILTER (WHERE latest_watermark_issue_state IN ('error','insufficient_evidence')) AS latest_watermark_issue_dataflows,
         COUNT(*) FILTER (WHERE latest_movement_state = 'unknown') AS latest_watermark_unknown_dataflows,
         COUNT(*) FILTER (WHERE latest_movement_state = 'changed') AS latest_watermark_changed_dataflows,
         NULL::BIGINT AS latest_watermark_invalid_dataflows,
         NULL::BIGINT AS latest_watermark_incomplete_dataflows,
         COUNT(*) FILTER (WHERE watermark_enabled) AS watermark_enabled_dataflows,
         ROUND(100.0 * COUNT(*) FILTER (WHERE watermark_enabled) / NULLIF(COUNT(*), 0), 2) AS watermark_coverage_rate,
         COUNT(*) FILTER (WHERE skipped_streak >= 3) AS skipped_streak_dataflows,
         COUNT(*) FILTER (WHERE age_days > 7) AS stale_candidates,
         COUNT(*) FILTER (WHERE age_days > 7) AS stale_dataflows,
         ROUND(100.0 * COUNT(*) FILTER (WHERE age_days > 7) / NULLIF(COUNT(*), 0), 2) AS stale_dataflow_rate,
         CASE WHEN COUNT(age_days) = 0 THEN NULL ELSE MIN(age_days) END AS min_age_days,
         CASE WHEN COUNT(age_days) = 0 THEN NULL ELSE {discrete_percentile('age_days', 0.50)} END AS p50_age_days,
         CASE WHEN COUNT(age_days) = 0 THEN NULL ELSE {discrete_percentile('age_days', 0.95)} END AS p95_age_days,
         CASE WHEN COUNT(age_days) = 0 THEN NULL ELSE MAX(age_days) END AS max_age_days,
         CASE WHEN COUNT(age_seconds) = 0 THEN NULL ELSE MIN(age_seconds) END AS min_age_seconds,
         CASE WHEN COUNT(age_seconds) = 0 THEN NULL ELSE {discrete_percentile('age_seconds', 0.50)} END AS p50_age_seconds,
         CASE WHEN COUNT(age_seconds) = 0 THEN NULL ELSE {discrete_percentile('age_seconds', 0.95)} END AS p95_age_seconds,
         CASE WHEN COUNT(age_seconds) = 0 THEN NULL ELSE MAX(age_seconds) END AS max_age_seconds
  FROM registry
)
SELECT fd.dataflow_records,
       jobs.job_records,
       fd.date_min,
       fd.date_max,
       fd.latest_dataflow_log_at,
       jobs.latest_job_log_at,
       GREATEST(fd.latest_dataflow_log_at, jobs.latest_job_log_at) AS latest_log_at,
       fd.active_engines,
       fd.active_metadata_providers,
       etl.successful_runs,
       etl.freshness_runs,
       etl.failed_runs,
       etl.skipped_runs,
       registry.observed_dataflows,
       etl.missing_dataflow_id_runs,
       registry.dataflows_with_freshness_evidence,
       registry.latest_status_issue_dataflows,
       registry.latest_check_missing_dataflows,
       registry.latest_watermark_issue_dataflows,
       registry.latest_watermark_unknown_dataflows,
       registry.latest_watermark_changed_dataflows,
       registry.latest_watermark_invalid_dataflows,
       registry.latest_watermark_incomplete_dataflows,
       registry.watermark_enabled_dataflows,
       registry.watermark_coverage_rate,
       etl.watermark_advanced_runs,
       etl.watermark_initialized_runs,
       etl.watermark_unchanged_runs,
       etl.watermark_changed_runs,
       etl.watermark_unknown_runs,
       etl.watermark_empty_runs,
       etl.watermark_incomplete_runs,
       etl.watermark_invalid_runs,
       etl.watermark_adjusted_runs,
       etl.watermark_not_requested_runs,
       etl.watermark_not_attempted_runs,
       etl.watermark_no_candidate_runs,
       etl.watermark_save_error_runs,
       etl.watermark_rate_runs,
       etl.watermark_enabled_runs,
       ROUND(100.0 * etl.watermark_advanced_runs /
         NULLIF(etl.watermark_rate_runs, 0), 2) AS watermark_advanced_rate,
       registry.skipped_streak_dataflows,
       3 AS skipped_streak_threshold,
       registry.stale_candidates,
       registry.stale_dataflows,
       7 AS stale_threshold_days,
       registry.stale_dataflow_rate,
       registry.min_age_days,
       registry.p50_age_days,
       registry.p95_age_days,
       registry.max_age_days,
       registry.min_age_seconds,
       registry.p50_age_seconds,
       registry.p95_age_seconds,
       registry.max_age_seconds
FROM filtered_dataflows_summary fd
CROSS JOIN filtered_jobs_summary jobs
CROSS JOIN etl_rows_summary etl
CROSS JOIN registry_summary registry
"""

_ATTENTION_SQL = """
SELECT
  COUNT(*) FILTER (WHERE age_days > 7) AS stale_candidates,
  (SELECT COUNT(*) FROM classified_etl_rows WHERE movement_state = 'unchanged')
    AS watermark_unchanged_runs
FROM registry
"""

_ATTENTION_DERIVED_CTES = f"""
{_V6_EVIDENCE_CTE}
, etl_rows AS (
  SELECT *, {_V6_HAS_WATERMARK} AS watermark_enabled,
         {_V6_MOVEMENT} AS persisted_movement_state,
         {_V6_PERSISTENCE} AS persistence_state
  FROM v6_evidence
  WHERE LOWER(COALESCE(operation_type, 'unknown')) = 'etl'
), classified_etl_rows AS (
  SELECT *, {_V6_CLASSIFIED_MOVEMENT} AS movement_state
  FROM etl_rows
), per_dataflow AS (
  SELECT _source_id, dataflow_id,
         FIRST(movement_state ORDER BY {_LATEST_RUN_ORDER}) AS latest_movement_state,
         MAX(event_time) FILTER (WHERE {_CHECK_ELIGIBLE}) AS latest_freshness_at
  FROM classified_etl_rows
  WHERE dataflow_id IS NOT NULL
  GROUP BY _source_id, dataflow_id
), registry AS (
  SELECT *, CASE WHEN latest_freshness_at IS NULL THEN NULL
                 ELSE GREATEST(0, epoch(current_timestamp - latest_freshness_at)) / 86400.0 END AS age_days
  FROM per_dataflow
)
"""

_MOVEMENT_TREND_SQL = f"""
SELECT date_trunc(?, timezone(?, event_time)) AS bucket_start,
       COUNT(*) FILTER (WHERE movement_state = 'advanced') AS advanced,
       COUNT(*) FILTER (WHERE movement_state = 'initialized') AS initialized,
       COUNT(*) FILTER (WHERE movement_state = 'unchanged') AS unchanged,
       COUNT(*) FILTER (WHERE movement_state = 'changed') AS changed,
       COUNT(*) FILTER (WHERE movement_state = 'unknown') AS unknown,
       COUNT(*) FILTER (WHERE movement_state = 'empty') AS empty,
       NULL::BIGINT AS incomplete,
       NULL::BIGINT AS invalid,
       NULL::BIGINT AS adjusted,
       COUNT(*) FILTER (WHERE persistence_state = 'not_requested') AS not_requested,
       COUNT(*) FILTER (WHERE persistence_state = 'not_attempted') AS not_attempted,
       COUNT(*) FILTER (WHERE persistence_state = 'no_candidate') AS no_candidate,
       COUNT(*) FILTER (WHERE persistence_state = 'save_error') AS save_error,
       COUNT(*) FILTER (WHERE watermark_enabled) AS watermark_enabled_runs,
       {_WATERMARK_RATE_DENOMINATOR} AS watermark_rate_runs,
       {_WATERMARK_ADVANCED_RATE} AS advanced_rate
FROM classified_etl_rows WHERE event_time IS NOT NULL
GROUP BY bucket_start ORDER BY bucket_start
"""

_AGE_DISTRIBUTION_SQL = """
SELECT CASE WHEN age_days IS NULL THEN 'Unknown' WHEN age_days <= 1 THEN '≤1d'
            WHEN age_days <= 3 THEN '1–3d' WHEN age_days <= 7 THEN '3–7d'
            WHEN age_days <= 30 THEN '7–30d' ELSE '>30d' END AS bucket,
       COUNT(*) AS dataflows,
       CASE WHEN MIN(age_days) > 7 THEN 1 ELSE 0 END AS is_stale
FROM registry GROUP BY bucket
ORDER BY CASE bucket WHEN '≤1d' THEN 1 WHEN '1–3d' THEN 2 WHEN '3–7d' THEN 3 WHEN '7–30d' THEN 4 WHEN '>30d' THEN 5 ELSE 6 END
"""

_STAGE_COVERAGE_SQL = """
SELECT COALESCE(NULLIF(stage, ''), 'unknown') AS stage,
       COUNT(DISTINCT (_source_id, dataflow_id)) AS observed_dataflows,
       COUNT(DISTINCT (_source_id, dataflow_id))
         FILTER (WHERE watermark_enabled) AS watermark_enabled_dataflows,
       COUNT(DISTINCT (_source_id, dataflow_id)) -
         COUNT(DISTINCT (_source_id, dataflow_id))
           FILTER (WHERE watermark_enabled) AS not_configured_dataflows,
       ROUND(100.0 * COUNT(DISTINCT (_source_id, dataflow_id))
         FILTER (WHERE watermark_enabled) /
         NULLIF(COUNT(DISTINCT (_source_id, dataflow_id)), 0), 2) AS coverage_rate
FROM etl_rows WHERE dataflow_id IS NOT NULL
GROUP BY stage ORDER BY observed_dataflows DESC, stage LIMIT 100
"""

_STREAK_DISTRIBUTION_SQL = """
SELECT CASE WHEN skipped_streak = 1 THEN '1'
            WHEN skipped_streak <= 3 THEN '2–3'
            WHEN skipped_streak <= 7 THEN '4–7'
            ELSE '>7' END AS bucket,
       COUNT(*) AS dataflows
FROM registry WHERE skipped_streak > 0
GROUP BY bucket
ORDER BY CASE bucket WHEN '1' THEN 1 WHEN '2–3' THEN 2 WHEN '4–7' THEN 3 ELSE 4 END
"""

_READ_BOUNDARY_BY_DATAFLOW_SQL = """
SELECT _source_id, log_session_id, dataflow_id, latest_run_id, latest_run_at,
       COALESCE(dataflow_name, dataflow_id) AS dataflow_name,
       COALESCE(source_name, 'unknown') AS source_name,
       COALESCE(destination_name, 'unknown') AS destination_name,
       latest_read_boundary_adjustment_state AS adjustment_state,
       latest_read_boundary_adjustment_basis AS adjustment_basis,
       latest_read_boundary_adjustment_reason AS adjustment_reason,
       latest_read_boundary_adjustment_input_json AS adjustment_input_json,
       latest_read_boundary_adjustment_effective_json AS adjustment_effective_json,
       adjustment_adjusted_runs,
       adjustment_unchanged_runs,
       adjustment_unknown_runs,
       adjustment_not_applied_runs,
       adjustment_comparable_runs,
       adjustment_total_runs
FROM registry
ORDER BY adjustment_adjusted_runs DESC NULLS LAST,
         CASE WHEN adjustment_comparable_runs > 0
              THEN adjustment_adjusted_runs::DOUBLE / adjustment_comparable_runs
              ELSE NULL END DESC NULLS LAST,
         dataflow_name ASC NULLS LAST, _source_id ASC NULLS LAST,
         log_session_id ASC NULLS LAST, dataflow_id ASC NULLS LAST,
         latest_run_id ASC NULLS LAST
"""

_BUNDLE_SQL = f"""
, summary_result AS ({_SUMMARY_SQL}),
age_by_dataflow_result AS ({_AGE_BY_DATAFLOW_SQL}),
movement_trend_result AS ({_MOVEMENT_TREND_SQL}),
age_distribution_result AS ({_AGE_DISTRIBUTION_SQL}),
stage_coverage_result AS ({_STAGE_COVERAGE_SQL}),
streak_distribution_result AS ({_STREAK_DISTRIBUTION_SQL}),
read_boundary_adjustment_result AS ({_READ_BOUNDARY_BY_DATAFLOW_SQL})
SELECT
  (SELECT summary_row FROM summary_result summary_row) AS summary,
  (SELECT list(age_row) FROM age_by_dataflow_result age_row) AS age_by_dataflow,
  (SELECT list(movement_row) FROM movement_trend_result movement_row) AS movement_trend,
  (SELECT list(age_bucket) FROM age_distribution_result age_bucket) AS age_distribution,
  (SELECT list(stage_row) FROM stage_coverage_result stage_row) AS stage_coverage,
  (SELECT list(streak_row) FROM streak_distribution_result streak_row) AS streak_distribution,
  (SELECT list(adjustment_row) FROM read_boundary_adjustment_result adjustment_row) AS read_boundary_adjustment
"""

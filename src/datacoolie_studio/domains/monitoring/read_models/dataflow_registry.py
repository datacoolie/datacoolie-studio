"""Shared environment/dataflow registry projections for Monitoring.

The existing page readers intentionally preserve their historical source-grain
contracts.  This reader is the migration boundary for registry consumers: it
deduplicates admitted copies across log sources before calculating entity-level
metrics and keeps the selected physical provenance on the latest run.
"""

from __future__ import annotations

from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.core.request_timing import timed_stage
from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics.record_codec import root_column_types
from datacoolie_studio.domains.analytics.serving_facts import MONITORING_JOB_FACTS_TABLE
from datacoolie_studio.domains.analytics.sql import quote_identifier
from datacoolie_studio.domains.analytics.v6_projection import field_expression
from datacoolie_studio.domains.monitoring.log_repository import monitoring_filter_sql
from datacoolie_studio.domains.monitoring.read_models.common import (
    AnalyticsContext,
    paged_rows,
    reader_context,
    rows,
)
from datacoolie_studio.domains.monitoring.read_models.freshness import (
    dataflow_registry_freshness_ctes,
)
from datacoolie_studio.domains.monitoring.read_models.performance import (
    PERFORMANCE_REGISTRY_COUNT_FIELDS,
    PERFORMANCE_REGISTRY_INPUT_COLUMNS,
    dataflow_registry_performance_ctes,
)
from datacoolie_studio.domains.monitoring.read_models.volume import (
    VOLUME_REGISTRY_INPUT_COLUMNS,
    dataflow_registry_volume_ctes,
)


_DATAFLOW_COLUMNS = (
    "_source_id",
    "_file_uri",
    "_file_kind",
    "_record_line",
    "_ingested_at",
    "observed_at",
    "log_schema_version",
    "log_session_id",
    "job_id",
    "job_num",
    "job_index",
    "attempts",
    "read_request",
    "dataflow_id",
    "dataflow_run_id",
    "workspace_id",
    "dataflow_name",
    "dataflow_description",
    "stage",
    "group_number",
    "execution_order",
    "processing_mode",
    "is_active",
    "datacoolie_version",
    "status",
    "message",
    "error_message",
    "start_time",
    "end_time",
    "duration_seconds",
    "operation_type",
    "configure",
    "source_name",
    "source_id",
    "source_connection_type",
    "source_format",
    "source_catalog",
    "source_database",
    "source_schema",
    "source_full_table",
    "source_table",
    "source_path",
    "source_query",
    "source_python_function",
    "source_watermark_columns",
    "source_filter_expression",
    "source_configure",
    "source_action",
    "source_rows_read",
    "source_status",
    "source_duration_seconds",
    "transform_status",
    "transform_duration_seconds",
    "transform_deduplicate_columns",
    "transform_latest_data_columns",
    "transform_filter_expression",
    "transform_additional_columns",
    "transform_schema_hints",
    "transform_select_columns",
    "transform_drop_columns",
    "transform_rename_columns",
    "transform_value_rules",
    "transform_hash_columns",
    "transform_masking_rules",
    "transform_configure",
    "destination_name",
    "destination_id",
    "destination_connection_type",
    "destination_format",
    "destination_catalog",
    "destination_database",
    "destination_schema",
    "destination_full_table",
    "destination_table",
    "destination_path",
    "destination_merge_keys",
    "destination_partition_columns",
    "destination_configure",
    "destination_operation_type",
    "destination_load_type",
    "destination_status",
    "destination_duration_seconds",
    "destination_rows_written",
    "destination_rows_inserted",
    "destination_rows_updated",
    "destination_rows_deleted",
    "destination_bytes_added",
    "destination_bytes_removed",
    "destination_bytes_saved",
    "destination_files_added",
    "destination_files_removed",
    "overhead_duration_seconds",
)
_REGISTRY_DATAFLOW_COLUMNS = (
    "_source_id", "_file_uri", "_file_kind", "_record_line", "_ingested_at",
    "observed_at", "log_schema_version", "log_session_id", "job_id", "job_num",
    "job_index", "dataflow_id", "dataflow_run_id", "dataflow_name",
    "dataflow_description", "stage", "status", "message", "error_message",
    "start_time", "end_time", "duration_seconds", "operation_type",
    "source_rows_read", "source_status", "source_duration_seconds",
    "transform_status", "transform_duration_seconds", "destination_status",
    "destination_duration_seconds", "destination_rows_written",
    "destination_rows_inserted", "destination_rows_updated", "destination_rows_deleted",
    "destination_bytes_added", "destination_bytes_removed", "destination_bytes_saved", "destination_files_added",
    "destination_files_removed", "overhead_duration_seconds",
)
_EXECUTION_DATAFLOW_COLUMNS = (*_REGISTRY_DATAFLOW_COLUMNS,
    "source_name", "source_connection_type", "source_format", "source_full_table",
    "source_table", "source_path", "destination_name", "destination_connection_type",
    "destination_format", "destination_full_table", "destination_table",
    "destination_path", "destination_load_type",
)
_FRESHNESS_DATAFLOW_COLUMNS = (
    "_source_id", "_file_uri", "_file_kind", "_record_line", "_ingested_at",
    "observed_at", "log_schema_version", "log_session_id", "job_id",
    "dataflow_id", "dataflow_run_id", "workspace_id", "dataflow_name",
    "dataflow_description", "stage", "status", "message", "error_message",
    "operation_type", "source_name", "source_format", "source_connection_type",
    "source_watermark_columns", "source_configure", "source_rows_read", "read_request", "attempts",
)
_REGISTRY_ROUTE_COLUMNS = (
    "source_name", "source_connection_type", "source_format", "source_full_table",
    "source_table", "source_path", "source_query", "source_python_function",
    "destination_name", "destination_connection_type", "destination_format",
    "destination_full_table", "destination_table", "destination_path",
    "destination_load_type",
)
_REGISTRY_CONTEXT_COLUMNS = (
    "log_schema_version", "operation_type", "destination_operation_type",
    "group_number", "execution_order",
)
_REGISTRY_OVERVIEW_COLUMNS = tuple(dict.fromkeys((
    *_REGISTRY_DATAFLOW_COLUMNS,
    *_REGISTRY_ROUTE_COLUMNS,
    *_REGISTRY_CONTEXT_COLUMNS,
    *PERFORMANCE_REGISTRY_INPUT_COLUMNS,
    *VOLUME_REGISTRY_INPUT_COLUMNS,
    *_FRESHNESS_DATAFLOW_COLUMNS,
)))
_REGISTRY_LIST_COLUMNS = (
    "_source_id", "_file_uri", "_file_kind", "_record_line", "_ingested_at",
    "observed_at", "log_schema_version", "log_session_id", "job_id", "job_num",
    "job_index", "dataflow_id", "dataflow_run_id", "dataflow_name",
    "dataflow_description", "stage", "status", "message", "error_message",
    "start_time", "end_time", "duration_seconds", "operation_type",
    *_REGISTRY_ROUTE_COLUMNS, *_REGISTRY_CONTEXT_COLUMNS,
)
_SAVED_WATERMARK_DATAFLOW_COLUMNS = (
    "_source_id", "_file_uri", "_file_kind", "_record_line", "_ingested_at",
    "observed_at", "log_schema_version", "log_session_id", "job_id",
    "dataflow_id", "dataflow_run_id", "status", "operation_type", "attempts",
)
_TECHNICAL_DATAFLOW_COLUMNS = tuple(dict.fromkeys((
    *_REGISTRY_DATAFLOW_COLUMNS,
    *_REGISTRY_ROUTE_COLUMNS,
    *_REGISTRY_CONTEXT_COLUMNS,
    "workspace_id", "group_number", "execution_order", "processing_mode",
    "is_active", "datacoolie_version", "destination_operation_type",
    "source_id", "source_catalog", "source_database", "source_schema",
    "source_filter_expression", "source_action",
    "destination_id", "destination_catalog", "destination_database", "destination_schema",
    "transform_filter_expression", "source_error_message", "transform_error_message",
    "destination_error_message",
)))
_TECHNICAL_REFERENCE_COMPARISON_BASIS = (
    "run/phase status; event/start/end and phase durations; route and runtime "
    "scalars; filter expressions; workload; messages/errors (nested configuration excluded)"
)
_DATAFLOW_SOURCE_TYPES = {
    **root_column_types("dataflow_run_log"),
    **analytics_schema.STUDIO_CACHE_COLUMNS,
    "_record_line": "BIGINT",
    "__event_time": "TIMESTAMPTZ",
    "__run_date": "DATE",
}


_DETAIL_SNAPSHOT_SQL = """
SELECT
    snapshot.*,
    snapshot._source_id AS latest_source_id,
    snapshot._file_uri AS latest_file_uri,
    snapshot._file_kind AS latest_file_kind,
    snapshot._record_line AS latest_record_line,
    snapshot.log_session_id AS latest_log_session_id,
    snapshot.dataflow_run_id AS latest_run_id,
    snapshot.normalized_status AS latest_run_status,
    snapshot.event_time AS latest_run_at,
    snapshot.start_time AS latest_start_time,
    snapshot.end_time AS latest_end_time,
    snapshot.duration_seconds AS latest_duration_seconds,
    snapshot.message AS latest_message,
    snapshot.error_message AS latest_error_message,
    snapshot.job_id AS latest_job_id,
    snapshot.job_num AS latest_job_num,
    snapshot.job_index AS latest_job_index
FROM filtered_dataflows snapshot
WHERE snapshot.dataflow_id IS NOT NULL
  AND TRIM(CAST(snapshot.dataflow_id AS VARCHAR)) <> ''
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY snapshot.dataflow_id
    ORDER BY snapshot.event_time DESC NULLS LAST,
             TRY_CAST(snapshot.observed_at AS TIMESTAMPTZ) DESC NULLS LAST,
             snapshot._source_id ASC NULLS LAST,
             snapshot._file_uri ASC NULLS LAST,
             snapshot._record_line ASC NULLS LAST,
             snapshot.log_session_id ASC NULLS LAST,
             snapshot.dataflow_run_id ASC NULLS LAST
) = 1
"""


# A profile is a whole-run snapshot.  Keep this projection deliberately narrow
# for the overview and configuration selectors: the configuration section
# hydrates the selected physical record through the reference below instead of
# putting nested attempts/configuration on every registry row.
_PROFILE_SNAPSHOT_DATAFLOW_COLUMNS = tuple(dict.fromkeys((
    "_source_id", "_file_uri", "_file_kind", "_record_line", "_ingested_at",
    "observed_at", "log_schema_version", "log_session_id", "job_id", "job_num",
    "job_index", "dataflow_id", "dataflow_run_id", "dataflow_name",
    "dataflow_description", "stage", "status", "start_time", "end_time",
    "duration_seconds", "operation_type", *_REGISTRY_ROUTE_COLUMNS,
)))


_RUNTIME_DATAFLOW_COLUMNS = (
    "_source_id", "_file_uri", "_file_kind", "_record_line", "_ingested_at",
    "observed_at", "log_schema_version", "log_session_id", "job_id", "job_num",
    "job_index", "dataflow_id", "dataflow_run_id", "status", "event_time",
    "source_duration_seconds", "transform_duration_seconds",
    "destination_duration_seconds", "overhead_duration_seconds",
)
_RECENT_RUN_DATAFLOW_COLUMNS = (
    "_source_id", "_file_uri", "_file_kind", "_record_line", "observed_at",
    "log_session_id", "job_id", "dataflow_id", "dataflow_run_id", "status",
    "event_time",
)


_PROFILE_SNAPSHOT_SQL = """
SELECT
    profile.*,
    profile._source_id AS latest_source_id,
    profile._file_uri AS latest_file_uri,
    profile._file_kind AS latest_file_kind,
    profile._record_line AS latest_record_line,
    profile.log_session_id AS latest_log_session_id,
    profile.dataflow_run_id AS latest_run_id,
    profile.normalized_status AS latest_run_status,
    profile.event_time AS latest_run_at,
    profile.start_time AS latest_start_time,
    profile.end_time AS latest_end_time,
    profile.duration_seconds AS latest_duration_seconds,
    profile.job_id AS latest_job_id,
    profile.job_num AS latest_job_num,
    profile.job_index AS latest_job_index
FROM filtered_dataflows profile
WHERE profile.dataflow_id IS NOT NULL
  AND TRIM(CAST(profile.dataflow_id AS VARCHAR)) <> ''
  AND CAST(profile.dataflow_id AS VARCHAR) = ?
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY profile.dataflow_id
    ORDER BY profile.event_time DESC NULLS LAST,
             TRY_CAST(profile.observed_at AS TIMESTAMPTZ) DESC NULLS LAST,
             profile._source_id ASC NULLS LAST,
             profile._file_uri ASC NULLS LAST,
             profile._record_line ASC NULLS LAST,
             profile.log_session_id ASC NULLS LAST,
             profile.dataflow_run_id ASC NULLS LAST
) = 1
"""


def _registry_filtered_ctes(
    source_ids: list[int],
    filters: dict[str, str],
    *,
    dataflow_columns: tuple[str, ...],
) -> tuple[str, list[Any]]:
    """Choose each run from retained typed observations, then apply report filters."""

    requested = set(dataflow_columns)
    if filters.get("investigateKind") == "physical_destination":
        from datacoolie_studio.domains.assets.endpoint_identity import DESTINATION_SIGNATURE_COLUMNS
        requested.update(DESTINATION_SIGNATURE_COLUMNS)
    requested.update({
        "_source_id", "_file_uri", "_file_kind", "_record_line", "_ingested_at",
        "observed_at", "log_schema_version", "log_session_id", "job_id", "dataflow_id",
        "dataflow_run_id", "status", "stage", "operation_type", "source_name",
        "source_connection_type", "destination_name", "destination_connection_type",
        "destination_load_type", "source_full_table", "source_table", "source_path",
        "destination_full_table", "destination_table", "destination_path", "dataflow_name",
        "error_message",
    })
    selected = [column for column in _DATAFLOW_COLUMNS if column in requested]
    selected.extend(
        column
        for column in ("source_error_message", "transform_error_message", "destination_error_message")
        if column in requested and column not in selected
    )
    available = set(_DATAFLOW_SOURCE_TYPES)
    projection: list[str] = []
    for column in selected:
        data_type = _DATAFLOW_SOURCE_TYPES.get(column, "VARCHAR")
        expression = field_expression(
            column,
            data_type,
            raw="observation",
            available=available,
        )
        projection.append(f"{expression} AS {quote_identifier(column)}")
    projection.extend((
        "LOWER(COALESCE(NULLIF(TRIM(CAST(observation.status AS VARCHAR)), ''), 'unknown')) AS normalized_status",
        "observation.__event_time AS event_time",
        "COALESCE(observation.__run_date, CAST(timezone('UTC', observation.__event_time) AS DATE)) AS run_date",
        "job.engine_name AS engine_name",
        "job.metadata_provider_name AS metadata_provider_name",
        "job.platform_name AS platform_name",
    ))
    ids = ", ".join("?" for _ in source_ids)
    filter_sql, filter_params = monitoring_filter_sql(
        filters,
        "canonical",
        "canonical",
        dataflow_table=analytics_schema.DATAFLOW_TABLE,
        dataflow_event_time_column="event_time",
    )
    sql = f"""
WITH all_log_versions AS (
  SELECT log_schema_version FROM {analytics_schema.DATAFLOW_TABLE}
  WHERE _source_id IN ({ids})
  UNION ALL
  SELECT log_schema_version FROM {analytics_schema.JOB_TABLE}
  WHERE _source_id IN ({ids})
), typed_dataflow_observations AS (
  SELECT {', '.join(projection)}
  FROM {analytics_schema.DATAFLOW_TABLE} observation
  LEFT JOIN {MONITORING_JOB_FACTS_TABLE} job
    ON job._source_id = observation._source_id
   AND job.log_session_id IS NOT DISTINCT FROM observation.log_session_id
   AND job.job_id = observation.job_id
  WHERE observation._source_id IN ({ids})
    AND observation.dataflow_id IS NOT NULL
    AND TRIM(CAST(observation.dataflow_id AS VARCHAR)) <> ''
    AND observation.dataflow_run_id IS NOT NULL
    AND TRIM(CAST(observation.dataflow_run_id AS VARCHAR)) <> ''
), canonical_dataflow_observations AS (
  SELECT *
  FROM typed_dataflow_observations
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY dataflow_id, dataflow_run_id
    ORDER BY TRY_CAST(observed_at AS TIMESTAMPTZ) DESC NULLS LAST,
             event_time DESC NULLS LAST,
             _source_id ASC NULLS LAST,
             _file_uri ASC NULLS LAST,
             _record_line ASC NULLS LAST,
             log_session_id ASC NULLS LAST
  ) = 1
), filtered_dataflows AS (
  SELECT canonical.*
  FROM canonical_dataflow_observations canonical
  WHERE TRUE{filter_sql}
)
"""
    return sql, [*source_ids, *source_ids, *source_ids, *filter_params]


def _effective_source_ids(source_ids: list[int], filters: dict[str, str]) -> list[int]:
    """Apply the admitted explicit source selector before canonical ranking."""

    selected = filters.get("investigateSourceId")
    if selected in (None, ""):
        return list(source_ids)
    try:
        selected_id = int(selected)
    except (TypeError, ValueError):
        return []
    return [selected_id] if selected_id in set(source_ids) else []


_PROFILE_SNAPSHOT_FIELDS = (
    "dataflow_id", "dataflow_run_id", "status", "dataflow_name", "dataflow_description",
    "event_time", "observed_at", "start_time", "end_time", "duration_seconds",
    "job_id", "log_session_id", "_source_id", "_file_uri", "_file_kind",
    "_record_line", "_ingested_at", "platform_name", "engine_name",
    "metadata_provider_name", *_REGISTRY_ROUTE_COLUMNS,
)


def _profile_snapshot(
    row: dict[str, Any] | None,
    *,
    outside_filter: bool = False,
) -> dict[str, Any] | None:
    """Project exact profile identity/route values without null backfill."""

    if row is None:
        return None
    return {
        **{field: row.get(field) for field in _PROFILE_SNAPSHOT_FIELDS},
        "outside_filter": bool(outside_filter),
    }


def _profile_run_in_filtered_population(
    conn: Any,
    source_ids: list[int],
    filters: dict[str, str],
    target_dataflow_id: str | None,
    profile_run_id: Any,
) -> bool:
    """Check all filtered canonical runs for the selected profile run ID.

    The profile itself is selected from the active filtered rows, while this
    narrow membership query preserves the current filter semantics.  It is
    deliberately one target-level query rather than one query per recent run.
    """

    if not source_ids or target_dataflow_id is None or profile_run_id is None:
        return False
    ctes, params = _registry_filtered_ctes(
        source_ids,
        filters,
        dataflow_columns=("dataflow_id", "dataflow_run_id"),
    )
    result = conn.execute(
        f"""
{ctes}
SELECT 1
FROM filtered_dataflows
WHERE CAST(dataflow_id AS VARCHAR) = ?
  AND CAST(dataflow_run_id AS VARCHAR) = ?
LIMIT 1
""",
        [*params, target_dataflow_id, profile_run_id],
    )
    return result.fetchone() is not None


def _recent_run_reference(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataflow_id": row.get("dataflow_id"),
        "dataflow_run_id": row.get("dataflow_run_id"),
        "event_time": row.get("event_time"),
        "observed_at": row.get("observed_at"),
        "status": row.get("status"),
        "job_id": row.get("job_id"),
        "log_session_id": row.get("log_session_id"),
        "_source_id": row.get("_source_id"),
        "_file_uri": row.get("_file_uri"),
        "_file_kind": row.get("_file_kind"),
        "_record_line": row.get("_record_line"),
    }


def _profile_snapshot_row(
    conn: Any,
    source_ids: list[int],
    filters: dict[str, str],
    target_dataflow_id: str | None,
) -> dict[str, Any] | None:
    """Select one whole filtered run for the detail profile/configuration."""

    if not source_ids or target_dataflow_id is None:
        return None
    ctes, params = _registry_filtered_ctes(
        source_ids,
        filters,
        dataflow_columns=_PROFILE_SNAPSHOT_DATAFLOW_COLUMNS,
    )
    result = conn.execute(f"{ctes}{_PROFILE_SNAPSHOT_SQL}", [*params, target_dataflow_id])
    profile_rows = rows(result)
    if not profile_rows:
        return None
    return profile_rows[0]


def _recent_runs(
    conn: Any,
    ctes: str,
    params: list[Any],
    target_dataflow_id: str | None,
) -> list[dict[str, Any]]:
    """Return at most five filtered canonical runs in display order."""

    if target_dataflow_id is None:
        return []
    result = conn.execute(
        f"{ctes}{_recent_runs_sql()}",
        [*params, target_dataflow_id],
    )
    return [_recent_run_reference(row) for row in rows(result)]


_REGISTRY_RUNS_CTE = """
, registry_runs AS (
    SELECT *
    FROM filtered_dataflows
    WHERE dataflow_id IS NOT NULL
      AND TRIM(CAST(dataflow_id AS VARCHAR)) <> ''
      AND dataflow_run_id IS NOT NULL
      AND TRIM(CAST(dataflow_run_id AS VARCHAR)) <> ''
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY dataflow_id, dataflow_run_id
        ORDER BY TRY_CAST(observed_at AS TIMESTAMPTZ) DESC NULLS LAST,
                 event_time DESC NULLS LAST,
                 _source_id ASC NULLS LAST,
                 _file_uri ASC NULLS LAST,
                 _record_line ASC NULLS LAST,
                 log_session_id ASC NULLS LAST
    ) = 1
)
, registry_run_metrics AS (
    SELECT
        rr.*,
        TRY_CAST(rr.source_duration_seconds AS DOUBLE) AS registry_source_duration_seconds,
        TRY_CAST(rr.transform_duration_seconds AS DOUBLE) AS registry_transform_duration_seconds,
        TRY_CAST(rr.destination_duration_seconds AS DOUBLE) AS registry_destination_duration_seconds,
        TRY_CAST(rr.overhead_duration_seconds AS DOUBLE) AS registry_overhead_duration_seconds
    FROM registry_runs rr
)
"""


def _registry_freshness_ctes(
    *, base_relation: str = "filtered_dataflows", as_of: str | None = None,
) -> str:
    """Compose the canonical Freshness owner without configuration aggregates."""
    return dataflow_registry_freshness_ctes(
        base_relation=base_relation, as_of=as_of, narrow_projection=True,
    )


def _registry_sql(as_of: str | None, facet: str = "all") -> str:
    freshness_ctes = _registry_freshness_ctes(
        base_relation="filtered_dataflows",
        as_of=as_of,
    ) if facet in {"all", "freshness"} else ""
    performance_ctes = dataflow_registry_performance_ctes() if facet in {"all", "performance"} else ""
    volume_ctes = dataflow_registry_volume_ctes() if facet in {"all", "volume"} else ""
    facet_fields = []
    facet_joins = []
    for enabled, relation, alias in (
        (bool(freshness_ctes), "registry_freshness_summary", "freshness"),
        (bool(performance_ctes), "registry_performance_by_dataflow", "performance"),
        (bool(volume_ctes), "registry_volume_by_dataflow", "volume"),
    ):
        if enabled:
            if alias == "performance":
                excluded = ', '.join(("dataflow_id", *PERFORMANCE_REGISTRY_COUNT_FIELDS))
                facet_fields.append(f", {alias}.* EXCLUDE ({excluded})")
                facet_fields.extend(
                    f", COALESCE({alias}.{field}, 0) AS {field}"
                    for field in PERFORMANCE_REGISTRY_COUNT_FIELDS
                )
            else:
                facet_fields.append(f", {alias}.* EXCLUDE (dataflow_id)")
            facet_joins.append(f"LEFT JOIN {relation} {alias} USING (dataflow_id)")
    return f"""
{freshness_ctes}
{_REGISTRY_RUNS_CTE.split(', registry_run_metrics AS (')[0]}
{performance_ctes}
{volume_ctes}
, registry_latest AS (
    SELECT *
    FROM registry_runs
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY dataflow_id
        ORDER BY event_time DESC NULLS LAST,
                 TRY_CAST(observed_at AS TIMESTAMPTZ) DESC NULLS LAST,
                 _source_id ASC NULLS LAST,
                 _file_uri ASC NULLS LAST,
                 _record_line ASC NULLS LAST,
                 log_session_id ASC NULLS LAST,
                 dataflow_run_id ASC NULLS LAST
    ) = 1
), registry_aggregate AS (
    SELECT
        dataflow_id,
        COUNT(*) AS run_count,
        COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded_count,
        COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed_count,
        COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped_count,
        COUNT(*) FILTER (WHERE normalized_status = 'running') AS running_count,
        COUNT(*) FILTER (WHERE normalized_status = 'pending') AS pending_count,
        COUNT(*) FILTER (WHERE normalized_status NOT IN (
          'succeeded', 'failed', 'skipped', 'running', 'pending'
        )) AS other_count
    FROM registry_runs
    GROUP BY dataflow_id
), registry AS (
    SELECT
        l.dataflow_id,
        l._source_id AS latest_source_id,
        l._file_uri AS latest_file_uri,
        l._file_kind AS latest_file_kind,
        l._record_line AS latest_record_line,
        l.log_session_id AS latest_log_session_id,
        l.job_id AS latest_job_id,
        l.job_num AS latest_job_num,
        l.job_index AS latest_job_index,
        l.dataflow_run_id AS latest_run_id,
        COALESCE(NULLIF(l.dataflow_name, ''), l.dataflow_id) AS dataflow_name,
        l.dataflow_description,
        COALESCE(NULLIF(l.stage, ''), 'unknown') AS stage,
        l.platform_name,
        l.engine_name,
        l.metadata_provider_name,
        l.normalized_status AS latest_run_status,
        l.normalized_status AS status,
        l.event_time AS latest_run_at,
        l.start_time AS latest_start_time,
        l.end_time AS latest_end_time,
        l.duration_seconds AS latest_duration_seconds,
        l.start_time AS start_time,
        l.end_time AS end_time,
        l.duration_seconds AS duration_seconds,
        l.message AS latest_message,
        l.error_message AS latest_error_message,
        l.message AS message,
        l.error_message AS error_message,
        l.operation_type AS latest_operation_type,
        l.operation_type,
        l.destination_operation_type,
        l.destination_load_type,
        l.group_number,
        l.execution_order,
        l.log_schema_version,
        l.source_name,
        l.source_connection_type,
        l.source_format,
        l.source_full_table,
        l.source_table,
        l.source_path,
        l.source_query,
        l.source_python_function,
        l.destination_name,
        l.destination_connection_type,
        l.destination_format,
        l.destination_full_table,
        l.destination_table,
        l.destination_path,
        l.job_id,
        a.* EXCLUDE (dataflow_id)
        {''.join(facet_fields)}
    FROM registry_aggregate a
    JOIN registry_latest l USING (dataflow_id)
    {' '.join(facet_joins)}
)
SELECT * FROM registry
"""

_EXECUTION_SQL = f"""
{_REGISTRY_RUNS_CTE}
SELECT
    _source_id, _file_uri, _file_kind, _record_line, _ingested_at,
    observed_at, log_schema_version, log_session_id, job_id, job_num, job_index,
    dataflow_id, dataflow_run_id, COALESCE(NULLIF(dataflow_name, ''), dataflow_id) AS dataflow_name,
    dataflow_description, platform_name, engine_name, metadata_provider_name,
    COALESCE(NULLIF(stage, ''), 'unknown') AS stage,
    normalized_status AS status, normalized_status AS latest_run_status,
    message, error_message, event_time, start_time, end_time, duration_seconds,
    operation_type, source_name, source_connection_type, source_format,
    source_full_table, source_table, source_path, source_rows_read,
    source_status, transform_status, destination_name, destination_connection_type,
    destination_format, destination_full_table, destination_table, destination_path,
    destination_load_type, destination_status,
    destination_rows_written, destination_rows_inserted, destination_rows_updated,
    destination_rows_deleted, destination_bytes_added, destination_bytes_removed,
    destination_files_added, destination_files_removed,
    registry_source_duration_seconds AS source_duration_seconds,
    registry_transform_duration_seconds AS transform_duration_seconds,
    registry_destination_duration_seconds AS destination_duration_seconds,
    registry_overhead_duration_seconds AS overhead_duration_seconds,
    CASE WHEN registry_source_duration_seconds IS NULL
              AND registry_transform_duration_seconds IS NULL
              AND registry_destination_duration_seconds IS NULL
              AND registry_overhead_duration_seconds IS NULL THEN NULL
         WHEN COALESCE(registry_source_duration_seconds, 0) = GREATEST(COALESCE(registry_source_duration_seconds, 0), COALESCE(registry_transform_duration_seconds, 0), COALESCE(registry_destination_duration_seconds, 0), COALESCE(registry_overhead_duration_seconds, 0)) THEN 'source'
         WHEN COALESCE(registry_transform_duration_seconds, 0) = GREATEST(COALESCE(registry_source_duration_seconds, 0), COALESCE(registry_transform_duration_seconds, 0), COALESCE(registry_destination_duration_seconds, 0), COALESCE(registry_overhead_duration_seconds, 0)) THEN 'transform'
         WHEN COALESCE(registry_destination_duration_seconds, 0) = GREATEST(COALESCE(registry_source_duration_seconds, 0), COALESCE(registry_transform_duration_seconds, 0), COALESCE(registry_destination_duration_seconds, 0), COALESCE(registry_overhead_duration_seconds, 0)) THEN 'destination'
         ELSE 'overhead' END AS phase_health
FROM registry_run_metrics
"""


def _runtime_sql(target_dataflow_id: str | None) -> tuple[str, list[str]]:
    """Build one narrow grouped population for the runtime detail section.

    The ranked rows retain the exact canonical observation selected for each
    run.  Aggregating the ranked population with conditional ``MAX`` values
    keeps the latest drilldown reference attached to the same tuple without a
    per-group lookup or a null-unsafe join.
    """

    target_sql = ""
    target_params: list[str] = []
    if target_dataflow_id is not None:
        target_sql = " AND CAST(rr.dataflow_id AS VARCHAR) = ?"
        target_params.append(target_dataflow_id)
    return f"""
{_REGISTRY_RUNS_CTE}
, runtime_ranked AS (
    SELECT
        rr.*,
        ROW_NUMBER() OVER (
            PARTITION BY rr.platform_name, rr.engine_name, rr.metadata_provider_name
            ORDER BY rr.event_time DESC NULLS LAST,
                     TRY_CAST(rr.observed_at AS TIMESTAMPTZ) DESC NULLS LAST,
                     rr._source_id ASC NULLS LAST,
                     rr._file_uri ASC NULLS LAST,
                     rr._record_line ASC NULLS LAST,
                     rr.log_session_id ASC NULLS LAST,
                     rr.dataflow_run_id ASC NULLS LAST
        ) AS runtime_rank
    FROM registry_runs rr
    WHERE TRUE{target_sql}
), runtime_grouped AS (
    SELECT
        dataflow_id,
        platform_name,
        engine_name,
        metadata_provider_name,
        COUNT(*) AS run_count,
        MAX(CASE WHEN runtime_rank = 1 THEN event_time END) AS latest_run_at,
        MAX(CASE WHEN runtime_rank = 1 THEN dataflow_run_id END) AS latest_run_id,
        MAX(CASE WHEN runtime_rank = 1 THEN job_id END) AS latest_job_id,
        MAX(CASE WHEN runtime_rank = 1 THEN log_session_id END) AS latest_log_session_id,
        MAX(CASE WHEN runtime_rank = 1 THEN _source_id END) AS latest_source_id,
        MAX(CASE WHEN runtime_rank = 1 THEN _file_uri END) AS latest_file_uri,
        MAX(CASE WHEN runtime_rank = 1 THEN _file_kind END) AS latest_file_kind,
        MAX(CASE WHEN runtime_rank = 1 THEN _record_line END) AS latest_record_line
    FROM runtime_ranked
    GROUP BY dataflow_id, platform_name, engine_name, metadata_provider_name
)
SELECT * FROM runtime_grouped
""", target_params


def _recent_runs_sql() -> str:
    """Return the bounded latest-five query over the filtered run population."""

    return f"""
{_REGISTRY_RUNS_CTE}
, recent_ranked AS (
    SELECT
        rr.*,
        ROW_NUMBER() OVER (
            PARTITION BY rr.dataflow_id
            ORDER BY rr.event_time DESC NULLS LAST,
                     TRY_CAST(rr.observed_at AS TIMESTAMPTZ) DESC NULLS LAST,
                     rr._source_id ASC NULLS LAST,
                     rr._file_uri ASC NULLS LAST,
                     rr._record_line ASC NULLS LAST,
                     rr.log_session_id ASC NULLS LAST,
                     rr.dataflow_run_id ASC NULLS LAST
        ) AS recent_rank
    FROM registry_runs rr
    WHERE CAST(rr.dataflow_id AS VARCHAR) = ?
)
SELECT
    dataflow_id,
    dataflow_run_id,
    event_time,
    observed_at,
    normalized_status AS status,
    job_id,
    log_session_id,
    _source_id,
    _file_uri,
    _file_kind,
    _record_line
FROM recent_ranked
WHERE recent_rank <= 5
ORDER BY recent_rank DESC
"""


_REGISTRY_SORT_COLUMNS = {
    "dataflow_name": "dataflow_name",
    "latest_job_id": "latest_job_id",
    "latest_run_at": "latest_run_at",
    "latest_run_status": "latest_run_status",
    "status": "latest_run_status",
    "message": "latest_message",
    "stage": "stage",
    "source_name": "source_name",
    "destination_name": "destination_name",
    "destination_load_type": "destination_load_type",
    "duration_seconds": "latest_duration_seconds",
    "run_count": "run_count",
    "volume_rows_read": "volume_rows_read",
    "volume_rows_written": "volume_rows_written",
    "volume_rows_inserted": "volume_rows_inserted",
    "volume_files_added": "volume_files_added",
    "volume_files_changed": "volume_files_changed",
    "volume_bytes_added": "volume_bytes_added",
    "volume_net_bytes": "volume_net_bytes",
    "volume_candidate_priority": "volume_candidate_priority",
    "p95_duration_seconds": "performance_p95_duration_seconds",
    "performance_avg_duration_seconds": "performance_avg_duration_seconds",
    "performance_total_run_time_seconds": "performance_total_run_time_seconds",
    "performance_candidate_count": "performance_candidate_count",
    "performance_bottleneck_phase": "performance_bottleneck_phase",
    "latest_freshness_at": "latest_freshness_at",
    "age_days": "age_days",
    "movement_state": "movement_state",
    "latest_success_watermark": "latest_success_watermark_sort_value",
}


def dataflow_registry_sort_fields(facet: str) -> frozenset[str]:
    """Sort only fields provided by the selected compact facet."""
    excluded = {
        "freshness": {"p95_duration_seconds", "performance_bottleneck_phase"},
        "performance": {"latest_freshness_at", "age_days", "movement_state", "latest_success_watermark"},
        "volume": {"p95_duration_seconds", "performance_bottleneck_phase", "latest_freshness_at", "age_days", "movement_state", "latest_success_watermark"},
    }.get(facet, set())
    if facet not in {"all", "performance"}:
        excluded = excluded | {
            "performance_avg_duration_seconds", "performance_total_run_time_seconds",
            "performance_candidate_count",
        }
    return frozenset(
        name for name in _REGISTRY_SORT_COLUMNS
        if name not in excluded and (facet in {"all", "volume"} or not name.startswith("volume_"))
    )


def dataflow_registry_read_model(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    *,
    limit: int,
    offset: int,
    sort_by: str,
    sort_dir: str,
    section: str = "overview",
    facet: str = "all",
    target_dataflow_id: str | None = None,
    analytics_context: AnalyticsContext | None = None,
) -> dict[str, Any]:
    """Return one compact row per environment dataflow.

    The environment is supplied by the API route; the analytics relation only
    contains source provenance. Freshness classification and recorded saves are
    evaluated from the same canonical run relation as the registry metrics.
    The merge is limited to the entity IDs on the requested page.
    Supplied contexts own their snapshot and prepare Freshness functions before
    BEGIN; registry-owned readers perform that setup through Monitoring context.
    """

    valid_sections = {"overview", "execution", "runtime", "read-watermark", "configuration", "technical"}
    if section not in valid_sections:
        raise ValueError(f"Unsupported dataflow registry section: {section}")
    if facet not in {"all", "freshness", "performance", "volume"}:
        raise ValueError(f"Unsupported dataflow registry facet: {facet}")
    if target_dataflow_id is not None:
        facet = "all"
    if section == "overview" and target_dataflow_id is None and sort_by not in dataflow_registry_sort_fields(facet):
        raise ValueError(f"Unsupported dataflow registry sort for {facet}: {sort_by}")


    order_column = _REGISTRY_SORT_COLUMNS.get(sort_by, "latest_run_at")
    # Sort the same observed subtotal shown by the Volume registry/drawer.
    if facet in {"all", "volume"} and sort_by in {
        "volume_rows_read", "volume_rows_written", "volume_rows_inserted",
        "volume_files_added", "volume_bytes_added", "volume_net_bytes",
    }:
        order_column = f"COALESCE({sort_by}, CASE WHEN {sort_by}_measured > 0 THEN {sort_by}_observed END)"
    direction = "ASC" if sort_dir == "asc" else "DESC"

    with reader_context(paths, analytics_context, snapshot=True) as (conn, source_ids, generation):
        source_ids = _effective_source_ids(source_ids, filters)
        if conn is None or not source_ids:
            return {"generation": generation, "records": [], "total_records": 0}
        requested_columns = (
            _EXECUTION_DATAFLOW_COLUMNS if section == "execution"
            else _RUNTIME_DATAFLOW_COLUMNS if section == "runtime"
            else _REGISTRY_OVERVIEW_COLUMNS if section == "overview"
            else _REGISTRY_DATAFLOW_COLUMNS
        )
        if section == "overview" and facet != "all":
            requested_columns = tuple(dict.fromkeys((
                *_REGISTRY_LIST_COLUMNS,
                *({
                    "freshness": _FRESHNESS_DATAFLOW_COLUMNS,
                    "performance": PERFORMANCE_REGISTRY_INPUT_COLUMNS,
                    "volume": VOLUME_REGISTRY_INPUT_COLUMNS,
                }[facet]),
            )))
        ctes, params = _registry_filtered_ctes(
            source_ids,
            filters,
            dataflow_columns=requested_columns,
        )
        if section == "runtime":
            runtime_sql, runtime_scope_params = _runtime_sql(target_dataflow_id)
            runtime_sort_columns = {
                "platform_name": "platform_name",
                "engine_name": "engine_name",
                "metadata_provider_name": "metadata_provider_name",
                "run_count": "run_count",
                "latest_run_at": "latest_run_at",
            }
            if sort_by not in runtime_sort_columns or sort_dir not in {"asc", "desc"}:
                raise ValueError("Unsupported dataflow registry runtime sort")
            runtime_order_column = runtime_sort_columns[sort_by]
            runtime_direction = sort_dir.upper()
            runtime_query = (
                f"SELECT runtime.*, COUNT(*) OVER () AS __total_records "
                f"FROM ({ctes}{runtime_sql}) runtime "
                f"ORDER BY {runtime_order_column} {runtime_direction} NULLS LAST, "
                "latest_run_at DESC NULLS LAST, platform_name ASC NULLS LAST, "
                "engine_name ASC NULLS LAST, metadata_provider_name ASC NULLS LAST, "
                "latest_run_id ASC NULLS LAST, latest_source_id ASC NULLS LAST, "
                "latest_file_uri ASC NULLS LAST, latest_record_line ASC NULLS LAST "
                "LIMIT ? OFFSET ?"
            )
            runtime_records, runtime_total = paged_rows(conn.execute(
                runtime_query,
                [*params, *runtime_scope_params, limit, offset],
            ))
            if not runtime_records and offset > 0:
                runtime_total = int(conn.execute(
                    f"SELECT COUNT(*) FROM ({ctes}{runtime_sql}) runtime",
                    [*params, *runtime_scope_params],
                ).fetchone()[0])
            return {
                "generation": generation,
                "records": runtime_records,
                "total_records": runtime_total,
            }
        if section == "execution":
            execution_sort_columns = {
                "event_time": "event_time",
                "latest_run_at": "event_time",
                "dataflow_run_id": "dataflow_run_id",
                "stage": "stage",
                "duration_seconds": "duration_seconds",
                "status": "normalized_status",
                "phase_health": "phase_health",
                "source_rows_read": "source_rows_read",
                "destination_rows_written": "destination_rows_written",
                "message": "message",
            }
            if sort_by not in execution_sort_columns or sort_dir not in {"asc", "desc"}:
                raise ValueError("Unsupported dataflow registry execution sort")
            execution_order_column = execution_sort_columns[sort_by]
            execution_direction = sort_dir.upper()
            execution_sql = f"SELECT execution.*, COUNT(*) OVER () AS __total_records FROM ({ctes}{_EXECUTION_SQL}) execution"
            execution_params = list(params)
            scope_sql = ""
            if target_dataflow_id is not None:
                scope_sql = " WHERE CAST(execution.dataflow_id AS VARCHAR) = ?"
                execution_params.append(target_dataflow_id)
            execution_records, execution_total = paged_rows(conn.execute(
                f"{execution_sql}{scope_sql} "
                f"ORDER BY {execution_order_column} {execution_direction} NULLS LAST, "
                "event_time DESC NULLS LAST, _source_id ASC NULLS LAST, "
                "_file_uri ASC NULLS LAST, _record_line ASC NULLS LAST, "
                "dataflow_run_id ASC NULLS LAST LIMIT ? OFFSET ?",
                [*execution_params, limit, offset],
            ))
            if not execution_records and offset > 0:
                count_scope = " WHERE CAST(execution.dataflow_id AS VARCHAR) = ?" if target_dataflow_id is not None else ""
                count_params = [*params, *([target_dataflow_id] if target_dataflow_id is not None else [])]
                execution_total = int(conn.execute(
                    f"SELECT COUNT(*) FROM ({ctes}{_EXECUTION_SQL}) execution{count_scope}",
                    count_params,
                ).fetchone()[0])
            return {
                "generation": generation,
                "records": execution_records,
                "total_records": execution_total,
            }
        if section == "configuration":
            profile_row = _profile_snapshot_row(
                conn,
                source_ids,
                filters,
                target_dataflow_id,
            )
            if profile_row is None:
                return {"generation": generation, "records": [], "total_records": 0}
            profile_row["profile_snapshot"] = _profile_snapshot(
                profile_row,
                outside_filter=not _profile_run_in_filtered_population(
                    conn,
                    source_ids,
                    filters,
                    target_dataflow_id,
                    profile_row.get("dataflow_run_id"),
                ),
            )
            return {
                "generation": generation,
                "records": [profile_row],
                "total_records": 1,
            }
        if section == "overview":
            with timed_stage("overview_sql_build"):
                result_sql = _registry_sql(filters.get("_asOf"), facet)
        else:
            result_sql = _DETAIL_SNAPSHOT_SQL
        scoped_sql = ""
        result_params = list(params)
        if target_dataflow_id is not None:
            scoped_sql = " WHERE CAST(registry.dataflow_id AS VARCHAR) = ?"
            result_params.append(target_dataflow_id)
        if section == "overview":
            result_order = f"ORDER BY {order_column} {direction} NULLS LAST, registry.dataflow_id ASC"
            registry_projection = (
                "registry.* EXCLUDE (latest_success_watermark_sort_value)"
                if facet in {"all", "freshness"} else "registry.*"
            )
        else:
            result_order = "ORDER BY registry.latest_run_at DESC NULLS LAST, registry.dataflow_id ASC"
            registry_projection = "registry.*"
        records, total = paged_rows(conn.execute(
            f"SELECT {registry_projection}, COUNT(*) OVER () AS __total_records "
            f"FROM ({ctes}{result_sql}) registry{scoped_sql} "
            f"{result_order} LIMIT ? OFFSET ?",
            [*result_params, limit, offset],
        ))
        if not records and offset > 0:
            count_scope = " WHERE CAST(registry.dataflow_id AS VARCHAR) = ?" if target_dataflow_id is not None else ""
            count_params = [*params, *([target_dataflow_id] if target_dataflow_id is not None else [])]
            total = int(conn.execute(
                f"SELECT COUNT(*) FROM ({ctes}{result_sql}) registry{count_scope}",
                count_params,
            ).fetchone()[0])

        page_ids = [str(row.get("dataflow_id")) for row in records if row.get("dataflow_id") is not None]
        include_last_saved = (
            section in {"overview", "read-watermark"} and
            (target_dataflow_id is not None or _is_entity_detail(filters))
        )
        last_saved = _saved_watermark_rows(
            conn, source_ids, filters, page_ids,
        ) if include_last_saved else {}
        saved_outside_scope = (
            _saved_watermark_outside_filter(conn, source_ids, filters, last_saved)
            if include_last_saved and last_saved else {}
        )
        technical = _technical_reference_rows(
            conn, source_ids, filters, page_ids,
        ) if section == "technical" else {}
        profile_row = (
            _profile_snapshot_row(conn, source_ids, filters, target_dataflow_id)
            if section == "overview" and target_dataflow_id is not None
            else None
        )
        profile_outside_filter = (
            not _profile_run_in_filtered_population(
                conn,
                source_ids,
                filters,
                target_dataflow_id,
                profile_row.get("dataflow_run_id"),
            )
            if profile_row is not None
            else False
        )
        if section == "overview" and target_dataflow_id is not None:
            # Recent status references do not need the facet/configuration
            # projection used by the overview row. Keep this bounded history
            # query on a narrow canonical identity relation.
            recent_ctes, recent_params = _registry_filtered_ctes(
                source_ids,
                filters,
                dataflow_columns=_RECENT_RUN_DATAFLOW_COLUMNS,
            )
            recent_runs = _recent_runs(
                conn,
                recent_ctes,
                recent_params,
                target_dataflow_id,
            )
        else:
            recent_runs = []
    for row in records:
        dataflow_id = str(row.get("dataflow_id"))
        if section == "overview" and target_dataflow_id is not None:
            row["profile_snapshot"] = _profile_snapshot(
                profile_row,
                outside_filter=profile_outside_filter,
            )
            row["recent_runs"] = recent_runs
        if include_last_saved:
            saved = last_saved.get(dataflow_id)
            row.update({
                "last_saved_watermark": saved.get("last_saved_watermark") if saved else None,
                "last_saved_watermark_at": saved.get("last_saved_watermark_at") if saved else None,
                "last_saved_watermark_run_id": saved.get("last_saved_watermark_run_id") if saved else None,
                "last_saved_watermark_source_id": saved.get("last_saved_watermark_source_id") if saved else None,
                "last_saved_watermark_file_uri": saved.get("last_saved_watermark_file_uri") if saved else None,
                "last_saved_watermark_record_line": saved.get("last_saved_watermark_record_line") if saved else None,
                "last_saved_watermark_scope": "filtered" if saved else None,
                "last_saved_watermark_outside_filter": saved_outside_scope.get(dataflow_id),
            })
        if section == "technical":
            row.update(technical.get(dataflow_id, _empty_technical_references()))
    return {"generation": generation, "records": records, "total_records": total}


def _is_entity_detail(filters: dict[str, str]) -> bool:
    return (
        filters.get("investigateKind") == "dataflow_id"
        and bool(filters.get("investigateValue"))
    )


def _saved_watermark_rows(
    conn: Any,
    source_ids: list[int],
    filters: dict[str, str],
    dataflow_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Select each entity's last recorded final-attempt save in current filters."""

    if not dataflow_ids:
        return {}
    ctes, params = _registry_filtered_ctes(
        source_ids, filters, dataflow_columns=_SAVED_WATERMARK_DATAFLOW_COLUMNS,
    )
    placeholders = ", ".join("?" for _ in dataflow_ids)
    query = f"""
{ctes}, saved_watermark_candidates AS (
  SELECT dataflow_id, dataflow_run_id, _source_id, _file_uri, _record_line,
         log_session_id, observed_at, event_time AS last_saved_watermark_at,
         NULLIF(CAST(attempts[-1].watermark.saved_watermark AS VARCHAR), 'null') AS last_saved_watermark
  FROM filtered_dataflows
  WHERE CAST(dataflow_id AS VARCHAR) IN ({placeholders})
    AND attempts IS NOT NULL AND len(attempts) > 0
    AND LOWER(COALESCE(CAST(attempts[-1].watermark.save_status AS VARCHAR), '')) = 'saved'
    AND NULLIF(CAST(attempts[-1].watermark.saved_watermark AS VARCHAR), 'null') IS NOT NULL
), latest_saved_watermark AS (
  SELECT *
  FROM saved_watermark_candidates
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY dataflow_id
    ORDER BY last_saved_watermark_at DESC NULLS LAST,
             TRY_CAST(observed_at AS TIMESTAMPTZ) DESC NULLS LAST,
             dataflow_run_id ASC NULLS LAST,
             _source_id ASC NULLS LAST,
             _file_uri ASC NULLS LAST,
             _record_line ASC NULLS LAST,
             log_session_id ASC NULLS LAST
  ) = 1
)
SELECT dataflow_id, last_saved_watermark, last_saved_watermark_at,
       dataflow_run_id AS last_saved_watermark_run_id,
       _source_id AS last_saved_watermark_source_id,
       _file_uri AS last_saved_watermark_file_uri,
       _record_line AS last_saved_watermark_record_line
FROM latest_saved_watermark
"""
    result = conn.execute(query, [*params, *dataflow_ids])
    return {str(row["dataflow_id"]): row for row in rows(result)}


def _saved_watermark_outside_filter(
    conn: Any,
    source_ids: list[int],
    filters: dict[str, str],
    saved_rows: dict[str, dict[str, Any]],
) -> dict[str, bool]:
    """Check selected save references against the active comparison scope."""

    selected = [
        (dataflow_id, str(row.get("last_saved_watermark_run_id") or ""))
        for dataflow_id, row in saved_rows.items()
        if row.get("last_saved_watermark_run_id")
    ]
    if not selected:
        return {}
    ctes, params = _registry_filtered_ctes(
        source_ids, filters, dataflow_columns=_SAVED_WATERMARK_DATAFLOW_COLUMNS,
    )
    result: dict[str, bool] = {}
    for dataflow_id, run_id in selected:
        query = f"""
{ctes}
SELECT EXISTS (
  SELECT 1 FROM filtered_dataflows
  WHERE CAST(dataflow_id AS VARCHAR) = ?
    AND CAST(dataflow_run_id AS VARCHAR) = ?
)
"""
        is_in_scope = bool(conn.execute(query, [*params, dataflow_id, run_id]).fetchone()[0])
        result[dataflow_id] = not is_in_scope
    return result


def _technical_reference_rows(
    conn: Any,
    source_ids: list[int],
    filters: dict[str, str],
    dataflow_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Return a deterministic, bounded physical-reference sample per entity."""

    if not dataflow_ids:
        return {}
    ctes, params = _registry_filtered_ctes(
        source_ids, filters, dataflow_columns=_TECHNICAL_DATAFLOW_COLUMNS,
    )
    placeholders = ", ".join("?" for _ in dataflow_ids)
    page_scope = f", page_filtered_dataflows AS (SELECT * FROM filtered_dataflows WHERE CAST(dataflow_id AS VARCHAR) IN ({placeholders})), page_run_keys AS (SELECT dataflow_id, dataflow_run_id FROM page_filtered_dataflows)"
    query = f"""
{ctes}{page_scope}{_REGISTRY_RUNS_CTE}, physical_references AS (
    SELECT
        observation.dataflow_id,
        observation.dataflow_run_id,
        observation._source_id,
        observation._file_uri,
        observation._file_kind,
        observation._record_line,
        observation._ingested_at,
        observation.observed_at,
        observation.event_time,
        observation.log_session_id,
        (
          observation._source_id IS NOT DISTINCT FROM rr._source_id
          AND observation._file_uri IS NOT DISTINCT FROM rr._file_uri
          AND observation._record_line IS NOT DISTINCT FROM rr._record_line
          AND observation.log_session_id IS NOT DISTINCT FROM rr.log_session_id
        ) AS is_canonical,
        TO_JSON(STRUCT_PACK(
          dataflow_name := observation.dataflow_name,
          dataflow_description := observation.dataflow_description,
          event_time := observation.event_time,
          start_time := observation.start_time,
          end_time := observation.end_time,
          duration_seconds := observation.duration_seconds,
          log_schema_version := observation.log_schema_version,
          workspace_id := observation.workspace_id,
          datacoolie_version := observation.datacoolie_version,
          platform_name := observation.platform_name,
          engine_name := observation.engine_name,
          metadata_provider_name := observation.metadata_provider_name,
          job_id := observation.job_id,
          job_num := observation.job_num,
          job_index := observation.job_index,
          stage := observation.stage,
          group_number := observation.group_number,
          execution_order := observation.execution_order,
          processing_mode := observation.processing_mode,
          is_active := observation.is_active,
          status := observation.normalized_status,
          operation_type := observation.operation_type,
          destination_operation_type := observation.destination_operation_type,
          message := observation.message,
          error_message := observation.error_message,
          source_error_message := observation.source_error_message,
          transform_error_message := observation.transform_error_message,
          destination_error_message := observation.destination_error_message,
          source_name := observation.source_name,
          source_id := observation.source_id,
          source_connection_type := observation.source_connection_type,
          source_format := observation.source_format,
          source_catalog := observation.source_catalog,
          source_database := observation.source_database,
          source_schema := observation.source_schema,
          source_full_table := observation.source_full_table,
          source_table := observation.source_table,
          source_path := observation.source_path,
          source_filter_expression := observation.source_filter_expression,
          source_action := observation.source_action,
          source_status := observation.source_status,
          source_rows_read := observation.source_rows_read,
          source_duration_seconds := observation.source_duration_seconds,
          transform_status := observation.transform_status,
          transform_duration_seconds := observation.transform_duration_seconds,
          transform_filter_expression := observation.transform_filter_expression,
          destination_name := observation.destination_name,
          destination_id := observation.destination_id,
          destination_connection_type := observation.destination_connection_type,
          destination_format := observation.destination_format,
          destination_catalog := observation.destination_catalog,
          destination_database := observation.destination_database,
          destination_schema := observation.destination_schema,
          destination_full_table := observation.destination_full_table,
          destination_table := observation.destination_table,
          destination_path := observation.destination_path,
          destination_load_type := observation.destination_load_type,
          destination_status := observation.destination_status,
          destination_duration_seconds := observation.destination_duration_seconds,
          destination_rows_written := observation.destination_rows_written,
          destination_rows_inserted := observation.destination_rows_inserted,
          destination_rows_updated := observation.destination_rows_updated,
          destination_rows_deleted := observation.destination_rows_deleted,
          destination_bytes_added := observation.destination_bytes_added,
          destination_bytes_removed := observation.destination_bytes_removed,
          destination_bytes_saved := observation.destination_bytes_saved,
          destination_files_added := observation.destination_files_added,
          destination_files_removed := observation.destination_files_removed,
          overhead_duration_seconds := observation.overhead_duration_seconds
        )) AS scalar_signature,
        ROW_NUMBER() OVER (
          PARTITION BY observation.dataflow_id
          ORDER BY observation.event_time DESC NULLS LAST,
                   TRY_CAST(observation.observed_at AS TIMESTAMPTZ) DESC NULLS LAST,
                   observation.dataflow_run_id ASC NULLS LAST,
                   observation._source_id ASC NULLS LAST,
                   observation._file_uri ASC NULLS LAST,
                   observation._record_line ASC NULLS LAST,
                   observation.log_session_id ASC NULLS LAST
        ) AS reference_rank
    FROM typed_dataflow_observations observation
    JOIN page_run_keys USING (dataflow_id, dataflow_run_id)
    JOIN registry_runs rr
      ON rr.dataflow_id = observation.dataflow_id
     AND rr.dataflow_run_id = observation.dataflow_run_id
), run_copy_conflicts AS (
    SELECT dataflow_id, dataflow_run_id,
           COUNT(DISTINCT scalar_signature) > 1 AS has_scalar_conflict
    FROM physical_references
    GROUP BY dataflow_id, dataflow_run_id
), physical_references_with_conflicts AS (
    SELECT reference.*, conflicts.has_scalar_conflict
    FROM physical_references reference
    JOIN run_copy_conflicts conflicts USING (dataflow_id, dataflow_run_id)
)
SELECT
    dataflow_id,
    COUNT(*) AS technical_reference_total,
    COUNT(*) FILTER (WHERE reference_rank <= 20) AS technical_reference_inspected,
    COUNT(*) <= 20 AS technical_reference_complete,
    COUNT(DISTINCT dataflow_run_id) FILTER (WHERE has_scalar_conflict) AS technical_reference_conflicting_run_count,
    '{_TECHNICAL_REFERENCE_COMPARISON_BASIS}' AS technical_reference_comparison_basis,
    LIST(STRUCT_PACK(
      dataflow_run_id := dataflow_run_id,
      source_id := _source_id,
      file_uri := _file_uri,
      file_kind := _file_kind,
      record_line := _record_line,
      log_session_id := log_session_id,
      observed_at := observed_at,
      event_time := event_time,
      is_canonical := is_canonical,
      scalar_conflict := has_scalar_conflict
    ) ORDER BY reference_rank) FILTER (WHERE reference_rank <= 20) AS technical_references
FROM physical_references_with_conflicts
GROUP BY dataflow_id
"""
    return {
        str(row["dataflow_id"]): row
        for row in rows(conn.execute(query, [*params, *dataflow_ids]))
    }


def _empty_technical_references() -> dict[str, Any]:
    return {
        "technical_reference_total": 0,
        "technical_reference_inspected": 0,
        "technical_reference_complete": True,
        "technical_reference_conflicting_run_count": 0,
        "technical_reference_comparison_basis": _TECHNICAL_REFERENCE_COMPARISON_BASIS,
        "technical_references": [],
    }

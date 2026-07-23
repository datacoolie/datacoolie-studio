from __future__ import annotations

from typing import Any, Literal

from datacoolie_studio.domains.monitoring.read_models.common import (
    rows,
    sorted_list_percentile,
)


_DATAFLOW_GROUP_COLUMNS = {"stage", "operation_type"}
_JOB_GROUP_COLUMNS = {"operation_types"}
DurationFactKind = Literal["dataflow", "job"]


def duration_distribution(
    conn: Any,
    ctes: str,
    params: list[Any],
    *,
    group_column: str,
    output_key: str,
    limit: int,
    eligible_statuses: tuple[str, ...] = ("succeeded", "failed", "skipped"),
    fact_kind: DurationFactKind = "dataflow",
) -> list[dict[str, Any]]:
    groups = rows(conn.execute(
        f"{ctes} {duration_group_query(group_column, eligible_statuses, fact_kind=fact_kind)}",
        [*params, limit],
    ))
    return duration_distribution_from_groups(
        conn,
        ctes,
        params,
        groups=groups,
        group_column=group_column,
        output_key=output_key,
        eligible_statuses=eligible_statuses,
        fact_kind=fact_kind,
    )


def duration_group_query(
    group_column: str,
    eligible_statuses: tuple[str, ...],
    *,
    fact_kind: DurationFactKind = "dataflow",
    standalone: bool = False,
) -> str:
    query = _GROUP_QUERY.format(**_sql_values(group_column, eligible_statuses, fact_kind))
    if not standalone:
        return query
    nested = query.lstrip()
    return f"WITH{nested[1:]}" if nested.startswith(",") else nested


def duration_distribution_from_groups(
    conn: Any,
    ctes: str,
    params: list[Any],
    *,
    groups: list[dict[str, Any]],
    group_column: str,
    output_key: str,
    eligible_statuses: tuple[str, ...],
    fact_kind: DurationFactKind = "dataflow",
) -> list[dict[str, Any]]:
    if not groups:
        return []
    sql_values = _sql_values(group_column, eligible_statuses, fact_kind)
    selected_values = [
        value
        for group in groups
        for value in (
            group.get("group_value") or "unknown",
            float(group.get("q1_duration_seconds") or 0),
            float(group.get("q3_duration_seconds") or 0),
        )
    ]
    selected_placeholders = ", ".join("(?, ?, ?)" for _ in groups)
    selected_groups_sql = (
        "selected_groups(group_value, q1, q3) AS "
        f"(VALUES {selected_placeholders})"
    )
    selected_params = [*params, *selected_values]
    outliers = rows(conn.execute(
        f"{ctes}, {selected_groups_sql} {_OUTLIER_QUERY.format(**sql_values)}",
        selected_params,
    ))
    operation_mix = rows(conn.execute(
        f"{ctes}, {selected_groups_sql} {_MIX_QUERY.format(**sql_values)}",
        selected_params,
    ))
    return _assemble(groups, outliers, operation_mix, output_key, fact_kind)


def _sql_values(
    group_column: str,
    eligible_statuses: tuple[str, ...],
    fact_kind: DurationFactKind,
) -> dict[str, str]:
    if fact_kind not in {"dataflow", "job"}:
        raise ValueError(f"Unsupported duration distribution fact kind: {fact_kind}")
    allowed_columns = _JOB_GROUP_COLUMNS if fact_kind == "job" else _DATAFLOW_GROUP_COLUMNS
    if group_column not in allowed_columns:
        raise ValueError(f"Unsupported duration distribution group: {group_column}")
    if not eligible_statuses or any(
        status not in {"succeeded", "failed", "skipped"}
        for status in eligible_statuses
    ):
        raise ValueError("Unsupported duration distribution status")
    if fact_kind == "job":
        group_expression = _job_operation_types_sql()
        group_runs_expression = _job_operation_types_sql("runs.")
        fact_table = "filtered_jobs"
        entity_name = "COALESCE(NULLIF(job_id, ''), 'unknown')"
        entity_id = entity_name
        operation_expression = group_expression
        operation_runs_expression = group_runs_expression
        runtime_context_expressions = {
            "engine_expression": "COALESCE(NULLIF(engine_name, ''), 'unknown')",
            "provider_expression": "COALESCE(NULLIF(metadata_provider_name, ''), 'unknown')",
            "platform_expression": "COALESCE(NULLIF(platform_name, ''), 'unknown')",
        }
    else:
        group_expression = f"COALESCE(NULLIF({group_column}, ''), 'unknown')"
        group_runs_expression = f"COALESCE(NULLIF(runs.{group_column}, ''), 'unknown')"
        fact_table = "filtered_dataflows"
        entity_name = "COALESCE(NULLIF(dataflow_name, ''), NULLIF(dataflow_id, ''), 'unknown')"
        entity_id = "dataflow_run_id"
        operation_expression = "COALESCE(NULLIF(operation_type, ''), 'unknown')"
        operation_runs_expression = "COALESCE(NULLIF(runs.operation_type, ''), 'unknown')"
        runtime_context_expressions = {
            "engine_expression": "'unknown'",
            "provider_expression": "'unknown'",
            "platform_expression": "'unknown'",
        }
    return {
        "fact_table": fact_table,
        "group_expression": group_expression,
        "group_runs_expression": group_runs_expression,
        "entity_name_expression": entity_name,
        "entity_id_expression": entity_id,
        "operation_expression": operation_expression,
        "operation_runs_expression": operation_runs_expression,
        **runtime_context_expressions,
        "q1": sorted_list_percentile("sorted_durations", "duration_count", 0.25),
        "p50": sorted_list_percentile("sorted_durations", "duration_count", 0.50),
        "q3": sorted_list_percentile("sorted_durations", "duration_count", 0.75),
        "p95": sorted_list_percentile("sorted_durations", "duration_count", 0.95),
        "status_sql": ",".join(f"'{status}'" for status in eligible_statuses),
    }


def _job_operation_types_sql(prefix: str = "") -> str:
    return (
        "COALESCE(NULLIF(TRIM(regexp_replace(COALESCE(CAST("
        f"{prefix}operation_types AS VARCHAR), ''), '[\\[\\]\"'']', '', 'g')), ''), 'unknown')"
    )


def _assemble(
    groups: list[dict[str, Any]],
    outliers: list[dict[str, Any]],
    operation_mix: list[dict[str, Any]],
    output_key: str,
    fact_kind: DurationFactKind,
) -> list[dict[str, Any]]:
    by_group: dict[str, list[list[Any]]] = {}
    for outlier in outliers:
        group = str(outlier.pop("group_value") or "unknown")
        outlier.pop("outlier_rank", None)
        detail = [
            outlier.get("duration_seconds"),
            outlier.get("dataflow_name"),
            outlier.get("dataflow_run_id"),
            outlier.get("status"),
            outlier.get("operation_type"),
        ]
        if fact_kind == "job":
            detail.extend([
                outlier.get("engine_name"),
                outlier.get("metadata_provider_name"),
                outlier.get("platform_name"),
            ])
        by_group.setdefault(group, []).append(detail)
    mix_by_group: dict[str, list[str]] = {}
    for value in operation_mix:
        group = str(value["group_value"])
        mix_by_group.setdefault(group, []).append(
            f"{value['operation_type']}: {int(value['count'])}"
        )
    for group in groups:
        group_value = str(group.pop("group_value") or "unknown")
        group[output_key] = group_value
        group["outliers"] = by_group.get(group_value, [])
        group["operation_mix"] = ", ".join(mix_by_group.get(group_value, []))
    return groups


_GROUP_QUERY = """
, eligible_duration_runs AS (
  SELECT {group_expression} AS group_value,
         duration_seconds, normalized_status
  FROM {fact_table}
  WHERE normalized_status IN ({status_sql}) AND duration_seconds IS NOT NULL
), grouped_duration_lists AS (
  SELECT group_value,
         COUNT(*) AS duration_count,
         list_sort(list(duration_seconds)) AS sorted_durations,
         ROUND(AVG(duration_seconds), 3) AS avg_duration_seconds,
         COUNT(*) FILTER (WHERE normalized_status = 'succeeded') AS succeeded,
         COUNT(*) FILTER (WHERE normalized_status = 'failed') AS failed,
         COUNT(*) FILTER (WHERE normalized_status = 'skipped') AS skipped
  FROM eligible_duration_runs
  GROUP BY group_value
), group_stats AS (
  SELECT *,
         {q1} AS q1,
         {p50} AS p50,
         {q3} AS q3,
         {p95} AS p95
  FROM grouped_duration_lists
), group_fences AS (
  SELECT *, q1 - 1.5 * (q3 - q1) AS lower_fence,
         q3 + 1.5 * (q3 - q1) AS upper_fence
  FROM group_stats
)
SELECT
  group_value,
  duration_count AS count,
  ROUND(list_extract(sorted_durations, 1), 3) AS min_duration_seconds,
  ROUND((SELECT MIN(value) FROM UNNEST(sorted_durations) item(value)
         WHERE value BETWEEN lower_fence AND upper_fence), 3) AS whisker_min_duration_seconds,
  q1 AS q1_duration_seconds,
  p50 AS p50_duration_seconds,
  q3 AS q3_duration_seconds,
  ROUND((SELECT MAX(value) FROM UNNEST(sorted_durations) item(value)
         WHERE value BETWEEN lower_fence AND upper_fence), 3) AS whisker_max_duration_seconds,
  p95 AS p95_duration_seconds,
  ROUND(list_extract(sorted_durations, duration_count), 3) AS max_duration_seconds,
  avg_duration_seconds,
  succeeded, failed, skipped,
  ROUND(100.0 * succeeded / NULLIF(succeeded + failed, 0), 2) AS success_rate,
  (SELECT COUNT(*) FROM UNNEST(sorted_durations) item(value)
   WHERE value < lower_fence OR value > upper_fence) AS outlier_count
FROM group_fences
ORDER BY p95 DESC, duration_count DESC, group_value
LIMIT ?
"""

_OUTLIER_QUERY = """
, eligible_duration_runs AS (
  SELECT {group_expression} AS group_value,
         duration_seconds,
         {entity_name_expression} AS dataflow_name,
         {entity_id_expression} AS dataflow_run_id, normalized_status,
         {operation_expression} AS operation_type,
         {engine_expression} AS engine_name,
         {provider_expression} AS metadata_provider_name,
         {platform_expression} AS platform_name
  FROM {fact_table}
  WHERE normalized_status IN ({status_sql}) AND duration_seconds IS NOT NULL
), ranked_duration_outliers AS (
  SELECT runs.*, ROW_NUMBER() OVER (
    PARTITION BY runs.group_value ORDER BY duration_seconds DESC, dataflow_run_id
  ) AS outlier_rank
  FROM eligible_duration_runs runs
  JOIN selected_groups groups USING (group_value)
  WHERE duration_seconds < q1 - 1.5 * (q3 - q1)
     OR duration_seconds > q3 + 1.5 * (q3 - q1)
)
SELECT group_value, ROUND(duration_seconds, 3) AS duration_seconds, dataflow_name,
       dataflow_run_id, normalized_status AS status,
       operation_type, engine_name, metadata_provider_name, platform_name,
       outlier_rank
FROM ranked_duration_outliers WHERE outlier_rank <= 40
ORDER BY group_value, duration_seconds DESC, dataflow_run_id
"""

_MIX_QUERY = """
SELECT groups.group_value AS group_value,
       {operation_runs_expression} AS operation_type,
       COUNT(*) AS count
FROM {fact_table} runs
JOIN selected_groups groups
  ON groups.group_value = {group_runs_expression}
WHERE runs.normalized_status IN ({status_sql}) AND runs.duration_seconds IS NOT NULL
GROUP BY groups.group_value, {operation_runs_expression}
ORDER BY groups.group_value, operation_type
"""

from __future__ import annotations

from typing import Any

from datacoolie_studio.domains.monitoring.metrics.failure import dataflow_phase_status_sql
from datacoolie_studio.domains.monitoring.read_models.common import (
    discrete_percentile,
    rows,
)


_GROUP_EXPRESSIONS = {
    "stage": "stage",
    "operation_type": "operation_type",
    "context": "CONCAT(COALESCE(NULLIF(operation_type, ''), 'unknown'), ' · ', COALESCE(NULLIF(stage, ''), 'unknown'))",
}
_STATUSES = ("succeeded", "failed", "skipped", "running", "pending", "unknown")


def runtime_phase_summary(
    conn: Any,
    ctes: str,
    params: list[Any],
    *,
    group_column: str,
    limit: int,
) -> list[dict[str, Any]]:
    query = runtime_phase_query(group_column)
    return pivot_runtime_phase(
        rows(conn.execute(f"{ctes} {query}", params)),
        group_column,
        limit,
    )


def runtime_phase_query(group_column: str, *, standalone: bool = False) -> str:
    if group_column not in _GROUP_EXPRESSIONS:
        raise ValueError(f"Unsupported runtime phase group: {group_column}")
    query = _QUERY.format(
        group_column=_GROUP_EXPRESSIONS[group_column],
        p95=discrete_percentile("duration", 0.95),
        source_status=dataflow_phase_status_sql("", "source"),
        transform_status=dataflow_phase_status_sql("", "transform"),
        destination_status=dataflow_phase_status_sql("", "destination"),
        overhead_status=dataflow_phase_status_sql("", "overhead"),
    )
    if not standalone:
        return query
    nested = query.lstrip()
    return f"WITH{nested[1:]}" if nested.startswith(",") else nested


def pivot_runtime_phase(
    values: list[dict[str, Any]],
    group_key: str,
    limit: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], dict[str, Any]] = {}
    for value in values:
        is_total = int(value.get("is_total") or 0)
        group = "Total" if is_total else str(value.get("group_value") or "unknown")
        phase = str(value.get("phase") or "unknown")
        target = grouped.setdefault(
            (is_total, group),
            {group_key: group, "group": group, "is_total": is_total, "total_duration_seconds": 0},
        )
        duration = float(value.get("duration_seconds") or 0)
        target["total_duration_seconds"] += duration
        for key, item in value.items():
            if key not in {"group_value", "is_total", "phase", "duration_seconds"}:
                target[f"{phase}_{key}"] = item
        target[f"{phase}_duration_seconds"] = round(duration, 3)
    result = []
    for target in grouped.values():
        total = float(target["total_duration_seconds"] or 0)
        target["total_duration_seconds"] = round(total, 3)
        for phase in ("source", "transform", "destination", "overhead"):
            duration = float(target.get(f"{phase}_duration_seconds") or 0)
            target[f"{phase}_duration_seconds"] = round(duration, 3)
            target[f"{phase}_duration_percent"] = _rate(duration, total)
            for status in _STATUSES:
                target.setdefault(f"{phase}_{status}", 0)
            target.setdefault(f"{phase}_run_count", 0)
            target.setdefault(f"{phase}_avg_duration_seconds", 0)
            target.setdefault(f"{phase}_p95_duration_seconds", 0)
        if total > 0:
            result.append(target)
    return sorted(
        result,
        key=lambda item: (-int(item["is_total"]), -float(item["total_duration_seconds"]), str(item["group"])),
    )[: limit + 1]


def _rate(part: int | float, whole: int | float) -> float:
    return round((part / whole) * 100, 2) if whole else 0


_QUERY = """
, eligible_phase_runs AS (
  SELECT *, COALESCE(
    overhead_duration_seconds,
    CASE WHEN duration_seconds IS NOT NULL THEN
      GREATEST(0, duration_seconds - COALESCE(source_duration_seconds, 0)
        - COALESCE(transform_duration_seconds, 0) - COALESCE(destination_duration_seconds, 0))
    END
  ) AS derived_overhead_duration
  FROM filtered_dataflows
  WHERE normalized_status IN ('succeeded','failed','skipped')
), phase_rows AS (
  SELECT {group_column} AS group_value, 'source' AS phase,
         source_duration_seconds AS duration, {source_status} AS phase_status
  FROM eligible_phase_runs
  UNION ALL
  SELECT {group_column}, 'transform', transform_duration_seconds, {transform_status}
  FROM eligible_phase_runs
  UNION ALL
  SELECT {group_column}, 'destination', destination_duration_seconds, {destination_status}
  FROM eligible_phase_runs
  UNION ALL
  SELECT {group_column}, 'overhead', derived_overhead_duration, {overhead_status}
  FROM eligible_phase_runs
)
SELECT
  CASE WHEN GROUPING(group_value) = 1 THEN 'Total'
       ELSE COALESCE(NULLIF(group_value, ''), 'unknown') END AS group_value,
  GROUPING(group_value) AS is_total,
  phase,
  ROUND(COALESCE(SUM(duration), 0), 3) AS duration_seconds,
  COUNT(duration) AS run_count,
  COALESCE(ROUND(AVG(duration), 3), 0) AS avg_duration_seconds,
  {p95} AS p95_duration_seconds,
  COUNT(*) FILTER (WHERE phase_status = 'succeeded') AS succeeded,
  COUNT(*) FILTER (WHERE phase_status = 'failed') AS failed,
  COUNT(*) FILTER (WHERE phase_status = 'skipped') AS skipped,
  COUNT(*) FILTER (WHERE phase_status = 'running') AS running,
  COUNT(*) FILTER (WHERE phase_status = 'pending') AS pending,
  COUNT(*) FILTER (
    WHERE phase_status IS NOT NULL
      AND phase_status NOT IN ('succeeded','failed','skipped','running','pending')
  ) AS unknown
FROM phase_rows
GROUP BY GROUPING SETS ((group_value, phase), (phase))
ORDER BY is_total DESC, group_value, phase
"""

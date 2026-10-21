from __future__ import annotations

from typing import Any
from datacoolie_studio.domains.monitoring.dimensions import CONTEXT_PREFIX

from datacoolie_studio.domains.monitoring.read_models.common import (
    discrete_percentile,
    nullable_discrete_percentile,
    rows,
)


_GROUP_EXPRESSIONS = {
    "stage": "COALESCE(NULLIF(stage, ''), 'unknown')",
    "operation_type": "COALESCE(NULLIF(operation_type, ''), 'unknown')",
    "context": f"'{CONTEXT_PREFIX}' || CAST(json_array(operation_type, stage) AS VARCHAR)",
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
        run_p95=nullable_discrete_percentile("duration_seconds", 0.95),
        source_status=v6_phase_status_sql("source"),
        transform_status=v6_phase_status_sql("transform"),
        destination_status=v6_phase_status_sql("destination"),
        overhead_status=v6_overhead_status_sql(),
        contribution_cte=contribution_input_cte(),
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
            if key in {"phase_runs_eligible", "phase_runs_measured"} or key.startswith("run_scope_"):
                target[key] = item
                continue
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
            if not target[f"{phase}_run_count"]:
                for measure in ("duration_seconds", "duration_percent", "avg_duration_seconds", "p95_duration_seconds"):
                    target[f"{phase}_{measure}"] = None
        if not target.get("phase_runs_measured") and target.get("phase_runs_eligible"):
            target["total_duration_seconds"] = None
        if total > 0 or target.get("phase_runs_eligible"):
            result.append(target)
    return sorted(
        result,
        key=lambda item: (-int(item["is_total"]), -float(item["total_duration_seconds"] or 0), str(item["group"])),
    )[: limit + 1]


def _rate(part: int | float, whole: int | float) -> float:
    return round((part / whole) * 100, 2) if whole else 0


def contribution_input_cte() -> str:
    """Recorded final-attempt values; diagnostic timing admission stays separate."""
    phases = ("source", "transform", "destination")
    fields = ["fd.*", "try_cast(fd.attempts[-1].duration_seconds AS DOUBLE) AS contribution_attempt_duration"]
    for phase in phases:
        fields.extend([
            f"fd.attempts[-1].{phase} AS __v6_{phase}",
            f"try_cast(fd.attempts[-1].{phase}.duration_seconds AS DOUBLE) AS __v6_{phase}_duration",
        ])
    elapsed = " + ".join(f"CASE WHEN __v6_{phase} IS NULL THEN 0 ELSE __v6_{phase}_duration END" for phase in phases)
    guards = " AND ".join(f"(__v6_{phase} IS NULL OR __v6_{phase}_duration IS NOT NULL)" for phase in phases)
    observed = " OR ".join(f"__v6_{phase}_duration IS NOT NULL" for phase in phases)
    return f""", contribution_inputs AS (
      SELECT {', '.join(fields)} FROM filtered_dataflows fd
    ), contribution_overhead AS (
      SELECT *, CASE WHEN contribution_attempt_duration IS NOT NULL AND {guards}
                     THEN GREATEST(0, contribution_attempt_duration - ({elapsed})) END AS derived_overhead_duration
      FROM contribution_inputs
    ), contribution_runs AS (
      SELECT *, ({observed} OR derived_overhead_duration IS NOT NULL) AS duration_observed
      FROM contribution_overhead
    )"""


def timing_input_cte(name: str = "timed_dataflows") -> str:
    """Select final-attempt timing members from the native LIST<STRUCT> value."""

    return f""", {name} AS MATERIALIZED (
  SELECT fd.*,
         fd.attempts[-1] AS __v6_attempt,
         fd.attempts[-1].source AS __v6_source,
         fd.attempts[-1].transform AS __v6_transform,
         fd.attempts[-1].destination AS __v6_destination,
         try_cast(fd.attempts[-1].duration_seconds AS DOUBLE) AS __v6_attempt_duration,
         try_cast(fd.attempts[-1].source.duration_seconds AS DOUBLE) AS __v6_source_duration,
         try_cast(fd.attempts[-1].transform.duration_seconds AS DOUBLE) AS __v6_transform_duration,
         try_cast(fd.attempts[-1].destination.duration_seconds AS DOUBLE) AS __v6_destination_duration,
         try_cast(fd.attempts[-1].source.start_time AS TIMESTAMPTZ) AS __v6_source_start,
         try_cast(fd.attempts[-1].source.end_time AS TIMESTAMPTZ) AS __v6_source_end,
         try_cast(fd.attempts[-1].transform.start_time AS TIMESTAMPTZ) AS __v6_transform_start,
         try_cast(fd.attempts[-1].transform.end_time AS TIMESTAMPTZ) AS __v6_transform_end,
         try_cast(fd.attempts[-1].destination.start_time AS TIMESTAMPTZ) AS __v6_destination_start,
         try_cast(fd.attempts[-1].destination.end_time AS TIMESTAMPTZ) AS __v6_destination_end
  FROM filtered_dataflows fd
)"""


def _timing_timestamp(index: int, prefix: str) -> str:
    names = {
        3: "__v6_attempt.start_time",
        4: "__v6_attempt.end_time",
        6: "__v6_source_start",
        7: "__v6_source_end",
        9: "__v6_transform_start",
        10: "__v6_transform_end",
        12: "__v6_destination_start",
        13: "__v6_destination_end",
    }
    return f"try_cast({prefix}{names[index]} AS TIMESTAMPTZ)"


def _timing_number(index: int, prefix: str) -> str:
    names = {
        5: "__v6_attempt_duration",
        8: "__v6_source_duration",
        11: "__v6_transform_duration",
        14: "__v6_destination_duration",
    }
    return f"try_cast({prefix}{names[index]} AS DOUBLE)"


def v6_timing_complete_sql(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    attempt_start = _timing_timestamp(3, prefix)
    attempt_end = _timing_timestamp(4, prefix)
    attempt_duration = _timing_number(5, prefix)
    checks = [
        f"try_cast({prefix}log_schema_version AS INTEGER) IN (6, 7)",
        f"len({prefix}attempts) > 0",
        f"{attempt_start} IS NOT NULL", f"{attempt_end} IS NOT NULL",
        f"{attempt_duration} IS NOT NULL",
        f"abs(epoch({attempt_end} - {attempt_start}) - {attempt_duration}) <= 0.001",
    ]
    previous_end = attempt_start
    for index, phase in enumerate(("source", "transform", "destination")):
        start = _timing_timestamp(6 + index * 3, prefix)
        end = _timing_timestamp(7 + index * 3, prefix)
        phase_checks = [
            f"{start} IS NOT NULL", f"{end} IS NOT NULL", f"{end} >= {start}",
            f"{start} >= {attempt_start}", f"{end} <= {attempt_end}", f"{start} >= {previous_end}",
            f"abs(epoch({end} - {start}) - {_timing_number(8 + index * 3, prefix)}) <= 0.001",
        ]
        if phase != "destination":
            absent = f"LOWER(TRIM(COALESCE({prefix}operation_type, ''))) = 'maintenance' AND {prefix}__v6_{phase} IS NULL"
            checks.append(f"(({absent}) OR ({' AND '.join(phase_checks)}))")
            previous_end = f"CASE WHEN {prefix}__v6_{phase} IS NULL THEN {previous_end} ELSE {end} END"
        else:
            checks.extend(phase_checks)
    return "(" + " AND ".join(checks) + ")"


def v6_phase_elapsed_sql(phase: str) -> str:
    """Elapsed arithmetic only: structurally absent maintenance phases contribute no time."""
    if phase not in {"source", "transform", "destination"}:
        raise ValueError(f"Unsupported v6 phase: {phase}")
    if phase == "destination":
        return "__v6_destination_duration"
    return f"CASE WHEN LOWER(TRIM(COALESCE(operation_type, ''))) = 'maintenance' AND __v6_{phase} IS NULL THEN 0 ELSE __v6_{phase}_duration END"


def v6_attempt_duration_sql(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"epoch({_timing_timestamp(4, prefix)} - {_timing_timestamp(3, prefix)})"


def v6_overhead_status_sql(alias: str = "") -> str:
    """Classify a failed v6 run with no failed phase as overhead evidence."""

    prefix = f"{alias}." if alias else ""
    statuses = [
        f"COALESCE(LOWER(NULLIF(TRIM(CAST(({prefix}__v6_{phase}).status AS VARCHAR)), '')), '') = 'failed'"
        for phase in ("source", "transform", "destination")
    ]
    return (
        f"CASE WHEN LOWER(COALESCE(NULLIF(TRIM(CAST({prefix}normalized_status AS VARCHAR)), ''), 'unknown')) = 'failed' "
        f"AND NOT ({' OR '.join(statuses)}) "
        f"AND len({prefix}attempts) > 0 THEN 'failed' END"
    )


def v6_phase_status_sql(phase: str, alias: str = "") -> str:
    if phase not in {"source", "transform", "destination"}:
        raise ValueError(f"Unsupported v6 phase: {phase}")
    prefix = f"{alias}." if alias else ""
    return f"LOWER(NULLIF(TRIM(CAST(({prefix}__v6_{phase}).status AS VARCHAR)), ''))"


_QUERY = """
{contribution_cte}
, run_scope AS (
  SELECT {group_column} AS group_value, GROUPING({group_column}) AS is_total,
         ROUND(SUM(duration_seconds), 3) AS run_scope_duration_seconds,
         ROUND(AVG(duration_seconds), 3) AS run_scope_avg_duration_seconds,
         {run_p95} AS run_scope_p95_duration_seconds,
         COUNT(*) AS run_scope_run_count,
         COUNT(*) FILTER (WHERE normalized_status = 'failed') AS run_scope_failed_count
  FROM contribution_runs
  GROUP BY GROUPING SETS (({group_column}), ())
)
, phase_rows AS (
  SELECT {group_column} AS group_value, 'source' AS phase,
         __v6_source_duration AS duration, {source_status} AS phase_status,
         __v6_source IS NOT NULL AS phase_present, duration_observed
  FROM contribution_runs
  UNION ALL
  SELECT {group_column}, 'transform', __v6_transform_duration, {transform_status},
         __v6_transform IS NOT NULL, duration_observed
  FROM contribution_runs
  UNION ALL
  SELECT {group_column}, 'destination', __v6_destination_duration, {destination_status},
         __v6_destination IS NOT NULL, duration_observed
  FROM contribution_runs
  UNION ALL
  SELECT {group_column}, 'overhead', derived_overhead_duration, {overhead_status},
         derived_overhead_duration IS NOT NULL, duration_observed
  FROM contribution_runs
), phase_summary AS (
SELECT
  CASE WHEN GROUPING(group_value) = 1 THEN 'Total'
       ELSE COALESCE(NULLIF(group_value, ''), 'unknown') END AS group_value,
  GROUPING(group_value) AS is_total,
  phase,
  COUNT(*) AS phase_runs_eligible,
  COUNT(*) FILTER (WHERE duration_observed) AS phase_runs_measured,
  ROUND(SUM(duration), 3) AS duration_seconds,
  COUNT(duration) AS run_count,
  ROUND(AVG(duration), 3) AS avg_duration_seconds,
  {p95} AS p95_duration_seconds,
  COUNT(*) FILTER (WHERE phase_status = 'succeeded') AS succeeded,
  COUNT(*) FILTER (WHERE phase_status = 'failed') AS failed,
  COUNT(*) FILTER (WHERE phase_status = 'skipped') AS skipped,
  COUNT(*) FILTER (WHERE phase_status = 'running') AS running,
  COUNT(*) FILTER (WHERE phase_status = 'pending') AS pending,
  COUNT(*) FILTER (
    WHERE phase <> 'overhead' AND phase_present
      AND COALESCE(phase_status, '') NOT IN ('succeeded','failed','skipped','running','pending')
  ) AS unknown
FROM phase_rows
GROUP BY GROUPING SETS ((group_value, phase), (phase))
)
SELECT phase_summary.*, run_scope.run_scope_duration_seconds,
       run_scope.run_scope_avg_duration_seconds, run_scope.run_scope_p95_duration_seconds,
       run_scope.run_scope_run_count, run_scope.run_scope_failed_count
FROM phase_summary
JOIN run_scope ON phase_summary.is_total = run_scope.is_total
  AND (phase_summary.is_total = 1 OR phase_summary.group_value = run_scope.group_value)
ORDER BY phase_summary.is_total DESC, phase_summary.group_value, phase
"""

"""Complete Diagnostics finding groups and exact affected-object evidence.

Groups and objects are derived from the same SQL population as the page
aggregates, before any display or page limit. A group is identified by the
structural selector ``rule|reason|section``; it is validated against the
supported rule table and bound as a parameter, never interpolated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from datacoolie_studio.domains.monitoring.read_models.jobs import (
    reconciliation_comparison_ctes,
)

FINDING_GROUP_DEFAULT_LIMIT = 50
FINDING_GROUP_MAX_LIMIT = 500
_METRIC_COUNT = 4
_MAX_SECTION_LENGTH = 512

_UNASSESSED_REASONS = (
    "nonterminal",
    "unknown_loss",
    "lost_detail",
    "unknown_snapshot",
    "stale_snapshot",
    "unknown_total",
    "range_incomplete",
)
_FIELD_SECTION = re.compile(r"^(dataflow|job)/[a-z][a-z /]{0,63}$")
_CAPTURE_REASON = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class _Rule:
    kind: str | None
    subject_kind: str
    reasons: tuple[str, ...] | None
    section: str = "none"


_RULES: dict[str, _Rule] = {
    "orphan_job_log": _Rule("issue", "job", ("missing_job_log",)),
    "missing_expected_children": _Rule("issue", "job", ("terminal_job_without_children",)),
    "reconciliation_mismatch": _Rule("issue", "job", ("count_mismatch",)),
    "field_completeness": _Rule(None, "field_group", ("critical_presence", "low_presence"), "field_group"),
    "log_loss": _Rule("warning", "job", ("records", "bytes", "records_and_bytes")),
    "diagnostic_capture": _Rule("warning", "dataflow_record", None, "capture"),
    "linkage_unassessed": _Rule("limitation", "job", (*_UNASSESSED_REASONS, "unassessed")),
    "reconciliation_unassessed": _Rule("limitation", "job", _UNASSESSED_REASONS),
    "log_loss_unknown": _Rule("limitation", "job", ("unknown_counter",)),
    "scope_child_subset": _Rule("notice", "job", ("filtered_child_scope",)),
}
_FIELD_REASON_KIND = {"critical_presence": "issue", "low_presence": "warning"}

GROUP_SORT_FIELDS = frozenset({
    "priority", "kind", "rule", "reason", "section", "affected_objects", "latest_time",
})
OBJECT_SORT_FIELDS = frozenset({
    "latest_time", "source_id", "log_session_id", "job_id", "dataflow_run_id",
    "job_status", "checks", "occurrences",
})
_GROUP_SORT_SQL = {
    "priority": "kind_rank",
    "kind": "kind_rank",
    "rule": "rule",
    "reason": "reason",
    "section": "section",
    "affected_objects": "affected_objects",
    "latest_time": "latest_time",
}
_OBJECT_SORT_SQL = {name: f"o.{name}" for name in OBJECT_SORT_FIELDS}


class FindingRequestError(ValueError):
    """An unsupported finding mode, selector, or sort request."""


@dataclass(frozen=True)
class FindingGroup:
    rule: str
    reason: str
    section: str

    @property
    def key(self) -> str:
        return f"{self.rule}|{self.reason}|{self.section}"

    @property
    def kind(self) -> str:
        rule = _RULES[self.rule]
        return rule.kind or _FIELD_REASON_KIND[self.reason]

    @property
    def subject_kind(self) -> str:
        return _RULES[self.rule].subject_kind


def parse_finding_group(value: str | None) -> FindingGroup:
    """Validate a structural group selector against the supported predicates."""

    if not value:
        raise FindingRequestError("findingGroup is required for object evidence")
    parts = value.split("|", 2)
    if len(parts) != 3:
        raise FindingRequestError("findingGroup must be rule|reason|section")
    rule_name, reason, section = parts
    rule = _RULES.get(rule_name)
    if rule is None:
        raise FindingRequestError("Unsupported finding rule")
    if len(section) > _MAX_SECTION_LENGTH:
        raise FindingRequestError("findingGroup section is too long")
    if rule.reasons is not None and reason not in rule.reasons:
        raise FindingRequestError("Unsupported finding reason for rule")
    if rule.reasons is None and not _CAPTURE_REASON.fullmatch(reason):
        raise FindingRequestError("Unsupported finding reason for rule")
    if rule.section == "none" and section:
        raise FindingRequestError("This finding rule has no section")
    if rule.section == "field_group" and not _FIELD_SECTION.fullmatch(section):
        raise FindingRequestError("Unsupported field-group section")
    return FindingGroup(rule_name, reason, section)


def validate_group_sort(sort_by: str | None, sort_dir: str | None) -> tuple[str, str]:
    by = sort_by or "priority"
    if by not in GROUP_SORT_FIELDS:
        raise FindingRequestError(f"Unsupported finding group sort: {by}")
    direction = sort_dir or "asc"
    if direction not in {"asc", "desc"}:
        raise FindingRequestError("sortDir must be asc or desc")
    return by, direction


def validate_object_sort(sort_by: str | None, sort_dir: str | None) -> tuple[str, str]:
    by = sort_by or "latest_time"
    if by not in OBJECT_SORT_FIELDS:
        raise FindingRequestError(f"Unsupported finding object sort: {by}")
    direction = sort_dir or "desc"
    if direction not in {"asc", "desc"}:
        raise FindingRequestError("sortDir must be asc or desc")
    return by, direction


def validate_finding_request(
    view: str,
    group: str | None,
    sort_by: str | None,
    sort_dir: str | None,
    kind_filter: str | None = None,
) -> None:
    """Reject an unsupported mode/selector/sort combination before any query."""

    if view == "registry":
        if group:
            raise FindingRequestError("findingGroup is not valid for the group list")
        validate_group_sort(sort_by, sort_dir)
        validate_group_kind_filter(kind_filter)
    elif view == "detail":
        if kind_filter:
            raise FindingRequestError("findingKind is only valid for the group list")
        parse_finding_group(group)
        validate_object_sort(sort_by, sort_dir)
    else:
        raise FindingRequestError(f"Unsupported Diagnostics evidence view: {view}")


def field_group_findings(field_completeness: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return actionable field groups whose existing severity is a finding."""

    findings = []
    for row in field_completeness:
        severity = row.get("severity")
        if not row.get("actionable") or severity not in {"bad", "warning"}:
            continue
        reason = "critical_presence" if severity == "bad" else "low_presence"
        findings.append({
            "kind": _FIELD_REASON_KIND[reason],
            "reason": reason,
            "section": f"{row['record_type']}/{row['group']}",
            "occurrences": int(row.get("missing_values") or 0),
            "row": row,
        })
    return findings


_STRUCT_METRICS = "STRUCT(metric VARCHAR, expected BIGINT, observed BIGINT, difference BIGINT)[]"
_OBJECT_COLUMNS = (
    ("source_id", "BIGINT"),
    ("log_session_id", "VARCHAR"),
    ("job_id", "VARCHAR"),
    ("dataflow_id", "VARCHAR"),
    ("dataflow_run_id", "VARCHAR"),
    ("dataflow_name", "VARCHAR"),
    ("file_uri", "VARCHAR"),
    ("file_kind", "VARCHAR"),
    ("record_line", "BIGINT"),
    ("latest_time", "TIMESTAMPTZ"),
    ("checks", "BIGINT"),
    ("occurrences", "BIGINT"),
    ("job_status", "VARCHAR"),
    ("job_total_dataflows", "BIGINT"),
    ("filtered_dataflow_records", "BIGINT"),
    ("recorded_dataflow_records", "BIGINT"),
    ("excluded_dataflow_records", "BIGINT"),
    ("observed_at", "TIMESTAMPTZ"),
    ("records_dropped", "HUGEINT"),
    ("bytes_dropped", "HUGEINT"),
    ("metrics", _STRUCT_METRICS),
)
_IDENTITY = "(source_id, log_session_id, job_id, dataflow_run_id, file_uri, record_line)"
_KIND_RANK = "CASE kind WHEN 'issue' THEN 0 WHEN 'warning' THEN 1 WHEN 'limitation' THEN 2 ELSE 3 END"


def _branch(rule: str, kind: str, reason: str, source: str, section: str = "''", **values: str) -> str:
    subject_kind = _RULES[rule].subject_kind
    columns = ", ".join(
        f"CAST({values.get(name, 'NULL')} AS {sql_type}) AS {name}"
        for name, sql_type in _OBJECT_COLUMNS
    )
    return (
        f"SELECT '{rule}' AS rule, '{kind}' AS kind, '{subject_kind}' AS subject_kind, "
        f"CAST({reason} AS VARCHAR) AS reason, CAST({section} AS VARCHAR) AS section, "
        f"{columns} FROM {source}"
    )


_LINKAGE_VALUES = {
    "source_id": "_source_id",
    "log_session_id": "log_session_id",
    "job_id": "job_id",
    "latest_time": "COALESCE(latest_dataflow_time, latest_job_time)",
    "job_status": "job_status",
    "job_total_dataflows": "job_total_dataflows",
    "filtered_dataflow_records": "filtered_dataflow_records",
    "recorded_dataflow_records": "recorded_dataflow_records",
    "excluded_dataflow_records": "excluded_dataflow_records",
    "observed_at": "job_observed_at",
    "records_dropped": "job_log_records_dropped",
}
_JOB_IDENTITY_JOIN = (
    "LEFT JOIN jobs_by_id j ON j._source_id = r._source_id "
    "AND j.log_session_id IS NOT DISTINCT FROM r.log_session_id "
    "AND j.job_id IS NOT DISTINCT FROM r.job_id"
)
_JOB_VALUES = {
    "source_id": "r._source_id",
    "log_session_id": "r.log_session_id",
    "job_id": "r.job_id",
    "latest_time": "j.latest_job_time",
    "job_status": "j.job_status",
    "job_total_dataflows": "j.job_total_dataflows",
    "observed_at": "j.job_observed_at",
    "records_dropped": "j.job_log_records_dropped",
    "bytes_dropped": "j.job_log_bytes_dropped",
}


def _reconciliation_branch(rule: str, kind: str, reason: str, predicate: str, group_reason: str) -> str:
    source = f"""(
        SELECT _source_id, log_session_id, job_id, {group_reason} AS reason, COUNT(*) AS checks,
               LIST(struct_pack(
                 metric := metric,
                 expected := CAST(expected AS BIGINT),
                 observed := CAST(observed AS BIGINT),
                 difference := CAST(observed AS BIGINT) - CAST(expected AS BIGINT)
               ) ORDER BY metric) AS metrics
        FROM comparisons WHERE {predicate}
        GROUP BY _source_id, log_session_id, job_id{', eligibility' if group_reason == 'eligibility' else ''}
      ) r {_JOB_IDENTITY_JOIN}"""
    return _branch(
        rule, kind, reason, source,
        **_JOB_VALUES, checks="r.checks", metrics="r.metrics",
    )


def _object_union(scope: str) -> str:
    linkage = _LINKAGE_VALUES
    branches = [
        _branch(
            "orphan_job_log", "issue", "'missing_job_log'",
            "linkage WHERE category = 'orphan_dataflow_job_id'",
            **{**linkage, "job_status": "NULL"},
        ),
        _branch(
            "missing_expected_children", "issue", "'terminal_job_without_children'",
            "linkage WHERE category = 'job_without_dataflow_records'", **linkage,
        ),
        _branch(
            "linkage_unassessed", "limitation", "unassessed_reason",
            "linkage WHERE category = 'job_children_unassessed' "
            "AND unassessed_reason <> 'filtered_child_scope'",
            **linkage,
        ),
        _branch(
            "log_loss", "warning",
            "CASE WHEN j.job_log_records_dropped > 0 AND j.job_log_bytes_dropped > 0 THEN 'records_and_bytes' "
            "WHEN j.job_log_records_dropped > 0 THEN 'records' ELSE 'bytes' END",
            "jobs_by_id j WHERE COALESCE(j.job_log_records_dropped, 0) > 0 "
            "OR COALESCE(j.job_log_bytes_dropped, 0) > 0",
            source_id="j._source_id", log_session_id="j.log_session_id", job_id="j.job_id",
            latest_time="j.latest_job_time", job_status="j.job_status",
            job_total_dataflows="j.job_total_dataflows", observed_at="j.job_observed_at",
            records_dropped="j.job_log_records_dropped", bytes_dropped="j.job_log_bytes_dropped",
        ),
        _branch(
            "log_loss_unknown", "limitation", "'unknown_counter'",
            "jobs_by_id j WHERE j.job_log_records_dropped IS NULL OR j.job_log_bytes_dropped IS NULL",
            source_id="j._source_id", log_session_id="j.log_session_id", job_id="j.job_id",
            latest_time="j.latest_job_time", job_status="j.job_status",
            job_total_dataflows="j.job_total_dataflows", observed_at="j.job_observed_at",
            records_dropped="j.job_log_records_dropped", bytes_dropped="j.job_log_bytes_dropped",
        ),
        _branch(
            "diagnostic_capture", "warning", "c.reason",
            "capture_findings c",
            section="c.field", source_id="c._source_id", log_session_id="c.log_session_id",
            job_id="c.job_id", dataflow_id="c.dataflow_id", dataflow_run_id="c.dataflow_run_id",
            dataflow_name="c.dataflow_name", file_uri="c._file_uri", file_kind="c._file_kind",
            record_line="c._record_line", latest_time="c.event_time", occurrences="c.occurrences",
        ),
    ]
    if scope == "full":
        branches.append(_reconciliation_branch(
            "reconciliation_mismatch", "issue", "'count_mismatch'",
            "eligibility = 'full' AND expected <> observed", "'count_mismatch'",
        ))
        branches.append(_reconciliation_branch(
            "reconciliation_unassessed", "limitation", "r.reason",
            "eligibility <> 'full'", "eligibility",
        ))
    else:
        branches.append(_branch(
            "scope_child_subset", "notice", "'filtered_child_scope'",
            f"selected_jobs r {_JOB_IDENTITY_JOIN}",
            **_JOB_VALUES, checks=str(_METRIC_COUNT),
        ))
    return "\nUNION ALL\n".join(branches)


@dataclass(frozen=True)
class FindingsContext:
    """SQL prefix, bound parameters, and non-SQL field findings for one snapshot."""

    prefix: str
    params: list[Any]
    fields: list[dict[str, Any]]
    scope: str


def findings_context(
    linkage_sql: str,
    base_params: list[Any],
    *,
    scope: str,
    child_relation: str,
    field_completeness: list[dict[str, Any]],
) -> FindingsContext:
    fields = field_group_findings(field_completeness)
    if fields:
        values = ", ".join(
            "(CAST(? AS VARCHAR), CAST(? AS VARCHAR), CAST(? AS VARCHAR), CAST(? AS BIGINT))"
            for _ in fields
        )
        field_params: list[Any] = [
            value
            for item in fields
            for value in (item["kind"], item["reason"], item["section"], item["occurrences"])
        ]
        field_sql = f"VALUES {values}"
    else:
        field_params = []
        field_sql = (
            "SELECT CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR), "
            "CAST(NULL AS BIGINT) WHERE FALSE"
        )
    comparison = reconciliation_comparison_ctes(scope, child_relation=child_relation)
    prefix = f"""{linkage_sql}
    {comparison}
    , capture_findings AS (
      SELECT r._source_id, r.log_session_id, r.job_id, r.dataflow_id, r.dataflow_run_id,
             r.dataflow_name, r._file_uri, r._file_kind, r._record_line, r.event_time,
             COALESCE(u.issue.reason, 'unknown') AS reason,
             COALESCE(u.issue.field, '') AS field,
             COUNT(*) AS occurrences
      FROM filtered_dataflows r
      CROSS JOIN UNNEST(r.diagnostic_issues) AS u(issue)
      GROUP BY r._source_id, r.log_session_id, r.job_id, r.dataflow_id, r.dataflow_run_id,
               r.dataflow_name, r._file_uri, r._file_kind, r._record_line, r.event_time,
               COALESCE(u.issue.reason, 'unknown'), COALESCE(u.issue.field, '')
    ), finding_objects AS (
      {_object_union(scope)}
    ), field_group_findings(kind, reason, section, occurrences) AS (
      {field_sql}
    ), finding_groups AS (
      SELECT rule, reason, section, kind, subject_kind,
             CAST(COUNT(*) AS BIGINT) AS affected_objects,
             CAST(SUM(checks) AS HUGEINT) AS checks,
             CAST(SUM(occurrences) AS HUGEINT) AS occurrences,
             MAX(latest_time) AS latest_time
      FROM finding_objects WHERE kind <> 'notice'
      GROUP BY rule, reason, section, kind, subject_kind
      UNION ALL
      SELECT 'field_completeness', reason, section, kind, 'field_group',
             CAST(1 AS BIGINT), CAST(NULL AS HUGEINT), CAST(occurrences AS HUGEINT),
             CAST(NULL AS TIMESTAMPTZ)
      FROM field_group_findings
    ), ranked_groups AS (
      SELECT *, {_KIND_RANK} AS kind_rank FROM finding_groups
    )"""
    return FindingsContext(prefix, [*base_params, *field_params], fields, scope)


def _rows(result: Any) -> list[dict[str, Any]]:
    columns = [column[0] for column in result.description]
    return [
        {column: _json_value(value) for column, value in zip(columns, row)}
        for row in result.fetchall()
    ]


def _json_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (datetime, date)) else value


def _group_row(row: dict[str, Any]) -> dict[str, Any]:
    group = FindingGroup(str(row["rule"]), str(row["reason"]), str(row["section"]))
    return {
        "group_key": group.key,
        "kind": row["kind"],
        "rule": group.rule,
        "reason": group.reason,
        "section": group.section,
        "subject_kind": row["subject_kind"],
        "affected_objects": int(row["affected_objects"] or 0),
        "checks": int(row["checks"]) if row.get("checks") is not None else None,
        "occurrences": int(row["occurrences"]) if row.get("occurrences") is not None else None,
        "latest_time": row.get("latest_time"),
    }


GROUP_KIND_FILTERS = {
    "all": ("issue", "warning", "limitation"),
    "findings": ("issue", "warning"),
    "limitations": ("limitation",),
}


def validate_group_kind_filter(value: str | None) -> tuple[str, ...]:
    kinds = GROUP_KIND_FILTERS.get(value or "all")
    if kinds is None:
        raise FindingRequestError(f"Unsupported finding kind filter: {value}")
    return kinds


def list_finding_groups(
    conn: Any,
    context: FindingsContext,
    *,
    limit: int,
    offset: int,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    kind_filter: str | None = None,
) -> list[dict[str, Any]]:
    by, direction = validate_group_sort(sort_by, sort_dir)
    kinds = ", ".join(f"'{kind}'" for kind in validate_group_kind_filter(kind_filter))
    order = f"{_GROUP_SORT_SQL[by]} {direction.upper()} NULLS LAST"
    result = conn.execute(
        f"""{context.prefix}
        SELECT rule, reason, section, kind, subject_kind, affected_objects, checks, occurrences, latest_time
        FROM ranked_groups
        WHERE kind IN ({kinds})
        ORDER BY {order}, kind_rank ASC, affected_objects DESC, rule, reason, section
        LIMIT ? OFFSET ?""",
        [*context.params, int(limit), int(offset)],
    )
    return [_group_row(row) for row in _rows(result)]


def findings_summary(conn: Any, context: FindingsContext) -> dict[str, Any]:
    result = conn.execute(
        f"""{context.prefix}
        SELECT 'group_kind' AS part, kind AS a, '' AS b, CAST(COUNT(*) AS BIGINT) AS n,
               CAST(0 AS HUGEINT) AS m
        FROM finding_groups GROUP BY kind
        UNION ALL
        SELECT 'objects', kind, subject_kind,
               CAST(COUNT(DISTINCT {_IDENTITY}) AS BIGINT), CAST(0 AS HUGEINT)
        FROM finding_objects WHERE kind <> 'notice' GROUP BY kind, subject_kind
        UNION ALL
        SELECT 'notice', rule, reason, CAST(COUNT(*) AS BIGINT),
               CAST(COALESCE(SUM(checks), 0) AS HUGEINT)
        FROM finding_objects WHERE kind = 'notice' GROUP BY rule, reason""",
        context.params,
    )
    group_counts = {"issue": 0, "warning": 0, "limitation": 0}
    objects: dict[tuple[str, str], int] = {}
    notices = []
    for row in _rows(result):
        if row["part"] == "group_kind":
            group_counts[str(row["a"])] = int(row["n"])
        elif row["part"] == "objects":
            objects[(str(row["a"]), str(row["b"]))] = int(row["n"])
        else:
            group = FindingGroup(str(row["a"]), str(row["b"]), "")
            notices.append({
                "code": group.reason,
                "group_key": group.key,
                "jobs": int(row["n"]),
                "checks": int(row["m"]),
            })
    for kind in ("issue", "warning"):
        count = sum(1 for item in context.fields if item["kind"] == kind)
        if count:
            objects[(kind, "field_group")] = count
    return {
        "group_counts": group_counts,
        "affected_objects": [
            {"kind": kind, "subject_kind": subject, "count": count}
            for (kind, subject), count in sorted(objects.items())
            if count
        ],
        "notices": sorted(notices, key=lambda item: item["group_key"]),
    }


def finding_group_summary(conn: Any, context: FindingsContext, group: FindingGroup) -> dict[str, Any] | None:
    """Return the selected group's full object total, or None when it has no rows."""

    if group.rule == "field_completeness":
        item = next(
            (
                field for field in context.fields
                if field["reason"] == group.reason and field["section"] == group.section
            ),
            None,
        )
        if item is None:
            return None
        return _group_row({
            "rule": group.rule, "reason": group.reason, "section": group.section,
            "kind": item["kind"], "subject_kind": "field_group", "affected_objects": 1,
            "checks": None, "occurrences": item["occurrences"], "latest_time": None,
        })
    result = conn.execute(
        f"""{context.prefix}
        SELECT rule, reason, section, kind, subject_kind,
               CAST(COUNT(*) AS BIGINT) AS affected_objects,
               CAST(SUM(checks) AS HUGEINT) AS checks,
               CAST(SUM(occurrences) AS HUGEINT) AS occurrences,
               MAX(latest_time) AS latest_time
        FROM finding_objects
        WHERE rule = ? AND reason = ? AND section = ?
        GROUP BY rule, reason, section, kind, subject_kind""",
        [*context.params, group.rule, group.reason, group.section],
    )
    rows = _rows(result)
    return _group_row(rows[0]) if rows else None


def list_finding_objects(
    conn: Any,
    context: FindingsContext,
    group: FindingGroup,
    *,
    limit: int,
    offset: int,
    sort_by: str | None = None,
    sort_dir: str | None = None,
) -> list[dict[str, Any]]:
    by, direction = validate_object_sort(sort_by, sort_dir)
    if group.rule == "field_completeness":
        item = next(
            (
                field for field in context.fields
                if field["reason"] == group.reason and field["section"] == group.section
            ),
            None,
        )
        if item is None or offset > 0:
            return []
        return [{
            "group_key": group.key,
            "rule": group.rule,
            "reason": group.reason,
            "section": group.section,
            "subject_kind": "field_group",
            "occurrences": item["occurrences"],
            "field_group": item["row"],
        }]
    order = f"{_OBJECT_SORT_SQL[by]} {direction.upper()} NULLS LAST"
    column_names = ", ".join(f"o.{name}" for name, _ in _OBJECT_COLUMNS)
    result = conn.execute(
        f"""{context.prefix}
        SELECT o.rule, o.reason, o.section, o.subject_kind, {column_names}
        FROM finding_objects o
        WHERE o.rule = ? AND o.reason = ? AND o.section = ?
        ORDER BY {order}, o.source_id, o.log_session_id NULLS FIRST, o.job_id NULLS FIRST,
                 o.dataflow_run_id NULLS FIRST, o.file_uri NULLS FIRST, o.record_line NULLS FIRST
        LIMIT ? OFFSET ?""",
        [*context.params, group.rule, group.reason, group.section, int(limit), int(offset)],
    )
    objects = []
    for row in _rows(result):
        row["group_key"] = group.key
        # Non-reconciliation objects have no per-metric detail; keep the list shape.
        row["metrics"] = row.get("metrics") or []
        objects.append(row)
    return objects

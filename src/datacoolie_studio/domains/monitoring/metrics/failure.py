from __future__ import annotations

from typing import Any

from datacoolie_studio.domains.monitoring.metrics.failure_taxonomy import (
    FAILURE_RULES,
    FailureClassification,
    categorize_failure,
    classify_failure,
)
from datacoolie_studio.domains.monitoring.metrics.v6_semantics import (
    final_attempt,
    is_supported_log_row,
)

__all__ = [
    "FailureClassification",
    "categorize_failure",
    "classify_failure",
    "failure_all_messages_sql",
    "failure_category_sql",
    "failure_message_sql",
    "failure_phase_sql",
    "failure_rule_id_sql",
    "failure_tags_sql",
    "dataflow_failed_phases",
    "dataflow_failure_phase_and_message",
    "dataflow_phase_failed_sql",
    "dataflow_phase_status_sql",
    "normalized_failure_message_sql",
]


_LIST_LIKE_PATTERN = r"^[a-z0-9_:\-./]+(?:\s*;\s*[a-z0-9_:\-./]+)+$"
DATAFLOW_EXECUTION_PHASES = ("source", "transform", "destination")
DATAFLOW_FAILURE_PHASES = (*DATAFLOW_EXECUTION_PHASES, "overhead")


def dataflow_failed_phases(row: dict[str, Any]) -> tuple[str, ...]:
    """Return every failed phase; overhead is the failed-run fallback."""
    if _overall_status(row) != "failed":
        return ()
    native_selected = is_supported_log_row(row) and "attempts" in row
    attempt = final_attempt(row) if native_selected else None
    explicit = tuple(
        phase
        for phase in DATAFLOW_EXECUTION_PHASES
        if _normalized_status(
            _attempt_phase_value(attempt, phase, "status")
            if native_selected else row.get(f"{phase}_status")
        ) == "failed"
    )
    if explicit:
        return explicit
    if is_supported_log_row(row):
        # A v6 failure with no phase evidence occurred before pipeline entry
        # (or omitted causal detail).  It is not an invented overhead phase.
        return ("unknown",)
    return ("overhead",)


def dataflow_failure_phase_and_message(row: dict[str, Any]) -> tuple[str, str]:
    """Select one causal phase and its best message from canonical status rules."""
    failed_phases = dataflow_failed_phases(row)
    if not failed_phases:
        return "unknown", ""
    phase = failed_phases[0]
    native_selected = is_supported_log_row(row) and "attempts" in row
    attempt = final_attempt(row) if native_selected else None
    if phase == "overhead":
        phase_message = None
    elif native_selected:
        phase_message = _attempt_phase_value(attempt, phase, "message")
    else:
        phase_message = row.get(f"{phase}_error_message")
    if _has_message(phase_message):
        return phase, str(phase_message)
    root_keys = ("message",) if native_selected else ("error_messages", "message", "error_message")
    for key in root_keys:
        if _has_message(row.get(key)):
            return phase, str(row[key])
    return phase, ""


def dataflow_phase_status_sql(alias: str, phase: str, *, native: bool = False) -> str:
    """Generate the SQL status for one phase from the same failure-phase contract."""
    prefix = f"{alias}." if alias else ""
    if phase in DATAFLOW_EXECUTION_PHASES:
        scalar = f"LOWER(NULLIF(TRIM(CAST({prefix}{phase}_status AS VARCHAR)), ''))"
        if not native:
            return scalar
        typed = f"LOWER(NULLIF(TRIM(CAST(({prefix}attempts[-1].{phase}).status AS VARCHAR)), ''))"
        # Serving keeps scalar aliases for compatibility, but native attempt
        # status is the source of truth for v6 report queries.
        return f"CASE WHEN CAST({prefix}log_schema_version AS INTEGER) IN (6, 7) THEN {typed} ELSE {scalar} END"
    if phase != "overhead":
        raise ValueError(f"Unsupported dataflow failure phase: {phase}")
    explicit_failed = " OR ".join(
        f"COALESCE({dataflow_phase_status_sql(alias, item, native=native)}, '') = 'failed'"
        for item in DATAFLOW_EXECUTION_PHASES
    )
    # v6 failures without a failed execution phase are preflight/omitted
    # causal detail.  The version marker is sufficient here and keeps this
    # scalar compatibility helper usable by callers that do not project the
    # native attempts list.
    v6_without_phase = f" AND COALESCE(TRY_CAST({prefix}log_schema_version AS INTEGER), 0) NOT IN (6, 7)"
    return (
        f"CASE WHEN {_normalized_status_sql(f'{prefix}normalized_status')} = 'failed' "
        f"AND NOT ({explicit_failed}){v6_without_phase} THEN 'failed' END"
    )


def dataflow_phase_failed_sql(alias: str, phase: str, *, native: bool = False) -> str:
    prefix = f"{alias}." if alias else ""
    return (
        f"{_normalized_status_sql(f'{prefix}normalized_status')} = 'failed' AND "
        f"COALESCE({dataflow_phase_status_sql(alias, phase, native=native)}, '') = 'failed'"
    )


def failure_message_sql(alias: str, *, native: bool = False) -> str:
    source = _message_sql(alias, "source", native=native)
    transform = _message_sql(alias, "transform", native=native)
    destination = _message_sql(alias, "destination", native=native)
    root = _root_message_sql(alias, native=native)
    source_failed = dataflow_phase_failed_sql(alias, "source", native=native)
    transform_failed = dataflow_phase_failed_sql(alias, "transform", native=native)
    destination_failed = dataflow_phase_failed_sql(alias, "destination", native=native)
    return f"""CASE
      WHEN {source_failed} THEN COALESCE({source}, {root}, '')
      WHEN {transform_failed} THEN COALESCE({transform}, {root}, '')
      WHEN {destination_failed} THEN COALESCE({destination}, {root}, '')
      WHEN {dataflow_phase_status_sql(alias, "overhead", native=native)} = 'failed' THEN COALESCE({root}, '')
      WHEN {_normalized_status_sql(f'{alias}.normalized_status')} = 'failed' THEN COALESCE({root}, '')
      ELSE '' END"""


def failure_all_messages_sql(alias: str, *, native: bool = False) -> str:
    return (
        "CONCAT_WS('\n', "
        f"{_message_sql(alias, 'source', native=native)}, {_message_sql(alias, 'transform', native=native)}, "
        f"{_message_sql(alias, 'destination', native=native)}, "
        f"{_root_message_sql(alias, native=native)})"
    )


def failure_phase_sql(alias: str, *, native: bool = False) -> str:
    return f"""CASE
      WHEN {dataflow_phase_failed_sql(alias, "source", native=native)} THEN 'source'
      WHEN {dataflow_phase_failed_sql(alias, "transform", native=native)} THEN 'transform'
      WHEN {dataflow_phase_failed_sql(alias, "destination", native=native)} THEN 'destination'
      WHEN {dataflow_phase_status_sql(alias, "overhead", native=native)} = 'failed' THEN 'overhead'
      ELSE 'unknown'
    END"""


def failure_category_sql(message_expression: str) -> str:
    lowered = f"LOWER(TRIM({message_expression}))"
    cases = [
        f"WHEN {lowered} = '' OR {lowered} = 'none' THEN 'Unspecified'",
        f"WHEN regexp_full_match({lowered}, '{_sql_literal(_LIST_LIKE_PATTERN)}') THEN 'Other'",
    ]
    for rule in FAILURE_RULES:
        combined = "|".join(rule.patterns)
        exclusions = "|".join(rule.excludes)
        exclusion_sql = (
            f" AND NOT regexp_matches({lowered}, '{_sql_literal(exclusions)}')"
            if exclusions else ""
        )
        cases.append(
            f"WHEN regexp_matches({lowered}, '{_sql_literal(combined)}'){exclusion_sql} "
            f"THEN '{_sql_literal(rule.category)}'"
        )
    return "CASE " + " ".join(cases) + " ELSE 'Other' END"


def failure_rule_id_sql(message_expression: str) -> str:
    return _failure_rule_value_sql(message_expression, "rule_id")


def failure_tags_sql(
    all_evidence_expression: str,
    primary_category_expression: str,
) -> str:
    lowered = f"LOWER(TRIM({all_evidence_expression}))"
    matched_lists: list[str] = []
    for rule in FAILURE_RULES:
        combined = "|".join(rule.patterns)
        exclusions = "|".join(rule.excludes)
        exclusion_sql = (
            f" AND NOT regexp_matches({lowered}, '{_sql_literal(exclusions)}')"
            if exclusions else ""
        )
        values = [rule.category, *rule.tags]
        list_sql = "[" + ", ".join(f"'{_sql_literal(value)}'" for value in values) + "]"
        matched_lists.append(
            f"CASE WHEN regexp_matches({lowered}, '{_sql_literal(combined)}')"
            f"{exclusion_sql} THEN {list_sql} ELSE [] END"
        )
    flattened = "flatten([" + ", ".join(matched_lists) + "])"
    return (
        "list_slice(list_sort(list_distinct(list_filter("
        f"{flattened}, tag -> tag <> {primary_category_expression}))), 1, 5)"
    )


def _failure_rule_value_sql(message_expression: str, field: str) -> str:
    lowered = f"LOWER(TRIM({message_expression}))"
    cases: list[str] = []
    for rule in FAILURE_RULES:
        combined = "|".join(rule.patterns)
        exclusions = "|".join(rule.excludes)
        exclusion_sql = (
            f" AND NOT regexp_matches({lowered}, '{_sql_literal(exclusions)}')"
            if exclusions else ""
        )
        value = getattr(rule, field)
        cases.append(
            f"WHEN regexp_matches({lowered}, '{_sql_literal(combined)}')"
            f"{exclusion_sql} THEN '{_sql_literal(str(value))}'"
        )
    return "CASE " + " ".join(cases) + " ELSE NULL END"


def _message_sql(alias: str, phase: str, *, native: bool = False) -> str:
    prefix = f"{alias}." if alias else ""
    scalar = f"NULLIF(TRIM(CAST({prefix}{phase}_error_message AS VARCHAR)), '')"
    if not native:
        return scalar
    typed = f"NULLIF(TRIM(CAST(({prefix}attempts[-1].{phase}).message AS VARCHAR)), '')"
    return f"CASE WHEN TRY_CAST({prefix}log_schema_version AS INTEGER) IN (6, 7) THEN {typed} ELSE {scalar} END"


def _root_message_sql(alias: str, *, native: bool) -> str:
    prefix = f"{alias}." if alias else ""
    error = f"NULLIF(TRIM(CAST({prefix}error_message AS VARCHAR)), '')"
    if not native:
        return error
    message = f"NULLIF(TRIM(CAST({prefix}message AS VARCHAR)), '')"
    return f"CASE WHEN TRY_CAST({prefix}log_schema_version AS INTEGER) IN (6, 7) THEN {message} ELSE {error} END"


def _normalized_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _attempt_phase_value(attempt: dict[str, Any] | None, phase: str, field: str) -> Any:
    if not attempt:
        return None
    member = attempt.get(phase)
    return member.get(field) if isinstance(member, dict) else None


def _normalized_status_sql(expression: str) -> str:
    return f"LOWER(TRIM(COALESCE(CAST({expression} AS VARCHAR), '')))"


def _overall_status(row: dict[str, Any]) -> str:
    return _normalized_status(row.get("normalized_status") or row.get("status"))


def _has_message(value: Any) -> bool:
    return value is not None and str(value).strip() not in {"", "[]", "{}"}


def normalized_failure_message_sql(message_expression: str) -> str:
    return (
        "LEFT(regexp_replace(regexp_replace(regexp_replace(regexp_replace("
        f"LOWER(TRIM({message_expression})), "
        "'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '<uuid>', 'g'), "
        "'\\b[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[t ][0-9:.+\\-z]+)?\\b', '<timestamp>', 'g'), "
        "'\\b[0-9]+\\b', '<number>', 'g'), '\\s+', ' ', 'g'), 360)"
    )


def _sql_literal(value: str) -> str:
    return value.replace("'", "''")

from __future__ import annotations

from typing import Any

from datacoolie_studio.domains.monitoring.metrics.failure_taxonomy import (
    FAILURE_RULES,
    FailureClassification,
    categorize_failure,
    classify_failure,
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
    explicit = tuple(
        phase
        for phase in DATAFLOW_EXECUTION_PHASES
        if _normalized_status(row.get(f"{phase}_status")) == "failed"
    )
    if explicit:
        return explicit
    return ("overhead",)


def dataflow_failure_phase_and_message(row: dict[str, Any]) -> tuple[str, str]:
    """Select one causal phase and its best message from canonical status rules."""
    failed_phases = dataflow_failed_phases(row)
    if not failed_phases:
        return "unknown", ""
    phase = failed_phases[0]
    phase_message = row.get(f"{phase}_error_message") if phase != "overhead" else None
    if _has_message(phase_message):
        return phase, str(phase_message)
    for key in ("error_messages", "error_message"):
        if _has_message(row.get(key)):
            return phase, str(row[key])
    return phase, ""


def dataflow_phase_status_sql(alias: str, phase: str) -> str:
    """Generate the SQL status for one phase from the same failure-phase contract."""
    prefix = f"{alias}." if alias else ""
    if phase in DATAFLOW_EXECUTION_PHASES:
        return f"LOWER(NULLIF(TRIM(CAST({prefix}{phase}_status AS VARCHAR)), ''))"
    if phase != "overhead":
        raise ValueError(f"Unsupported dataflow failure phase: {phase}")
    explicit_failed = " OR ".join(
        f"COALESCE({dataflow_phase_status_sql(alias, item)}, '') = 'failed'"
        for item in DATAFLOW_EXECUTION_PHASES
    )
    return (
        f"CASE WHEN {_normalized_status_sql(f'{prefix}normalized_status')} = 'failed' "
        f"AND NOT ({explicit_failed}) THEN 'failed' END"
    )


def dataflow_phase_failed_sql(alias: str, phase: str) -> str:
    prefix = f"{alias}." if alias else ""
    return (
        f"{_normalized_status_sql(f'{prefix}normalized_status')} = 'failed' AND "
        f"COALESCE({dataflow_phase_status_sql(alias, phase)}, '') = 'failed'"
    )


def failure_message_sql(alias: str) -> str:
    source = _message_sql(alias, "source")
    transform = _message_sql(alias, "transform")
    destination = _message_sql(alias, "destination")
    root = f"NULLIF(TRIM(CAST({alias}.error_message AS VARCHAR)), '')"
    source_failed = dataflow_phase_failed_sql(alias, "source")
    transform_failed = dataflow_phase_failed_sql(alias, "transform")
    destination_failed = dataflow_phase_failed_sql(alias, "destination")
    return f"""CASE
      WHEN {source_failed} THEN COALESCE({source}, {root}, '')
      WHEN {transform_failed} THEN COALESCE({transform}, {root}, '')
      WHEN {destination_failed} THEN COALESCE({destination}, {root}, '')
      WHEN {dataflow_phase_status_sql(alias, "overhead")} = 'failed' THEN COALESCE({root}, '')
      ELSE '' END"""


def failure_all_messages_sql(alias: str) -> str:
    return (
        "CONCAT_WS('\n', "
        f"{_message_sql(alias, 'source')}, {_message_sql(alias, 'transform')}, "
        f"{_message_sql(alias, 'destination')}, "
        f"NULLIF(TRIM(CAST({alias}.error_message AS VARCHAR)), ''))"
    )


def failure_phase_sql(alias: str) -> str:
    return f"""CASE
      WHEN {dataflow_phase_failed_sql(alias, "source")} THEN 'source'
      WHEN {dataflow_phase_failed_sql(alias, "transform")} THEN 'transform'
      WHEN {dataflow_phase_failed_sql(alias, "destination")} THEN 'destination'
      WHEN {dataflow_phase_status_sql(alias, "overhead")} = 'failed' THEN 'overhead'
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


def _message_sql(alias: str, phase: str) -> str:
    return f"NULLIF(TRIM(CAST({alias}.{phase}_error_message AS VARCHAR)), '')"


def _normalized_status(value: Any) -> str:
    return str(value or "").strip().lower()


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

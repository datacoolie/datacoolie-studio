"""Typed observation expressions for Monitoring views."""
from __future__ import annotations

from datacoolie_studio.domains.analytics.sql import quote_identifier

METRIC_PATHS = {
    "source_rows_read": "rows_read",
    **{f"destination_{key}": key for key in (
        "rows_written", "rows_inserted", "rows_updated", "rows_deleted",
        "files_added", "files_removed", "bytes_added", "bytes_removed", "bytes_saved",
    )},
}
def field_expression(
    column: str,
    data_type: str,
    *,
    raw: str = "r",
    available=None,
    _presence: bool = False,
) -> str:
    """Derive report aliases from typed source columns, never whole-record JSON."""
    def ref(name): return f"{raw}.{quote_identifier(name)}"
    if column == "raw_record_json":
        return "NULL::VARCHAR"
    if column.endswith("_present"):
        # Native storage deliberately collapses an omitted optional member and
        # an explicit JSON null.  Presence-derived report flags therefore mean
        # that the stored value is non-null; they never consult a sidecar path
        # list which is absent from native tables.
        value_column = column.removesuffix("_present")
        value = field_expression(
            value_column,
            data_type,
            raw=raw,
            available=available,
            _presence=True,
        )
        return f"{value} IS NOT NULL"
    if column in METRIC_PATHS:
        return f"{ref('metrics')}.{quote_identifier(METRIC_PATHS[column])}"
    phase_fields = {"start_time", "end_time", "duration_seconds", "status", "message"}
    value = None
    for phase in ("source", "transform", "destination"):
        if column.startswith(phase + "_"):
            suffix = column.removeprefix(phase + "_")
            fields = phase_fields | ({"operation_type", "operation_details"} if phase == "destination" else set())
            if suffix in fields or column == "source_action":
                member = "source_action" if column == "source_action" else suffix
                value = f"{ref('attempts')}[-1].{quote_identifier(phase)}.{quote_identifier(member)}"
    if column == "transformers_applied":
        value = f"{ref('attempts')}[-1].transform.transformers_applied"
    if column in {"error_message", "source_error_message", "transform_error_message", "destination_error_message"}:
        base = raw if column == "error_message" else f"{ref('attempts')}[-1].{quote_identifier(column.removesuffix('_error_message'))}"
        return f"CASE WHEN {base}.status = 'failed' THEN {base}.message END"
    if column == "error_message_truncated":
        column = "message_truncated"
    if column == "overhead_duration_seconds":
        if available is not None and "attempts" not in available:
            return "NULL::DOUBLE"
        attempts = ref("attempts")
        final_attempt = f"{attempts}[-1]"
        return f"""CASE
          WHEN {attempts} IS NULL OR len({attempts}) = 0 THEN NULL
          WHEN TRY_CAST({final_attempt}.duration_seconds AS DOUBLE) IS NULL THEN NULL
          WHEN ({final_attempt}.source IS NOT NULL AND TRY_CAST({final_attempt}.source.duration_seconds AS DOUBLE) IS NULL)
            OR ({final_attempt}.transform IS NOT NULL AND TRY_CAST({final_attempt}.transform.duration_seconds AS DOUBLE) IS NULL)
            OR ({final_attempt}.destination IS NOT NULL AND TRY_CAST({final_attempt}.destination.duration_seconds AS DOUBLE) IS NULL)
            THEN NULL
          ELSE GREATEST(
            0,
            TRY_CAST({final_attempt}.duration_seconds AS DOUBLE)
              - CASE WHEN {final_attempt}.source IS NULL THEN 0 ELSE COALESCE(TRY_CAST({final_attempt}.source.duration_seconds AS DOUBLE), 0) END
              - CASE WHEN {final_attempt}.transform IS NULL THEN 0 ELSE COALESCE(TRY_CAST({final_attempt}.transform.duration_seconds AS DOUBLE), 0) END
              - CASE WHEN {final_attempt}.destination IS NULL THEN 0 ELSE COALESCE(TRY_CAST({final_attempt}.destination.duration_seconds AS DOUBLE), 0) END
          )
        END"""
    if value is None:
        if available is not None and column not in available:
            return "NULL" if _presence else f"NULL::{data_type}"
        value = ref(column)
    if _presence:
        return value
    if data_type == "TIMESTAMP WITH TIME ZONE":
        data_type = "TIMESTAMPTZ"
    if data_type in {"TIMESTAMPTZ", "DOUBLE"}:
        return f"TRY_CAST({value} AS {data_type})"
    if data_type == "VARCHAR":
        return f"CAST({value} AS VARCHAR)"
    return value

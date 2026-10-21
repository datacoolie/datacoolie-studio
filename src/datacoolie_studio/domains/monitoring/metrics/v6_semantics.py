"""Small policies for reading selected native v6 evidence.

Analytics stores fixed v6 members as DuckDB STRUCT/LIST values.  Monitoring
receives those selected members from a bounded query and decodes JSON only for
dynamic leaves (typed watermark values and phase detail fields).  A complete
execution record is never reconstructed or reparsed here.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from threading import RLock
from typing import Any

from datacoolie_studio.core.time import parse_utc_datetime


_PHASES = ("source", "transform", "destination")
_FAILURE_STATUSES = {"failed", "failure", "error", "cancelled", "canceled"}
_DATE_FOLDER_PARTITION_KEY = "__date_folder_partition__"
_ISO_FRACTION_RE = re.compile(r"[Tt ]\d{2}:\d{2}:\d{2}\.(\d+)")
_FRESHNESS_REGISTRATION_LOCK = RLock()


def _decode_json_leaf(value: Any) -> Any:
    """Decode one selected DuckDB JSON leaf without narrowing its numbers."""

    if not isinstance(value, str) or not value.strip():
        return value
    try:
        return json.loads(value, parse_int=int, parse_float=Decimal)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def _normalise(value: Any) -> Any:
    """Convert DuckDB nested values to ordinary Python containers.

    JSON-typed fields are returned as text by some DuckDB Python versions.  The
    caller passes those fields through ``_decode_json_leaf`` explicitly; known
    STRUCT strings are left alone so authored strings which happen to contain
    JSON are not reinterpreted.
    """

    if isinstance(value, dict):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    return value


def _mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return _normalise(value)


def _dynamic(value: Any) -> Any:
    """Decode an explicitly selected JSON-typed leaf recursively."""

    return _normalise(_decode_json_leaf(value))


def _lossless_json_text(value: Any) -> str | None:
    """Serialize one selected bound without narrowing Decimal or large integers."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return str(value) if value.is_finite() else json.dumps(str(value), ensure_ascii=False)
    if isinstance(value, float):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (date, datetime)):
        return json.dumps(value.isoformat(), ensure_ascii=False)
    if isinstance(value, dict):
        members = []
        for key, item in value.items():
            members.append(
                f"{json.dumps(str(key), ensure_ascii=False)}:{_lossless_json_text(item)}"
            )
        return "{" + ",".join(members) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_lossless_json_text(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def _bound_empty(value: Any) -> bool:
    return value is None or value == {}


def _source_configure_mapping(row: dict[str, Any]) -> dict[str, Any]:
    value = _dynamic(row.get("source_configure"))
    return value if isinstance(value, dict) else {}


def _source_watermark_keys(row: dict[str, Any]) -> set[str]:
    value = _dynamic(row.get("source_watermark_columns"))
    if isinstance(value, dict):
        return {str(key) for key in value}
    if isinstance(value, (list, tuple)):
        return {str(key) for key in value if key is not None}
    return set()


def _temporal_bound_supported(row: dict[str, Any], key: str) -> bool:
    """Whether source evidence authorizes engine-like temporal normalization."""

    if key == _DATE_FOLDER_PARTITION_KEY:
        return str(row.get("source_connection_type") or "").lower() == "file" or str(
            row.get("source_format") or ""
        ).lower() in {"csv", "json", "jsonl", "parquet", "avro", "excel"}
    source_format = str(row.get("source_format") or "").lower()
    source_type = str(row.get("source_connection_type") or "").lower()
    if source_format == "sql" or source_type == "database":
        return key in _source_watermark_keys(row)
    if source_format == "api" or source_type == "api":
        mapping = _source_configure_mapping(row).get("range_param_mapping")
        if not isinstance(mapping, dict):
            return False
        binding = mapping.get(key)
        if not isinstance(binding, dict):
            return False
        return any(
            isinstance(binding.get(bound), dict)
            and bool(binding[bound].get("name"))
            and bool(binding[bound].get("location"))
            for bound in ("lower", "upper")
        )
    return False


def _parse_temporal_bound(value: Any) -> tuple[str, Any] | None:
    """Parse a supported plain/tagged temporal value without truncation."""

    if isinstance(value, dict) and len(value) == 1:
        tag, raw = next(iter(value.items()))
        if tag not in {"__date__", "__datetime__", "__time__"} or not isinstance(raw, str):
            return None
        if tag == "__date__":
            try:
                return "date", date.fromisoformat(raw)
            except ValueError:
                return None
        if tag == "__datetime__":
            parsed = _parse_iso_datetime(raw)
            return ("datetime", parsed) if parsed is not None else None
        try:
            from datetime import time
            return "time", time.fromisoformat(raw)
        except ValueError:
            return None
    if not isinstance(value, str):
        return None
    if _ISO_FRACTION_RE.search(value):
        fraction = _ISO_FRACTION_RE.search(value)
        if fraction and len(fraction.group(1)) > 6:
            return None
    try:
        return "date", date.fromisoformat(value)
    except ValueError:
        pass
    parsed = _parse_iso_datetime(value)
    return ("datetime", parsed) if parsed is not None else None


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc)
    return parsed


def _tagged_temporal(value: Any) -> str | None:
    if not isinstance(value, dict) or len(value) != 1:
        return None
    tag = next(iter(value))
    return tag if tag in {"__date__", "__datetime__", "__time__"} else None


def _temporal_outcome(left: Any, right: Any, row: dict[str, Any], key: str | None) -> str | None:
    """Compare plain and tagged temporal values without losing their type."""

    left_tag = _tagged_temporal(left)
    right_tag = _tagged_temporal(right)
    if left_tag is None and right_tag is None:
        return None
    if left_tag is not None and right_tag is not None and left_tag != right_tag:
        return "unknown"
    left_temporal = _parse_temporal_bound(left)
    right_temporal = _parse_temporal_bound(right)
    if left_temporal is None or right_temporal is None:
        return "unknown"
    left_kind, left_value = left_temporal
    right_kind, right_value = right_temporal
    if left_kind == right_kind:
        if left_kind == "datetime":
            left_aware = left_value.tzinfo is not None
            right_aware = right_value.tzinfo is not None
            if left_aware != right_aware:
                return "unknown"
        try:
            return "equal" if left_value == right_value else "different"
        except (TypeError, ValueError):
            return "unknown"
    if {left_kind, right_kind} != {"date", "datetime"}:
        return "unknown"
    # A date-only string may be materialized as midnight datetime text by a
    # supported reader. Tagged date and tagged datetime remain incomparable.
    if left_tag is not None and right_tag is not None:
        return "unknown"
    if key is None or not _temporal_bound_supported(row, key):
        return "unknown"
    datetime_value = left_value if left_kind == "datetime" else right_value
    date_value = left_value if left_kind == "date" else right_value
    if datetime_value.tzinfo is not None:
        return "unknown"
    try:
        at_midnight = datetime_value == datetime_value.replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        return "equal" if at_midnight and datetime_value.date() == date_value else "different"
    except (TypeError, ValueError):
        return "unknown"


def _compare_read_bound_values(left: Any, right: Any, row: dict[str, Any], key: str | None = None) -> str:
    """Return equal/different/unknown for one reader-bound value."""

    temporal_outcome = _temporal_outcome(left, right, row, key)
    if temporal_outcome is not None:
        return temporal_outcome
    if isinstance(left, bool) or isinstance(right, bool):
        return "equal" if isinstance(left, bool) and isinstance(right, bool) and left == right else "different" if isinstance(left, bool) and isinstance(right, bool) else "unknown"
    if isinstance(left, (int, Decimal, float)) or isinstance(right, (int, Decimal, float)):
        if not isinstance(left, (int, Decimal, float)) or not isinstance(right, (int, Decimal, float)):
            return "unknown"
        try:
            return "equal" if left == right else "different"
        except (TypeError, ValueError, InvalidOperation):
            return "unknown"
    if isinstance(left, str) and isinstance(right, str):
        if left == right:
            return "equal"
        if key and _temporal_bound_supported(row, key):
            left_temporal = _parse_temporal_bound(left)
            right_temporal = _parse_temporal_bound(right)
            if left_temporal is None and right_temporal is None:
                return "different"
            if left_temporal is None or right_temporal is None:
                return "unknown"
            left_kind, left_value = left_temporal
            right_kind, right_value = right_temporal
            if left_kind == right_kind:
                return "equal" if left_value == right_value else "different"
            if {left_kind, right_kind} == {"date", "datetime"}:
                datetime_value = left_value if left_kind == "datetime" else right_value
                date_value = left_value if left_kind == "date" else right_value
                if datetime_value.tzinfo is not None or datetime_value.timetz() != datetime_value.replace(hour=0, minute=0, second=0, microsecond=0).timetz():
                    return "unknown"
                return "equal" if datetime_value.date() == date_value else "different"
            return "unknown"
        return "different"
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return "unknown"
        outcomes = [_compare_read_bound_values(left[key], right[key], row, str(key)) for key in left]
        if "unknown" in outcomes:
            return "unknown"
        return "equal" if all(outcome == "equal" for outcome in outcomes) else "different"
    if isinstance(left, (list, tuple)) or isinstance(right, (list, tuple)):
        return "equal" if type(left) is type(right) and left == right else "unknown"
    if type(left) is not type(right):
        return "unknown"
    return "equal" if left == right else "unknown"


def read_boundary_adjustment(
    row: dict[str, Any], *, _prepared: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify the final reader lower bound against its resolved selection."""

    def result(state: str, basis: str | None, reason: str, input_value: Any = None, effective: Any = None):
        return {
            "adjustment_state": state,
            "adjustment_basis": basis,
            "adjustment_reason": reason,
            "adjustment_input_json": _lossless_json_text(input_value) if input_value is not None else None,
            "adjustment_effective_json": _lossless_json_text(effective) if effective is not None else None,
        }

    operation = str(row.get("operation_type") or "").strip().lower()
    if operation and operation != "etl":
        return result("not_applied", "operation", "non_etl_operation")
    if _prepared is None:
        attempt = final_attempt(row)
        if attempt is None:
            return result("unknown", None, "attempt_unavailable")
        source = _mapping(attempt.get("source"))
        selection = _selection_with_decoded_bounds(source.get("selection")) if source else None
        effective = _dynamic(selection.get("lower")) if selection is not None else None
        request = _mapping(row.get("read_request")) or {}
        watermark = _mapping(attempt.get("watermark"))
    else:
        attempt = _prepared["attempt"]
        if attempt is None:
            return result("unknown", None, "attempt_unavailable")
        source = _prepared["source"]
        selection = _prepared["selection"]
        effective = _prepared["effective_lower"]
        request = _prepared["raw_request"] or {}
        watermark = _prepared["watermark"]
    if selection is None:
        return result("unknown", None, "selection_unavailable")
    mode = str(selection.get("mode") or "").strip().lower()
    if mode in {"full", "range"}:
        return result("not_applied", "mode", f"{mode}_mode")
    if mode != "incremental":
        return result("unknown", None, "unsupported_mode")
    if "lower" not in selection:
        return result("unknown", None, "resolved_lower_missing")
    load_status = str((watermark or {}).get("load_status") or "").strip().lower()
    loaded_present = isinstance(watermark, dict) and "loaded_watermark" in watermark
    input_value = None
    basis = None
    if "watermark_start" in request and request.get("watermark_start") is not None:
        input_value = (
            _prepared["request_watermark_start"]
            if _prepared is not None
            else _dynamic(request.get("watermark_start"))
        )
        basis = "request"
    elif load_status in {"loaded", "reused"} and loaded_present:
        input_value = (
            _prepared["loaded"]
            if _prepared is not None
            else _dynamic(watermark.get("loaded_watermark"))
        )
        basis = "loaded_watermark"
    elif _bound_empty(effective):
        return result("unknown", None, "input_lower_unavailable", None, effective)
    else:
        return result("unknown", None, "input_lower_unavailable", None, effective)
    if _bound_empty(input_value) and _bound_empty(effective):
        return result("not_applied", basis, "no_lower_bound", input_value, effective)
    if _bound_empty(input_value):
        return result("unknown", basis, "input_lower_empty", input_value, effective)
    if _bound_empty(effective):
        return result("unknown", basis, "resolved_lower_empty", input_value, effective)
    if not isinstance(input_value, dict) or not isinstance(effective, dict) or set(input_value) != set(effective):
        return result("unknown", basis, "incomparable_bound_shape", input_value, effective)
    outcomes = [_compare_read_bound_values(input_value[key], effective[key], row, str(key)) for key in input_value]
    if "unknown" in outcomes:
        return result("unknown", basis, "incomparable_bound_value", input_value, effective)
    state = "unchanged" if all(outcome == "equal" for outcome in outcomes) else "adjusted"
    return result(state, basis, "equal" if state == "unchanged" else "different", input_value, effective)


def is_v6_row(row: dict[str, Any]) -> bool:
    value = row.get("log_schema_version")
    try:
        return int(value) == 6 if value is not None else False
    except (TypeError, ValueError):
        return False


def is_supported_log_row(row: dict[str, Any]) -> bool:
    """Whether the row uses the fixed native attempt/phase contract."""

    value = row.get("log_schema_version")
    try:
        return int(value) in (6, 7) if value is not None else False
    except (TypeError, ValueError):
        return False


def attempts(row: dict[str, Any]) -> list[dict[str, Any]]:
    values = row.get("attempts")
    if not isinstance(values, (list, tuple)):
        return []
    return [item for item in (_mapping(value) for value in values) if item is not None]


def final_attempt(row: dict[str, Any]) -> dict[str, Any] | None:
    values = attempts(row)
    return values[-1] if values else None


def recovered_attempt_failures(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Return failed attempt evidence which did not become terminal root health."""

    values = attempts(row)
    root_status = str(row.get("status") or "").lower()
    if root_status in _FAILURE_STATUSES:
        return []
    return [
        {
            "attempt_number": item.get("attempt_number"),
            "status": item.get("status"),
            "message": item.get("message"),
            "start_time": item.get("start_time"),
            "end_time": item.get("end_time"),
            "duration_seconds": item.get("duration_seconds"),
        }
        for item in values
        if str(item.get("status") or "").lower() in _FAILURE_STATUSES
    ]


def failed_attempts(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in attempts(row)
        if str(item.get("status") or "").lower() in _FAILURE_STATUSES
    ]


def _phase_interval(phase: dict[str, Any] | None) -> tuple[datetime, datetime] | None:
    if not isinstance(phase, dict):
        return None
    start = parse_utc_datetime(phase.get("start_time"))
    end = parse_utc_datetime(phase.get("end_time"))
    if start is None or end is None or end < start:
        return None
    return start, end


def _attempt_interval(attempt: dict[str, Any] | None) -> tuple[datetime, datetime] | None:
    return _phase_interval(attempt)


def phase_timing(row: dict[str, Any]) -> dict[str, Any]:
    """Describe whether final-attempt phase percentages are safe to calculate."""

    attempt = final_attempt(row)
    if attempt is None:
        return {"available": False, "reason": "no_attempt", "phases": {}}
    attempt_interval = _attempt_interval(attempt)
    phases: dict[str, dict[str, Any]] = {}
    if attempt_interval is None:
        return {"available": False, "reason": "invalid_attempt_interval", "phases": phases}
    attempt_start, attempt_end = attempt_interval
    interval_duration = (attempt_end - attempt_start).total_seconds()
    reported_attempt_duration = _reported_duration(attempt.get("duration_seconds"))
    attempt_duration_consistent = (
        reported_attempt_duration is not None
        and abs(reported_attempt_duration - interval_duration) <= 0.001
    )
    previous_end: datetime | None = None
    complete = attempt_duration_consistent
    for phase_name in _PHASES:
        phase = attempt.get(phase_name)
        if phase is None and phase_name != "destination" and str(row.get("operation_type") or "").strip().lower() == "maintenance":
            continue
        interval = _phase_interval(phase)
        if interval is None:
            complete = False
            continue
        start, end = interval
        contained = attempt_start <= start <= end <= attempt_end
        non_overlapping = previous_end is None or start >= previous_end
        reported_duration = _reported_duration(phase.get("duration_seconds"))
        consistent_duration = (
            reported_duration is not None
            and abs(reported_duration - (end - start).total_seconds()) <= 0.001
        )
        if not contained or not non_overlapping or not consistent_duration:
            complete = False
        phases[phase_name] = {
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "duration_seconds": round((end - start).total_seconds(), 6),
            "contained": contained,
            "non_overlapping": non_overlapping,
            "consistent_duration": consistent_duration,
            "status": phase.get("status"),
            "message": phase.get("message"),
        }
        previous_end = end
    return {
        "available": complete and bool(phases),
        "reason": None if complete and bool(phases) else (
            "inconsistent_attempt_duration"
            if not attempt_duration_consistent
            else "incomplete_or_inconsistent_phases"
        ),
        "attempt_start_time": attempt_start.isoformat(),
        "attempt_end_time": attempt_end.isoformat(),
        "attempt_duration_seconds": round(interval_duration, 6),
        "reported_attempt_duration_seconds": reported_attempt_duration,
        "attempt_duration_consistent": attempt_duration_consistent,
        "phases": phases,
    }


def final_overhead_duration(row: dict[str, Any]) -> float | None:
    """Subtract only applicable phases after validating final-attempt timing."""
    timing = phase_timing(row)
    if not timing.get("available"):
        return None
    return round(max(0.0, timing["attempt_duration_seconds"] - sum(
        phase["duration_seconds"] for phase in timing["phases"].values()
    )), 6)


def _reported_duration(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result >= 0 else None


def final_phase_duration(row: dict[str, Any], phase: str) -> float | None:
    if phase not in _PHASES:
        return None
    evidence = phase_timing(row)
    value = evidence.get("phases", {}).get(phase, {}).get("duration_seconds")
    return float(value) if value is not None else None


def root_duration(row: dict[str, Any]) -> float | None:
    value = row.get("duration_seconds")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def attempt_duration(row: dict[str, Any]) -> float | None:
    attempt = final_attempt(row)
    interval = _attempt_interval(attempt)
    if interval is None:
        return None
    return max(0.0, (interval[1] - interval[0]).total_seconds())


def _canonical(value: Any) -> Any:
    # JSON has one numeric value space: integer and fractional spellings of
    # the same number compare equally.  Keep booleans separate because bool
    # is an int subclass in Python and ``true`` must not equal ``1``.
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float, Decimal)):
        return ("number", Decimal(str(value)))
    if isinstance(value, dict):
        return ("object", tuple((key, _canonical(item)) for key, item in sorted(value.items())))
    if isinstance(value, list):
        return ("array", tuple(_canonical(item) for item in value))
    return (type(value).__name__, value)


def exact_equal(left: Any, right: Any) -> bool | None:
    if left is None or right is None:
        return None
    return _canonical(left) == _canonical(right)


def _typed_scalar(value: Any) -> tuple[str, Any] | None:
    """Return a monotonic scalar only for explicitly typed scalar evidence."""

    if isinstance(value, bool):
        return "bool", value
    if isinstance(value, (int, float, Decimal)):
        return "number", value
    if isinstance(value, str):
        return None
    if isinstance(value, dict) and len(value) == 1:
        key, inner = next(iter(value.items()))
        if key == "__decimal__":
            return key, Decimal(inner)
        if key == "__datetime__":
            return key, parse_utc_datetime(inner)
        if key in {"__date__", "__time__"}:
            return key, str(inner)
    return None


def _source_temporal_scalar(value: Any) -> tuple[str, Any] | None:
    """Parse ISO temporal strings with the producer's ordering semantics.

    The API reader can mark a watermark column as source-ordered even though
    its JSON representation is a plain string.  Keep that ordering typed: a
    date stays a date, offset-aware datetimes compare as UTC instants, and
    naive datetimes remain naive so they cannot be silently mixed with aware
    values.
    """

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if "T" not in text and " " not in text:
            return "date", date.fromisoformat(text)
        parsed = datetime.fromisoformat(text.replace(" ", "T"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return "datetime", parsed
    except (ValueError, OverflowError):
        return None


def _typed_map_scalar(value: Any) -> tuple[str, Any] | None:
    """Order only singleton map values with supported producer semantics."""

    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return _source_temporal_scalar(value)
    if isinstance(value, (int, float, Decimal)):
        try:
            numeric = Decimal(str(value))
            if not numeric.is_finite():
                return None
            return "number", numeric
        except (InvalidOperation, ValueError):
            return None
    if not isinstance(value, dict) or len(value) != 1:
        return None
    kind, raw = next(iter(value.items()))
    try:
        if kind == "__decimal__":
            numeric = Decimal(raw)
            return ("decimal", numeric) if numeric.is_finite() else None
        if kind == "__date__":
            return "date", date.fromisoformat(str(raw))
        if kind == "__datetime__":
            temporal = _source_temporal_scalar(raw)
            return temporal if temporal is not None and temporal[0] == "datetime" else None
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None
    return None


def watermark_is_configured(
    *,
    request_configured: bool,
    source_watermark_columns: Any,
    watermark_present: bool,
    load_status: Any,
    save_status: Any,
    merge_base: Any,
    candidate: Any,
    loaded: Any,
    to_save: Any,
    saved: Any,
) -> bool:
    """Whether native evidence demonstrates configured watermark use.

    Neutral outcomes such as ``no_candidate`` and ``not_attempted`` by
    themselves do not establish applicability.
    """

    if request_configured or _source_watermark_configured(source_watermark_columns):
        return True
    if any(
        _has_watermark_value(value)
        for value in (merge_base, candidate, loaded, to_save, saved)
    ):
        return True
    if not watermark_present:
        return False
    meaningful = {
        "loaded", "reused", "load_error", "saved", "save_error",
        "error", "failed", "cancelled", "canceled",
    }
    return any(
        str(value or "").strip().lower() in meaningful
        for value in (load_status, save_status)
    )


def watermark_issue_state(
    *,
    configured: bool,
    movement_state: str,
    persistence_state: str,
    load_status: Any,
    save_status: Any,
) -> str:
    """Separate actionable persistence errors from unavailable movement."""

    statuses = {str(value or "").strip().lower() for value in (load_status, save_status)}
    if statuses & {"load_error", "save_error", "error", "failed", "failure", "cancelled", "canceled"}:
        return "error"
    if not configured:
        return "none"
    if persistence_state == "unknown" or (
        movement_state == "unknown"
        and persistence_state not in {"no_candidate", "not_attempted", "not_requested"}
    ):
        return "insufficient_evidence"
    return "none"


def _has_watermark_value(value: Any) -> bool:
    """Treat empty containers as absent while retaining zero, false and explicit null members."""

    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple)):
        return bool(value)
    return True


def persisted_movement(
    baseline: Any,
    saved: Any,
    save_status: str | None,
    configured: bool,
    *,
    baseline_present: bool | None = None,
    candidate_kind: str | None = None,
    candidate_values: Any = None,
) -> str:
    """Classify persisted movement against the save-time merge baseline.

    ``baseline`` is deliberately named independently from the loaded state:
    the producer captures ``merge_base`` immediately before saving.  The
    optional ``baseline_present`` flag lets callers distinguish an omitted
    merge-base field from an explicitly stored JSON ``null``.  A positional
    ``None`` remains compatible with the old helper contract by treating it as
    an explicit empty baseline unless a caller supplies ``False``.
    """

    if not configured:
        return "not_configured"
    normalized_save_status = str(save_status or "").strip().lower()
    if normalized_save_status not in {"saved", "success", "succeeded"} or saved is None:
        return "unknown"
    if baseline_present is False:
        return "unknown"
    if baseline is None:
        return "initialized"
    if exact_equal(baseline, saved):
        return "unchanged"
    kind = str(candidate_kind or "").strip().lower()
    if kind in {"opaque", "metadata"}:
        return "changed"
    try:
        scalar_left, scalar_right = _typed_scalar(baseline), _typed_scalar(saved)
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return "changed"
    if scalar_left is not None or scalar_right is not None:
        if (
            scalar_left is None
            or scalar_right is None
            or scalar_left[0] != scalar_right[0]
            or scalar_left[0] == "bool"
        ):
            return "changed"
        try:
            return "advanced" if scalar_right[1] > scalar_left[1] else "changed"
        except (TypeError, ValueError, InvalidOperation):
            return "changed"
    if isinstance(baseline, dict) or isinstance(saved, dict):
        if not isinstance(baseline, dict) or not isinstance(saved, dict):
            return "changed"
        if len(baseline) != 1 or baseline.keys() != saved.keys():
            return "changed"
        if not isinstance(candidate_values, dict) or candidate_values.keys() != baseline.keys():
            return "changed"
        if kind not in {"observed_max", "request_end"}:
            return "changed"
        key = next(iter(baseline))
        if exact_equal(saved[key], candidate_values[key]) is not True:
            return "changed"
        left, right = _typed_map_scalar(baseline[key]), _typed_map_scalar(saved[key])
        if left is None or right is None or left[0] != right[0]:
            return "changed"
        try:
            return "advanced" if right[1] > left[1] else "changed"
        except (TypeError, ValueError, InvalidOperation):
            return "changed"
    return "changed"


def watermark_movement_state(
    persisted_state: str,
    row: dict[str, Any],
    *,
    configured: bool,
    persistence_state: str,
    _prepared: dict[str, Any] | None = None,
) -> str:
    """Compose persisted movement with an affirmatively empty watermark read."""

    if persisted_state != "unknown" or not configured:
        return persisted_state
    if persistence_state not in {"not_attempted", "no_candidate", "not_requested"}:
        return persisted_state
    operation = str(row.get("operation_type") or "").strip().lower()
    root_status = str(row.get("status") or "").strip().lower()
    if operation != "etl" or root_status not in {"succeeded", "skipped"}:
        return persisted_state

    if _prepared is None:
        attempt = final_attempt(row)
        source = _mapping((attempt or {}).get("source")) if attempt else None
        selection = _selection_with_decoded_bounds(source.get("selection")) if source else None
        watermark = _mapping((attempt or {}).get("watermark")) if attempt else None
    else:
        attempt = _prepared["attempt"]
        source = _prepared["source"]
        selection = _prepared["selection"]
        watermark = _prepared["watermark"]
    if attempt is None or source is None or selection is None:
        return persisted_state
    if str(attempt.get("status") or "").strip().lower() not in {"succeeded", "skipped"}:
        return persisted_state
    if str(source.get("status") or "").strip().lower() != "succeeded":
        return persisted_state
    rows_read = source.get("rows_read")
    if isinstance(rows_read, bool) or not isinstance(rows_read, int) or rows_read != 0:
        return persisted_state

    if _prepared is None:
        request = _mapping(row.get("read_request")) or {}
        resolved_range = _mapping(request.get("read_range"))
    else:
        request = _prepared["raw_request"] or {}
        resolved_range = _prepared["resolved_range"]
    if resolved_range is not None or str(selection.get("mode") or "").lower() != "incremental":
        return persisted_state
    load_status = str((watermark or {}).get("load_status") or "").strip().lower()
    save_status = str((watermark or {}).get("save_status") or "").strip().lower()
    if load_status in {"load_error", "error", "failed", "failure", "cancelled", "canceled"}:
        return persisted_state
    if save_status in {"save_error", "error", "failed", "failure", "cancelled", "canceled"}:
        return persisted_state

    if _prepared is None:
        candidate = _mapping((watermark or {}).get("candidate"))
        candidate_values = _dynamic(candidate.get("values")) if candidate else None
    else:
        candidate = _prepared["candidate"]
        candidate_values = _prepared["candidate_values"]
    if (
        _has_watermark_value((watermark or {}).get("saved_watermark"))
        or _has_watermark_value((watermark or {}).get("watermark_to_save"))
        or _has_watermark_value(candidate_values)
    ):
        return persisted_state

    selection_keys = (
        _prepared["selection_keys"]
        if _prepared is not None
        else _selected_non_null_keys(selection)
    )
    if not selection_keys:
        return persisted_state
    if _prepared is None:
        source_columns = _as_key_set(row.get("source_watermark_columns"))
        request_watermarks = {
            key
            for field in ("watermark_start", "watermark_end")
            for key in _non_null_mapping_keys(_mapping(request.get(field)))
        }
        loaded = _dynamic((watermark or {}).get("loaded_watermark"))
        loaded_keys = _non_null_mapping_keys(_mapping(loaded))
    else:
        source_columns = _prepared["source_columns"]
        request_watermarks = _prepared["request_watermarks"]
        loaded = _prepared["loaded"]
        loaded_keys = _prepared["loaded_keys"]
    attributable_keys = request_watermarks | (
        loaded_keys
        if load_status in {"loaded", "reused"}
        else set()
    )
    selected_keys = selection_keys & attributable_keys
    if not selected_keys:
        return persisted_state

    reader_format = str(row.get("source_format") or "").strip().lower()
    reader_type = str(row.get("source_connection_type") or "").strip().lower()
    if reader_format == "sql" or reader_type == "database":
        active_keys = selected_keys & source_columns
    elif reader_format == "api" or reader_type == "api":
        source_config = (
            _prepared["source_configure"]
            if _prepared is not None
            else _decode_mapping(row.get("source_configure"))
        ) or {}
        bindings = _mapping(source_config.get("range_param_mapping")) or {}
        active_keys = {
            key for key in selected_keys
            if key in bindings and _api_watermark_binding_active(bindings[key], selection, key)
        }
    else:
        # File/custom readers need reader-specific partition or binding evidence
        # that the current logs do not reliably expose.
        active_keys = set()
    return "empty" if active_keys else persisted_state


def _selected_non_null_keys(selection: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for field in ("lower", "upper"):
        keys.update(_non_null_mapping_keys(_mapping(selection.get(field))))
    return keys


def _selection_with_decoded_bounds(value: Any) -> dict[str, Any] | None:
    """Decode the dynamic lower/upper bounds of a resolved source selection."""

    selection = _mapping(value)
    if selection is None:
        return None
    for field in ("lower", "upper"):
        if field in selection:
            selection[field] = _dynamic(selection[field])
    return selection


def _non_null_mapping_keys(value: dict[str, Any] | None) -> set[str]:
    return {str(key) for key, item in (value or {}).items() if item is not None}


def _as_key_set(value: Any) -> set[str]:
    decoded = _dynamic(value)
    if isinstance(decoded, str):
        decoded = _dynamic(decoded)
    if isinstance(decoded, dict):
        return {str(key) for key in decoded}
    if isinstance(decoded, (list, tuple)):
        return {str(key) for key in decoded if key is not None}
    return set()


def _decode_mapping(value: Any) -> dict[str, Any] | None:
    decoded = _dynamic(value)
    if isinstance(decoded, str):
        decoded = _dynamic(decoded)
    return _mapping(decoded)


def _combined_dynamic(value: Any) -> Any:
    """Decode one value from the row-local packed evidence payload.

    The packed payload has already passed through ``json.loads``.  Preserve
    those ordinary Python containers by identity so the combined classifier
    does not recursively copy the same row for every scalar decision.
    Dynamic leaves still use the canonical JSON decoder, including Decimal
    parsing and the existing one-level nested-string behavior.
    """

    return _decode_json_leaf(value)


def _combined_mapping(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _combined_decode_mapping(value: Any) -> dict[str, Any] | None:
    decoded = _combined_dynamic(value)
    if isinstance(decoded, str):
        decoded = _combined_dynamic(decoded)
    return _combined_mapping(decoded)


def _combined_key_set(value: Any) -> set[str]:
    decoded_value = _combined_dynamic(value)
    if isinstance(decoded_value, str):
        decoded_value = _combined_dynamic(decoded_value)
    if isinstance(decoded_value, dict):
        return {str(key) for key in decoded_value}
    if isinstance(decoded_value, (list, tuple)):
        return {str(key) for key in decoded_value if key is not None}
    return set()


def _combined_selection(value: Any) -> dict[str, Any] | None:
    selection = _combined_mapping(value)
    if selection is None:
        return None
    selection = dict(selection)
    for field in ("lower", "upper"):
        if field in selection:
            selection[field] = _combined_dynamic(selection[field])
    return selection


def _prepare_combined_freshness(row: dict[str, Any]) -> dict[str, Any]:
    """Prepare one packed evidence row for all canonical classifiers.

    This context is request/UDF-call local.  It deliberately has no shared or
    process-level lifetime.  The public helpers continue to normalize their
    ordinary Python inputs exactly as before.
    """

    attempt = None
    values = row.get("attempts")
    if isinstance(values, (list, tuple)):
        for value in reversed(values):
            if isinstance(value, dict):
                attempt = value
                break
    source = _combined_mapping((attempt or {}).get("source")) if attempt else None
    selection = _combined_selection(source.get("selection")) if source else None
    raw_request = _combined_mapping(row.get("read_request"))
    request = dict(raw_request) if raw_request is not None else None
    if request is not None:
        for key in ("watermark_start", "watermark_end"):
            if key in request:
                request[key] = _combined_dynamic(request[key])
        read_range = _combined_mapping(request.get("read_range"))
        if read_range is not None:
            read_range = dict(read_range)
            for key in ("start", "end"):
                if key in read_range:
                    read_range[key] = _combined_dynamic(read_range[key])
            request["read_range"] = read_range
    watermark = _combined_mapping((attempt or {}).get("watermark")) if attempt else None
    candidate = _combined_mapping(watermark.get("candidate")) if watermark else None
    # The packed JSON boundary already matches one native JSON-leaf adapter
    # decode. Keep that value for configuration/persistence; the movement
    # helper historically applies one additional dynamic decode.
    candidate_values = candidate.get("values") if candidate else None
    candidate_values_dynamic = _combined_dynamic(candidate_values)
    loaded_present = isinstance(watermark, dict) and "loaded_watermark" in watermark
    merge_base_present = (
        bool(row["merge_base_present"])
        if "merge_base_present" in row
        else isinstance(watermark, dict) and "merge_base" in watermark
    )
    saved_present = (
        bool(row["saved_watermark_present"])
        if "saved_watermark_present" in row
        else isinstance(watermark, dict) and "saved_watermark" in watermark
    )
    candidate_present = (
        bool(row["candidate_present"])
        if "candidate_present" in row
        else isinstance(watermark, dict) and "candidate" in watermark
    )
    loaded_raw = watermark.get("loaded_watermark") if loaded_present else None
    # Keep both representations: configuration receives the adapter value,
    # while movement/read-boundary logic receives its canonical dynamic value.
    loaded = _combined_dynamic(loaded_raw) if loaded_present else None
    merge_base = watermark.get("merge_base") if merge_base_present else None
    to_save = (
        watermark.get("watermark_to_save")
        if watermark and "watermark_to_save" in watermark
        else None
    )
    saved = watermark.get("saved_watermark") if saved_present else None
    raw_source_watermark_columns = row.get("source_watermark_columns")
    # Source columns are a native VARCHAR in the analytics schema. Its
    # configuration adapter decodes once; the movement key-set helper decodes
    # up to two nested JSON string layers.
    source_watermark_columns = _combined_dynamic(raw_source_watermark_columns)
    source_configure = _combined_decode_mapping(row.get("source_configure"))
    raw_watermark_start = raw_request.get("watermark_start") if raw_request else None
    raw_watermark_end = raw_request.get("watermark_end") if raw_request else None
    request_watermarks = {
        key
        for value in (raw_watermark_start, raw_watermark_end)
        for key, item in (_combined_mapping(value) or {}).items()
        if item is not None
    }
    selection_keys = {
        key
        for value in (
            selection.get("lower") if selection else None,
            selection.get("upper") if selection else None,
        )
        for key, item in (_combined_mapping(value) or {}).items()
        if item is not None
    }
    loaded_keys = {
        key for key, item in (_combined_mapping(loaded) or {}).items() if item is not None
    }
    return {
        "attempt": attempt,
        "source": source,
        "selection": selection,
        "raw_request": raw_request,
        "request": request,
        "resolved_range": _combined_mapping(raw_request.get("read_range")) if raw_request else None,
        "request_watermark_start": _combined_dynamic(raw_watermark_start),
        "watermark": watermark,
        "candidate": candidate,
        "candidate_values": candidate_values_dynamic,
        "candidate_values_adapter": candidate_values,
        "loaded": loaded,
        "loaded_adapter": loaded_raw,
        "merge_base": merge_base,
        "to_save": to_save,
        "saved": saved,
        "loaded_present": loaded_present,
        "merge_base_present": merge_base_present,
        "saved_present": saved_present,
        "candidate_present": candidate_present,
        "watermark_present": (
            bool(row["watermark_present"])
            if "watermark_present" in row
            else watermark is not None
        ),
        "source_watermark_columns": source_watermark_columns,
        "source_columns": _combined_key_set(raw_source_watermark_columns),
        "source_configure": source_configure,
        "selection_keys": selection_keys,
        "request_watermarks": request_watermarks,
        "loaded_keys": loaded_keys,
        "effective_lower": (
            _combined_dynamic(selection.get("lower")) if selection is not None else None
        ),
    }


def _api_watermark_binding_active(binding: Any, selection: dict[str, Any], key: str) -> bool:
    binding = _decode_mapping(binding)
    if binding is None:
        return False
    for field, binding_field in (("lower", "lower"), ("upper", "upper")):
        bound = _mapping(selection.get(field))
        rule = _mapping(binding.get(binding_field))
        if bound is not None and bound.get(key) is not None and rule is not None:
            if rule.get("name") and rule.get("location"):
                return True
    return False


def _watermark_movement_from_sql(persisted_state, evidence_json, configured, persistence_state):
    evidence = _decode_mapping(evidence_json)
    if evidence is None:
        return persisted_state
    row = dict(evidence)
    attempt = {
        "status": evidence.get("attempt_status"),
        "source": {
            "status": evidence.get("source_status"),
            "rows_read": evidence.get("source_rows_read"),
            "selection": evidence.get("selection"),
        },
        "watermark": evidence.get("watermark"),
    }
    if evidence.get("attempt_present"):
        row["attempts"] = [attempt]
    return watermark_movement_state(
        str(persisted_state or "unknown"), row,
        configured=bool(configured),
        persistence_state=str(persistence_state or "unknown"),
    )


def _read_boundary_adjustment_from_sql(evidence_json):
    evidence = _decode_mapping(evidence_json)
    if evidence is None:
        return _lossless_json_text({
            "adjustment_state": "unknown",
            "adjustment_basis": None,
            "adjustment_reason": "evidence_unavailable",
            "adjustment_input_json": None,
            "adjustment_effective_json": None,
        })
    attempt = {
        "status": evidence.get("attempt_status"),
        "source": {
            "status": evidence.get("source_status"),
            "rows_read": evidence.get("source_rows_read"),
            "selection": evidence.get("selection"),
        },
        "watermark": evidence.get("watermark"),
    }
    row = dict(evidence)
    if evidence.get("attempt_present"):
        row["attempts"] = [attempt]
    return _lossless_json_text(read_boundary_adjustment(row))


def _compare_persisted_json(loaded, saved, status, configured):
    # Keep the callable alive beyond an individual pooled reader cursor.
    def decode(text):
        return _decode_json_leaf(text) if text is not None else None

    return persisted_movement(
        decode(loaded),
        decode(saved),
        status,
        configured,
        baseline_present=True,
    )


def _compare_persisted_json_with_presence(
    baseline, saved, status, configured, baseline_present
):
    """Carry omitted-versus-explicit-null presence separately from JSON."""

    def decode(text):
        return _decode_json_leaf(text) if text is not None else None

    return persisted_movement(
        decode(baseline),
        decode(saved),
        status,
        configured,
        baseline_present=bool(baseline_present) if baseline_present is not None else False,
    )


def _compare_persisted_json_with_presence_kind(
    baseline, saved, status, configured, baseline_present, candidate_kind
):
    result = _compare_persisted_json_with_presence(
        baseline, saved, status, configured, baseline_present
    )
    if result in {"advanced", "changed"} and str(candidate_kind or "").lower() in {
        "opaque", "metadata"
    }:
        # Equality is already classified as unchanged by the adapter.  Opaque
        # and metadata candidates never supply a temporal ordering guarantee.
        return "changed"
    return result


def _compare_persisted_json_with_presence_kind_and_candidate(
    baseline, saved, status, configured, baseline_present, candidate_kind, candidate_values
):
    def decode(text):
        return _decode_json_leaf(text) if text is not None else None

    return persisted_movement(
        decode(baseline),
        decode(saved),
        status,
        configured,
        baseline_present=bool(baseline_present) if baseline_present is not None else False,
        candidate_kind=candidate_kind,
        candidate_values=decode(candidate_values),
    )


def _watermark_configuration_from_sql(
    request_configured, source_columns, watermark_present, load_status,
    save_status, candidate, loaded, to_save, saved, merge_base,
):
    def decode(text):
        return _decode_json_leaf(text) if text is not None else None

    return watermark_is_configured(
        request_configured=bool(request_configured),
        source_watermark_columns=decode(source_columns),
        watermark_present=bool(watermark_present),
        load_status=load_status,
        save_status=save_status,
        merge_base=decode(merge_base),
        candidate=decode(candidate),
        loaded=decode(loaded),
        to_save=decode(to_save),
        saved=decode(saved),
    )


def _request_has_watermark_configuration_from_sql(start, end, start_operator, end_operator):
    return _watermark_request_values_configured(start, end, start_operator, end_operator)


def _watermark_issue_from_sql(configured, movement, persistence, load_status, save_status):
    return watermark_issue_state(
        configured=bool(configured),
        movement_state=str(movement or "unknown"),
        persistence_state=str(persistence or "unknown"),
        load_status=load_status,
        save_status=save_status,
    )


def _persistence_state_from_sql(
    save_status, watermark_present, saved, candidate_present, candidate,
    attempt_present, root_status,
):
    def decode(text):
        return _decode_json_leaf(text) if text is not None else None

    watermark = None
    if watermark_present:
        watermark = {"save_status": save_status}
        saved_value = decode(saved)
        if saved_value is not None:
            watermark["saved_watermark"] = saved_value
    return _persistence_state(
        watermark=watermark,
        save_status=save_status,
        candidate_present=bool(candidate_present),
        candidate_values=decode(candidate),
        attempt={} if attempt_present else None,
        root_status=root_status,
    )


def _combined_freshness_classification(row: dict[str, Any]) -> dict[str, Any]:
    """Classify one packed registry row using the canonical Python helpers."""

    prepared = _prepare_combined_freshness(row)
    request_configured = _request_has_watermark_configuration(prepared["raw_request"])
    configured = watermark_is_configured(
        request_configured=request_configured,
        source_watermark_columns=prepared["source_watermark_columns"],
        watermark_present=prepared["watermark_present"],
        load_status=(prepared["watermark"] or {}).get("load_status"),
        save_status=(prepared["watermark"] or {}).get("save_status"),
        merge_base=prepared["merge_base"],
        candidate=prepared["candidate_values_adapter"],
        loaded=prepared["loaded_adapter"],
        to_save=prepared["to_save"],
        saved=prepared["saved"],
    )
    save_status = (prepared["watermark"] or {}).get("save_status")
    persistence_watermark = None
    if prepared["watermark_present"]:
        persistence_watermark = {"save_status": save_status}
        if prepared["saved"] is not None:
            persistence_watermark["saved_watermark"] = prepared["saved"]
    persistence_state = _persistence_state(
        watermark=persistence_watermark,
        save_status=save_status,
        candidate_present=prepared["candidate_present"],
        candidate_values=prepared["candidate_values_adapter"],
        attempt=prepared["attempt"],
        root_status=row.get("status"),
    )
    persisted_state = persisted_movement(
        prepared["merge_base"],
        prepared["saved"],
        "saved" if save_status in {"saved", "success", "succeeded"} else save_status,
        configured,
        baseline_present=prepared["merge_base_present"],
        candidate_kind=(prepared["candidate"] or {}).get("kind"),
        candidate_values=prepared["candidate_values_adapter"],
    )
    movement_state = watermark_movement_state(
        persisted_state,
        row,
        configured=configured,
        persistence_state=persistence_state,
        _prepared=prepared,
    )
    load_status = (prepared["watermark"] or {}).get("load_status")
    adjustment = read_boundary_adjustment(row, _prepared=prepared)
    return {
        "request_configured": request_configured,
        "watermark_enabled": configured,
        "persisted_movement_state": persisted_state,
        "persistence_state": persistence_state,
        "movement_state": movement_state,
        "watermark_issue_state": watermark_issue_state(
            configured=configured,
            movement_state=movement_state,
            persistence_state=persistence_state,
            load_status=load_status,
            save_status=save_status,
        ),
        "adjustment_state": adjustment.get("adjustment_state"),
        "adjustment_basis": adjustment.get("adjustment_basis"),
        "adjustment_reason": adjustment.get("adjustment_reason"),
        "adjustment_input_json": adjustment.get("adjustment_input_json"),
        "adjustment_effective_json": adjustment.get("adjustment_effective_json"),
    }


def _combined_freshness_classification_from_sql(evidence_json: Any) -> str | None:
    """Decode and classify one registry evidence payload once per row."""

    evidence = _decode_json_leaf(evidence_json)
    if not isinstance(evidence, dict):
        return None
    row = dict(evidence)
    attempt = {
        "status": evidence.get("attempt_status"),
        "source": {
            "status": evidence.get("source_status"),
            "rows_read": evidence.get("source_rows_read"),
            "selection": evidence.get("selection"),
        },
        "watermark": evidence.get("watermark"),
    }
    if evidence.get("attempt_present"):
        row["attempts"] = [attempt]
    return _lossless_json_text(_combined_freshness_classification(row))


def register_freshness_functions(conn) -> None:
    """Bounded scalar comparison preserves authored JSON types and decimal tokens."""
    # Connections share an instance catalog. Serialize initialization only;
    # never retain connections or hold this lock while executing report SQL.
    with _FRESHNESS_REGISTRATION_LOCK:
        if conn.execute(
            "SELECT 1 FROM duckdb_functions() "
            "WHERE function_name = 'studio_v6_freshness_classification' LIMIT 1"
        ).fetchone() is not None:
            return
        _register_freshness_functions(conn)


def _register_freshness_functions(conn) -> None:
    import duckdb

    for name, function, parameters, return_type in (
        (
            "studio_v6_persisted_movement",
            _compare_persisted_json,
            ["VARCHAR", "VARCHAR", "VARCHAR", "BOOLEAN"],
            "VARCHAR",
        ),
        (
            "studio_v6_persisted_movement_with_presence",
            _compare_persisted_json_with_presence,
            ["VARCHAR", "VARCHAR", "VARCHAR", "BOOLEAN", "BOOLEAN"],
            "VARCHAR",
        ),
        (
            "studio_v6_persisted_movement_with_presence_kind",
            _compare_persisted_json_with_presence_kind,
            ["VARCHAR", "VARCHAR", "VARCHAR", "BOOLEAN", "BOOLEAN", "VARCHAR"],
            "VARCHAR",
        ),
        (
            "studio_v6_persisted_movement_with_presence_kind_and_candidate",
            _compare_persisted_json_with_presence_kind_and_candidate,
            ["VARCHAR", "VARCHAR", "VARCHAR", "BOOLEAN", "BOOLEAN", "VARCHAR", "VARCHAR"],
            "VARCHAR",
        ),
        (
            "studio_v6_watermark_movement",
            _watermark_movement_from_sql,
            ["VARCHAR", "VARCHAR", "BOOLEAN", "VARCHAR"],
            "VARCHAR",
        ),
        (
            "studio_v6_read_boundary_adjustment",
            _read_boundary_adjustment_from_sql,
            ["VARCHAR"],
            "VARCHAR",
        ),
        (
            "studio_v6_watermark_is_configured",
            _watermark_configuration_from_sql,
            ["BOOLEAN", "VARCHAR", "BOOLEAN", "VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR"],
            "BOOLEAN",
        ),
        (
            "studio_v6_request_has_watermark_configuration",
            _request_has_watermark_configuration_from_sql,
            ["VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR"],
            "BOOLEAN",
        ),
        (
            "studio_v6_watermark_issue_state",
            _watermark_issue_from_sql,
            ["BOOLEAN", "VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR"],
            "VARCHAR",
        ),
        (
            "studio_v6_persistence_state",
            _persistence_state_from_sql,
            ["VARCHAR", "BOOLEAN", "VARCHAR", "BOOLEAN", "VARCHAR", "BOOLEAN", "VARCHAR"],
            "VARCHAR",
        ),
        (
            "studio_v6_freshness_classification",
            _combined_freshness_classification_from_sql,
            ["VARCHAR"],
            "VARCHAR",
        ),
    ):
        try:
            conn.create_function(name, function, parameters, return_type, null_handling="special")
        except (duckdb.NotImplementedException, duckdb.CatalogException) as exc:
            message = str(exc)
            # Pooled reader cursors share the database's scalar-function catalog.
            if name not in message or not any(
                marker in message for marker in ("already created", "already exists")
            ):
                raise


def freshness_evidence(row: dict[str, Any]) -> dict[str, Any]:
    attempt = final_attempt(row)
    request = _mapping(row.get("read_request"))
    source = _mapping((attempt or {}).get("source")) if attempt else None
    selection = _selection_with_decoded_bounds(source.get("selection")) if source else None
    source_status = source.get("status") if source else None
    watermark = _mapping((attempt or {}).get("watermark")) if attempt else None
    candidate = _mapping(watermark.get("candidate")) if watermark else None
    candidate_values = _dynamic(candidate.get("values")) if candidate else None
    loaded_present = isinstance(watermark, dict) and "loaded_watermark" in watermark
    merge_base_present = isinstance(watermark, dict) and "merge_base" in watermark
    saved_present = isinstance(watermark, dict) and "saved_watermark" in watermark
    candidate_present = isinstance(watermark, dict) and "candidate" in watermark
    load_status = watermark.get("load_status") if watermark else None
    loaded = _dynamic(watermark.get("loaded_watermark")) if loaded_present else None
    merge_base = _dynamic(watermark.get("merge_base")) if merge_base_present else None
    to_save = _dynamic(watermark.get("watermark_to_save")) if watermark and "watermark_to_save" in watermark else None
    saved = _dynamic(watermark.get("saved_watermark")) if saved_present else None
    save_status = watermark.get("save_status") if watermark else None
    if request is not None:
        for key in ("watermark_start", "watermark_end"):
            if key in request:
                request[key] = _dynamic(request[key])
        read_range = _mapping(request.get("read_range"))
        if read_range is not None:
            for key in ("start", "end"):
                if key in read_range:
                    read_range[key] = _dynamic(read_range[key])
            request["read_range"] = read_range
    request_configured = _request_has_watermark_configuration(request)
    configured = watermark_is_configured(
        request_configured=request_configured,
        source_watermark_columns=row.get("source_watermark_columns"),
        watermark_present=watermark is not None,
        load_status=load_status,
        save_status=save_status,
        merge_base=merge_base,
        candidate=candidate_values,
        loaded=loaded,
        to_save=to_save,
        saved=saved,
    )
    configuration_state = "configured" if configured else "not_configured"
    selection_state = (
        "selected" if selection is not None else
        "not_selected" if attempt is not None else "unknown"
    )
    comparison_basis = (
        "merge_base" if merge_base_present and merge_base is not None
        else "merge_base_null" if merge_base_present
        else "missing_merge_base"
    )
    persistence_state = _persistence_state(
        watermark=watermark,
        save_status=save_status,
        candidate_present=candidate_present,
        candidate_values=candidate_values,
        attempt=attempt,
        root_status=row.get("status"),
    )
    result: dict[str, Any] = {
        "read_request": request,
        "selection": selection,
        "source_status": source_status,
        "selection_upper_origin": selection.get("upper_origin") if isinstance(selection, dict) else None,
        "loaded_watermark": loaded,
        "loaded_watermark_present": loaded_present,
        "merge_base": merge_base,
        "merge_base_present": merge_base_present,
        "candidate": candidate_values,
        "candidate_kind": candidate.get("kind") if isinstance(candidate, dict) else None,
        "candidate_present": candidate_present,
        "watermark_to_save": to_save,
        "save_status": save_status,
        "load_status": load_status,
        "saved_watermark": saved,
        "saved_watermark_present": saved_present,
        "configuration_state": configuration_state,
        "selection_state": selection_state,
        "configuration_selection_state": f"{configuration_state}_{selection_state}",
        "coverage_state": configuration_state,
        "comparison_basis": comparison_basis,
        "persistence_state": persistence_state,
        "movement_state": "not_configured" if not configured else "unknown",
        "watermark_issue_state": watermark_issue_state(
            configured=configured,
            movement_state="not_configured" if not configured else "unknown",
            persistence_state=persistence_state,
            load_status=load_status,
            save_status=save_status,
        ),
    }
    persisted_state = persisted_movement(
        merge_base,
        saved,
        "saved" if save_status in {"saved", "success", "succeeded"} else save_status,
        configured,
        baseline_present=merge_base_present,
        candidate_kind=candidate.get("kind") if isinstance(candidate, dict) else None,
        candidate_values=candidate_values,
    )
    result["movement_state"] = watermark_movement_state(
        persisted_state,
        row,
        configured=configured,
        persistence_state=persistence_state,
    )
    result["watermark_issue_state"] = watermark_issue_state(
        configured=configured,
        movement_state=result["movement_state"],
        persistence_state=persistence_state,
        load_status=load_status,
        save_status=save_status,
    )
    return result


def _request_has_watermark_configuration(request: Any) -> bool:
    if not isinstance(request, dict):
        return False
    return _watermark_request_values_configured(
        request.get("watermark_start"),
        request.get("watermark_end"),
        request.get("watermark_start_operator"),
        request.get("watermark_end_operator"),
    )


def _watermark_request_values_configured(*values: Any) -> bool:
    return any(value is not None for value in values)


def _source_watermark_configured(value: Any) -> bool:
    """Treat empty serialized column lists as absent configuration."""

    if value in (None, "", [], {}):
        return False
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"", "null", "[]", "{}"}:
            return False
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return True
        return parsed not in (None, [], {})
    return True


def _persistence_state(
    *,
    watermark: dict[str, Any] | None,
    save_status: Any,
    candidate_present: bool,
    candidate_values: Any,
    attempt: dict[str, Any] | None,
    root_status: Any,
) -> str:
    if watermark is None:
        if attempt is None or str(root_status or "").lower() in _FAILURE_STATUSES:
            return "not_attempted"
        return "not_requested"
    normalized = str(save_status or "").strip().lower()
    if normalized in {"saved", "success", "succeeded"}:
        return "saved" if watermark.get("saved_watermark") is not None else "unknown"
    if normalized in {"save_error", "error", "failed", "failure", "cancelled", "canceled"}:
        return "save_error"
    if normalized == "not_attempted":
        return "not_attempted"
    if normalized == "not_requested":
        return "not_requested"
    if not candidate_present or not _has_watermark_value(candidate_values):
        return "no_candidate"
    if save_status is None:
        return "not_attempted"
    return "unknown"


__all__ = [
    "attempt_duration",
    "attempts",
    "exact_equal",
    "failed_attempts",
    "final_attempt",
    "final_phase_duration",
    "freshness_evidence",
    "is_v6_row",
    "is_supported_log_row",
    "phase_timing",
    "persisted_movement",
    "read_boundary_adjustment",
    "register_freshness_functions",
    "recovered_attempt_failures",
    "root_duration",
]

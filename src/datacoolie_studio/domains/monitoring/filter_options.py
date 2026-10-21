"""Time-scoped filter counts, cached under a consistent analytics snapshot."""
from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.monitoring.context import reader as analytics_reader
from datacoolie_studio.domains.monitoring.metrics.filtering import normalize_monitoring_filters_for_timezone
from datacoolie_studio.domains.monitoring.repository import monitoring_filter_options_read_model
from datacoolie_studio.domains.read_models.cache import fingerprint
from datacoolie_studio.domains.read_models.contracts import ResultCacheKey, get_or_compute
from datacoolie_studio.domains.read_models.provider import result_cache_provider


FILTER_OPTIONS_PRODUCER = "monitoring-filter-counts-v2"
FILTER_OPTIONS_NAMESPACE = "monitoring.page.filter-options"


def filter_time_scope(
    filters: dict[str, str], timezone_info: tzinfo | None, *, now: datetime | None = None,
) -> dict[str, str]:
    """Canonical effective bounds share the report normalizer's actual tzinfo."""
    range_value = (filters.get("range") or "all").strip().lower()
    supplied = {"range": range_value}
    if range_value == "custom":
        supplied.update(startTime=filters.get("startTime", ""), endTime=filters.get("endTime", ""))
    normalized = normalize_monitoring_filters_for_timezone(supplied, timezone_info, now)
    start = parse_utc_datetime(normalized.get("_relativeStartTime") or normalized.get("startTime"))
    end = parse_utc_datetime(normalized.get("endTime"))
    if start is None and end is None:
        return {"range": "all"}
    return {
        "range": "custom",
        "startTime": start.astimezone(timezone.utc).isoformat() if start is not None else "",
        "endTime": end.astimezone(timezone.utc).isoformat() if end is not None else "",
    }


def monitoring_filter_options(
    paths: list[EnvironmentSource], session: Session, *,
    filters: dict[str, str] | None = None,
    timezone_info: tzinfo | None = None,
    timezone_label: str = "UTC",
    environment_id: int | None = None,
) -> dict[str, Any]:
    del session
    scope = filter_time_scope(filters or {}, timezone_info)
    with analytics_reader(paths, snapshot=True) as (conn, source_ids, generation):
        if conn is None or not source_ids:
            return _payload({})
        context = (conn, source_ids, generation)

        def produce() -> dict[str, Any]:
            result = monitoring_filter_options_read_model(paths, context, filters=scope)
            return _payload(result["options"])

        resolved_id = environment_id or (getattr(paths[0], "environment_id", None) if paths else None)
        if resolved_id is None:
            return produce()
        key = ResultCacheKey(
            environment_id=resolved_id,
            namespace=FILTER_OPTIONS_NAMESPACE,
            parameters_fingerprint=fingerprint({"scope": scope, "timezone": timezone_label}),
            input_fingerprint=generation,
            producer_version=FILTER_OPTIONS_PRODUCER,
        )
        provider = result_cache_provider()
        result, _ = get_or_compute(provider.store, provider.coordinator, key, produce)
        return result.payload


def _payload(options: dict[str, Any]) -> dict[str, Any]:
    return {
        "options": options,
        "summary": {"source": "duckdb_filter_values", "fields": len(options)},
        "errors": [],
    }

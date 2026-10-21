from __future__ import annotations

import json
from datetime import datetime, timezone

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.sources.registration import configured_location_dict
from datacoolie_studio.domains.sources.storage_binding import binding_to_dict


def source_to_dict(source: EnvironmentSource) -> dict:
    """Serialize a source read model without depending on workspace orchestration."""

    return {
        "id": source.id,
        "environment_id": source.environment_id,
        "uri": source.uri,
        "label": source.label,
        "enabled": source.enabled,
        "sync_schedule_enabled": source.sync_schedule_enabled,
        "sync_interval_minutes": source.sync_interval_minutes,
        "last_scheduled_sync_at": (
            as_utc(source.last_scheduled_sync_at)
            if source.last_scheduled_sync_at
            else None
        ),
        "created_at": source.created_at,
        "source_config": json_object(source.source_config_json),
        "storage": binding_to_dict(source),
        "configured_location": configured_location_dict(source),
        "latest_validation": validation_from_source(source),
    }


def validation_from_source(source: EnvironmentSource) -> dict | None:
    if not source.read_check_status and not source.read_check_result_json:
        return None
    result = json_object(source.read_check_result_json)
    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "status": source.read_check_status or result.get("status") or "unknown",
        "message": result.get("message") or "",
        "detected_provider": result.get("detected_provider"),
        "detected_format": result.get("detected_format"),
        "metadata_write_back_supported": result.get(
            "metadata_write_back_supported"
        ),
        "record_counts": result.get("record_counts") or {},
        "records_scanned": result.get("records_scanned") or 0,
        "validated_at": (
            as_utc(source.read_checked_at) if source.read_checked_at else None
        ),
        "errors": result.get("errors") or [],
    }


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}

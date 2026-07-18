from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.core.config import analytics_database_path, database_path
from datacoolie_studio.db.models import StudioSetting
from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.logs import cache as logs_cache

STUDIO_TIMEZONE_KEY = "studio.timezone"
SOURCE_CHECK_INTERVAL_KEY = "studio.source_check_interval_seconds"
DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS = 30
MIN_SOURCE_CHECK_INTERVAL_SECONDS = 5
MAX_SOURCE_CHECK_INTERVAL_SECONDS = 3600


def studio_timezone_context(session: Session) -> dict[str, Any]:
    server_tz, server_timezone_label = _server_timezone_info()
    setting = session.get(StudioSetting, STUDIO_TIMEZONE_KEY)
    if setting and setting.value:
        try:
            configured_tz = ZoneInfo(setting.value)
        except ZoneInfoNotFoundError:
            return {
                "timezone": server_timezone_label,
                "timezone_source": "server_default",
                "timezone_info": server_tz,
                "updated_at": setting.updated_at,
            }
        return {
            "timezone": setting.value,
            "timezone_source": "configured",
            "timezone_info": configured_tz,
            "updated_at": setting.updated_at,
        }
    return {
        "timezone": server_timezone_label,
        "timezone_source": "server_default",
        "timezone_info": server_tz,
        "updated_at": None,
    }


def get_studio_settings(session: Session) -> dict[str, Any]:
    context = studio_timezone_context(session)
    source_check_setting = session.get(StudioSetting, SOURCE_CHECK_INTERVAL_KEY)
    return {
        "timezone": context["timezone"],
        "timezone_source": context["timezone_source"],
        "source_check_interval_seconds": source_check_interval_seconds(session, source_check_setting),
        "updated_at": _latest_updated_at(context["updated_at"], source_check_setting.updated_at if source_check_setting else None),
        "storage": _studio_storage_context(session),
    }


def set_studio_timezone(session: Session, timezone_name: str | None) -> dict[str, Any]:
    normalized = (timezone_name or "").strip()
    setting = session.get(StudioSetting, STUDIO_TIMEZONE_KEY)
    if not normalized:
        if setting is not None:
            session.delete(setting)
            session.commit()
        return get_studio_settings(session)
    normalized = _validate_timezone_name(normalized)
    if setting is None:
        setting = StudioSetting(key=STUDIO_TIMEZONE_KEY, value=normalized)
        session.add(setting)
    else:
        setting.value = normalized
    session.commit()
    return get_studio_settings(session)


def set_source_check_interval(session: Session, interval_seconds: int) -> dict[str, Any]:
    value = int(interval_seconds)
    if value < MIN_SOURCE_CHECK_INTERVAL_SECONDS or value > MAX_SOURCE_CHECK_INTERVAL_SECONDS:
        raise ValueError(
            f"Source check interval must be between {MIN_SOURCE_CHECK_INTERVAL_SECONDS} "
            f"and {MAX_SOURCE_CHECK_INTERVAL_SECONDS} seconds"
        )
    setting = session.get(StudioSetting, SOURCE_CHECK_INTERVAL_KEY)
    if setting is None:
        session.add(StudioSetting(key=SOURCE_CHECK_INTERVAL_KEY, value=str(value)))
    else:
        setting.value = str(value)
    session.commit()
    return get_studio_settings(session)


def source_check_interval_seconds(session: Session, setting: StudioSetting | None = None) -> int:
    setting = setting if setting is not None else session.get(StudioSetting, SOURCE_CHECK_INTERVAL_KEY)
    if setting is None:
        return DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS
    try:
        value = int(setting.value)
    except (TypeError, ValueError):
        return DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS
    if value < MIN_SOURCE_CHECK_INTERVAL_SECONDS or value > MAX_SOURCE_CHECK_INTERVAL_SECONDS:
        return DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS
    return value


def _latest_updated_at(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _validate_timezone_name(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {value}") from exc
    return value


def _server_timezone_info() -> tuple[tzinfo, str]:
    server_now = datetime.now().astimezone()
    tz_value = server_now.tzinfo or timezone.utc
    zone_key = getattr(tz_value, "key", None)
    if isinstance(zone_key, str) and zone_key.strip():
        return tz_value, zone_key
    tz_name = server_now.tzname() or "UTC"
    return tz_value, tz_name


def _studio_storage_context(session: Session) -> dict[str, Any]:
    workspace_db_path = database_path()
    analytics_path = analytics_database_path()
    analytics_stats = logs_cache.analytics_cache_stats()
    cached_source_ids = {int(source_id) for source_id in analytics_stats.get("cached_source_ids", [])}
    active_etl_source_ids = {
        int(source_id)
        for source_id in session.scalars(
            select(EnvironmentSource.id).where(EnvironmentSource.source_kind == "logs")
        ).all()
    }
    orphan_source_ids = sorted(cached_source_ids - active_etl_source_ids)
    return {
        "workspace_database": _path_descriptor(workspace_db_path),
        "analytics_cache": {
            "scope": "studio",
            "path": str(analytics_path),
            "exists": bool(analytics_stats.get("exists")),
            "size_bytes": analytics_stats.get("size_bytes"),
            "dataflow_row_count": int(analytics_stats.get("dataflow_row_count", 0)),
            "job_row_count": int(analytics_stats.get("job_row_count", 0)),
            "filter_value_count": int(analytics_stats.get("filter_value_count", 0)),
            "cached_source_count": len(cached_source_ids),
            "active_source_count": len(cached_source_ids & active_etl_source_ids),
            "orphan_source_count": len(orphan_source_ids),
            "orphan_source_ids": orphan_source_ids,
        },
    }


def _path_descriptor(path: Path) -> dict[str, Any]:
    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists else None,
    }

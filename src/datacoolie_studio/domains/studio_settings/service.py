from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from datacoolie_studio.core.config import analytics_database_path, database_url
from datacoolie_studio.db.session import get_engine
from datacoolie_studio.db.models import (
    EnvironmentSource,
    SourceObservation,
    StudioSetting,
    utc_now,
)
from datacoolie_studio.domains.analytics import maintenance as analytics_maintenance

STUDIO_TIMEZONE_KEY = "studio.timezone"
SOURCE_CHECK_INTERVAL_KEY = "studio.source_check_interval_seconds"
SOURCE_CHECK_MODE_KEY = "studio.source_check_mode"
SOURCE_CHECK_MAX_INTERVAL_KEY = "studio.source_check_max_interval_seconds"
DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS = 30
DEFAULT_SOURCE_CHECK_MODE = "adaptive"
DEFAULT_SOURCE_CHECK_MAX_INTERVAL_SECONDS = 300
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
    source_check_mode_setting = session.get(StudioSetting, SOURCE_CHECK_MODE_KEY)
    source_check_max_setting = session.get(StudioSetting, SOURCE_CHECK_MAX_INTERVAL_KEY)
    policy = source_check_policy(
        session,
        interval_setting=source_check_setting,
        mode_setting=source_check_mode_setting,
        max_setting=source_check_max_setting,
    )
    return {
        "timezone": context["timezone"],
        "timezone_source": context["timezone_source"],
        "timezone_offset_minutes": _timezone_offset_minutes(context["timezone_info"]),
        **policy,
        "updated_at": _latest_updated_at(
            context["updated_at"],
            source_check_setting.updated_at if source_check_setting else None,
            source_check_mode_setting.updated_at if source_check_mode_setting else None,
            source_check_max_setting.updated_at if source_check_max_setting else None,
        ),
    }


def get_studio_diagnostics(session: Session) -> dict[str, Any]:
    return _studio_storage_context(session)


def compact_workspace_database() -> dict[str, Any]:
    engine = get_engine()
    if engine.dialect.name != "sqlite":
        raise ValueError("Workspace database compaction is available only for SQLite")
    configured = make_url(str(engine.url))
    if configured.database in {None, "", ":memory:"}:
        raise ValueError("Workspace database compaction requires a file-backed SQLite database")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("VACUUM")
    return _workspace_database_descriptor(str(engine.url))


def update_studio_settings(session: Session, changes: Mapping[str, Any]) -> dict[str, Any]:
    unknown_keys = set(changes) - {
        "timezone",
        "source_check_interval_seconds",
        "source_check_mode",
        "source_check_max_interval_seconds",
    }
    if unknown_keys:
        raise ValueError(f"Unsupported Studio settings: {', '.join(sorted(unknown_keys))}")

    normalized_timezone: str | None = None
    if "timezone" in changes:
        raw_timezone = changes["timezone"]
        normalized_timezone = (raw_timezone or "").strip()
        if normalized_timezone:
            normalized_timezone = _validate_timezone_name(normalized_timezone)

    normalized_interval: int | None = None
    if "source_check_interval_seconds" in changes:
        raw_interval = changes["source_check_interval_seconds"]
        if raw_interval is None:
            raise ValueError("Source check interval cannot be null")
        normalized_interval = _validate_source_check_interval(raw_interval)

    normalized_mode: str | None = None
    if "source_check_mode" in changes:
        normalized_mode = str(changes["source_check_mode"] or "").strip().lower()
        if normalized_mode not in {"fixed", "adaptive"}:
            raise ValueError("Source check mode must be fixed or adaptive")

    normalized_max_interval: int | None = None
    if "source_check_max_interval_seconds" in changes:
        raw_max_interval = changes["source_check_max_interval_seconds"]
        if raw_max_interval is None:
            raise ValueError("Source check max interval cannot be null")
        normalized_max_interval = _validate_source_check_interval(raw_max_interval)

    current_policy = source_check_policy(session)
    effective_interval = (
        normalized_interval
        if normalized_interval is not None
        else int(current_policy["source_check_interval_seconds"])
    )
    effective_max = (
        normalized_max_interval
        if normalized_max_interval is not None
        else int(current_policy["source_check_max_interval_seconds"])
    )
    if effective_max < effective_interval:
        raise ValueError("Source check max interval must be greater than or equal to the base interval")

    changed = False
    if "timezone" in changes:
        timezone_setting = session.get(StudioSetting, STUDIO_TIMEZONE_KEY)
        if normalized_timezone:
            if timezone_setting is None:
                session.add(StudioSetting(key=STUDIO_TIMEZONE_KEY, value=normalized_timezone))
                changed = True
            elif timezone_setting.value != normalized_timezone:
                timezone_setting.value = normalized_timezone
                changed = True
        elif timezone_setting is not None:
            session.delete(timezone_setting)
            changed = True

    if normalized_interval is not None:
        interval_setting = session.get(StudioSetting, SOURCE_CHECK_INTERVAL_KEY)
        interval_value = str(normalized_interval)
        if interval_setting is None:
            session.add(StudioSetting(key=SOURCE_CHECK_INTERVAL_KEY, value=interval_value))
            changed = True
        elif interval_setting.value != interval_value:
            interval_setting.value = interval_value
            changed = True

    if normalized_mode is not None:
        mode_setting = session.get(StudioSetting, SOURCE_CHECK_MODE_KEY)
        if mode_setting is None:
            session.add(StudioSetting(key=SOURCE_CHECK_MODE_KEY, value=normalized_mode))
            changed = True
        elif mode_setting.value != normalized_mode:
            mode_setting.value = normalized_mode
            changed = True

    if normalized_max_interval is not None:
        max_setting = session.get(StudioSetting, SOURCE_CHECK_MAX_INTERVAL_KEY)
        max_value = str(normalized_max_interval)
        if max_setting is None:
            session.add(StudioSetting(key=SOURCE_CHECK_MAX_INTERVAL_KEY, value=max_value))
            changed = True
        elif max_setting.value != max_value:
            max_setting.value = max_value
            changed = True

    policy_changed = bool(
        {"source_check_mode", "source_check_interval_seconds", "source_check_max_interval_seconds"}
        .intersection(changes)
    ) and changed
    if policy_changed:
        cloud_source_ids = select(EnvironmentSource.id).where(
            EnvironmentSource.storage_provider != "local"
        )
        session.query(SourceObservation).filter(
            SourceObservation.source_id.in_(cloud_source_ids)
        ).update(
            {
                SourceObservation.next_observation_at: utc_now(),
                SourceObservation.unchanged_streak: 0,
                SourceObservation.failure_streak: 0,
            },
            synchronize_session=False,
        )
    if changed:
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
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


def source_check_policy(
    session: Session,
    *,
    interval_setting: StudioSetting | None = None,
    mode_setting: StudioSetting | None = None,
    max_setting: StudioSetting | None = None,
) -> dict[str, int | str]:
    interval = source_check_interval_seconds(session, interval_setting)
    mode_setting = (
        mode_setting
        if mode_setting is not None
        else session.get(StudioSetting, SOURCE_CHECK_MODE_KEY)
    )
    mode = str(mode_setting.value).lower() if mode_setting else DEFAULT_SOURCE_CHECK_MODE
    if mode not in {"fixed", "adaptive"}:
        mode = DEFAULT_SOURCE_CHECK_MODE
    max_setting = (
        max_setting
        if max_setting is not None
        else session.get(StudioSetting, SOURCE_CHECK_MAX_INTERVAL_KEY)
    )
    try:
        max_interval = int(max_setting.value) if max_setting else DEFAULT_SOURCE_CHECK_MAX_INTERVAL_SECONDS
    except (TypeError, ValueError):
        max_interval = DEFAULT_SOURCE_CHECK_MAX_INTERVAL_SECONDS
    if (
        max_interval < interval
        or max_interval < MIN_SOURCE_CHECK_INTERVAL_SECONDS
        or max_interval > MAX_SOURCE_CHECK_INTERVAL_SECONDS
    ):
        max_interval = max(interval, DEFAULT_SOURCE_CHECK_MAX_INTERVAL_SECONDS)
    return {
        "source_check_mode": mode,
        "source_check_interval_seconds": interval,
        "source_check_max_interval_seconds": max_interval,
    }


def _validate_source_check_interval(value: Any) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Source check interval must be an integer") from exc
    if normalized < MIN_SOURCE_CHECK_INTERVAL_SECONDS or normalized > MAX_SOURCE_CHECK_INTERVAL_SECONDS:
        raise ValueError(
            f"Source check interval must be between {MIN_SOURCE_CHECK_INTERVAL_SECONDS} "
            f"and {MAX_SOURCE_CHECK_INTERVAL_SECONDS} seconds"
        )
    return normalized


def _latest_updated_at(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _timezone_offset_minutes(timezone_info: tzinfo) -> int:
    offset = datetime.now(timezone_info).utcoffset()
    return round(offset.total_seconds() / 60) if offset is not None else 0


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
    analytics_path = analytics_database_path()
    analytics_stats = analytics_maintenance.cache_stats()
    cached_source_ids = {int(source_id) for source_id in analytics_stats.get("cached_source_ids", [])}
    active_etl_source_ids = {
        int(source_id)
        for source_id in session.scalars(
            select(EnvironmentSource.id).where(EnvironmentSource.source_kind == "logs")
        ).all()
    }
    orphan_source_ids = sorted(cached_source_ids - active_etl_source_ids)
    return {
        "workspace_database": _workspace_database_descriptor(database_url()),
        "analytics_cache": {
            "scope": "studio",
            "path": str(analytics_path),
            "exists": bool(analytics_stats.get("exists")),
            "size_bytes": analytics_stats.get("size_bytes"),
            "schema_version": analytics_stats.get("schema_version"),
            "generation": analytics_stats.get("generation"),
            "build_state": analytics_stats.get("build_state", "rebuild_required"),
            "published_at": analytics_stats.get("published_at"),
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


def _workspace_database_descriptor(url_value: str) -> dict[str, Any]:
    configured = make_url(url_value)
    backend = configured.get_backend_name()
    if backend != "sqlite":
        return {
            "backend": backend,
            "path": configured.database or backend,
            "exists": True,
            "size_bytes": None,
            "maintenance_supported": False,
        }
    database = configured.database
    if database in {None, "", ":memory:"}:
        return {
            "backend": "sqlite",
            "path": ":memory:",
            "exists": True,
            "size_bytes": None,
            "maintenance_supported": False,
        }
    descriptor = _path_descriptor(Path(database).expanduser())
    return {"backend": "sqlite", **descriptor, "maintenance_supported": True}

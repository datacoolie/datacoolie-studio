from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, utc_now
from datacoolie_studio.domains.analytics import access as analytics_access
from datacoolie_studio.domains.analytics import ownership as analytics_ownership
from datacoolie_studio.domains.code_artifacts.service import (
    refresh_code_artifact,
    validate_code_artifact,
)
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.logs.discovery import LogSyncMode, LogSyncSpec
from datacoolie_studio.domains.logs.ingestion import refresh_log_source_cache
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.service import ensure_metadata_materialization
from datacoolie_studio.domains.sources import service as source_service
from datacoolie_studio.domains.source_observation.repository import (
    reset_observation,
    resume_observation,
)
from datacoolie_studio.domains.studio_settings.service import source_check_interval_seconds
from datacoolie_studio.domains.sync import service as sync_service
from datacoolie_studio.domains.sync.scheduler import retry_source_observation as retry_observation


class SourceRefreshBusyError(RuntimeError):
    """Raised when another refresh already owns the source lock."""

    def __init__(self, source_kind: str):
        self.source_kind = source_kind
        super().__init__(source_kind)


class AnalyticsUpgradeBusyError(RuntimeError):
    """Raised when log refresh is blocked by an analytics cache upgrade."""


class SourceObservationDisabledError(RuntimeError):
    """Raised when an observation retry is requested for a disabled source."""


def validate_source(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> dict:
    """Validate one source and resume observation after a successful check."""
    if source.source_kind == "metadata":
        result = source_service.validate_metadata_source(
            session,
            source,
            secret_store=secret_store,
        )
    elif source.source_kind == "sql":
        result = source_service.validate_sql_source(
            session,
            source,
            secret_store=secret_store,
        )
    elif source.source_kind == "logs":
        result = source_service.validate_log_source(
            session,
            source,
            secret_store=secret_store,
        )
    elif source.source_kind == "code":
        result = validate_code_artifact(
            session,
            source,
            secret_store=secret_store,
        )
    else:
        raise ValueError(f"Unsupported source kind: {source.source_kind}")

    if result.get("status") in {"ok", "warning"}:
        resume_observation(
            session,
            source.id,
            due_at=utc_now()
            + timedelta(seconds=source_check_interval_seconds(session)),
            preserve_outcome=True,
        )
        session.commit()
    return result


def refresh_source(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None = None,
    log_sync_spec: LogSyncSpec | None = None,
) -> dict:
    """Run the source-kind refresh workflow and return its sync status."""
    if source.source_kind == "logs":
        return _refresh_log_source(
            session,
            source,
            secret_store=secret_store,
            sync_spec=log_sync_spec or LogSyncSpec(mode=LogSyncMode.INCREMENTAL),
        )
    with sync_service.source_refresh_guard(source.id) as acquired:
        if not acquired:
            raise SourceRefreshBusyError(source.source_kind)
        if source.source_kind == "metadata":
            try:
                ensure_metadata_materialization(
                    session,
                    source,
                    force=True,
                    secret_store=secret_store,
                )
            except MetadataReadError:
                pass
        elif source.source_kind == "sql":
            result = source_service.validate_sql_source(
                session,
                source,
                secret_store=secret_store,
            )
            if result.get("status") in {"ok", "warning"}:
                source.last_successful_sync_at = utc_now()
                session.commit()
        elif source.source_kind == "code":
            refresh_code_artifact(
                session,
                source,
                secret_store=secret_store,
            )
        else:
            raise ValueError(f"Unsupported source kind: {source.source_kind}")

    result = sync_service.source_sync_status(session, source)
    if (result.get("sync_execution") or {}).get("state") == "succeeded":
        reset_observation(
            session,
            source.id,
            due_at=utc_now()
            + timedelta(seconds=source_check_interval_seconds(session)),
            pending_changes=False,
        )
        session.commit()
        result = sync_service.source_sync_status(session, source)
    return result


def retry_source(
    session: Session,
    source: EnvironmentSource,
) -> dict:
    """Retry automatic observation for an enabled source."""
    if not source.enabled:
        raise SourceObservationDisabledError
    retry_observation(session, source)
    return sync_service.source_sync_status(session, source)


def _refresh_log_source(
    session: Session,
    source: EnvironmentSource,
    *,
    secret_store: CredentialSecretStore | None,
    sync_spec: LogSyncSpec,
) -> dict:
    if analytics_ownership.cache_upgrade_is_building(session):
        raise AnalyticsUpgradeBusyError
    with analytics_access.analytics_global_rebuild_lock:
        if analytics_ownership.cache_upgrade_is_building(session):
            raise AnalyticsUpgradeBusyError
        with sync_service.source_refresh_guard(source.id) as acquired:
            if not acquired:
                raise SourceRefreshBusyError(source.source_kind)
            return refresh_log_source_cache(
                session,
                source,
                sync_spec=sync_spec,
                secret_store=secret_store,
            )

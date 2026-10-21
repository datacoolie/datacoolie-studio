from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, SourceRegistration
from datacoolie_studio.domains.environment_caches import (
    invalidate_environment_derived_caches,
)
from datacoolie_studio.domains.source_observation.repository import reset_observation
from datacoolie_studio.domains.sources.registration import (
    canonicalize_location_config,
    get_or_create_registration,
)
from datacoolie_studio.domains.sources.storage_binding import (
    apply_binding,
    validate_and_normalize_binding,
)
from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.storage.identity import storage_identity_scope
from datacoolie_studio.domains.storage.uri import normalized_source_uri, parse_storage_uri
from datacoolie_studio.domains.studio_settings.service import source_check_interval_seconds

SourceKind = Literal["metadata", "sql", "logs", "code"]


def create_source(
    session: Session,
    environment_id: int,
    *,
    source_kind: SourceKind,
    uri: str,
    label: str | None,
    enabled: bool,
    source_config: dict | None = None,
    storage: dict | None = None,
    registration: SourceRegistration | None = None,
    input_uri: str | None = None,
) -> EnvironmentSource:
    """Persist one source without owning the surrounding application workflow."""
    raw_uri = uri if source_kind == "logs" else input_uri or uri
    requested_config = dict(source_config or {})
    if source_kind == "sql":
        requested_config.setdefault("discovery_mode", "manual_sql_path")
        requested_config.setdefault("sql_root_uri", sql_root_for_uri(uri))

    normalized_uri, binding = validate_and_normalize_binding(
        session,
        uri=uri,
        storage=storage,
        source_config=requested_config,
    )
    if source_kind == "sql" and not str(
        requested_config.get("sql_root_uri") or ""
    ).strip():
        requested_config["sql_root_uri"] = sql_root_for_uri(normalized_uri)
    canonical_config, input_locations, canonical_locations = canonicalize_location_config(
        requested_config,
        binding,
    )

    if registration is None:
        registration, _ = get_or_create_registration(
            session,
            environment_id=environment_id,
            purpose=source_kind,
            input_uri=raw_uri,
            canonical_uri=normalized_uri,
            binding=binding,
            input_locations=input_locations,
            canonical_locations=canonical_locations,
        )
        existing = source_for_registration(
            session,
            registration.id,
            source_kind,
            normalized_uri,
        )
        if existing is not None:
            return existing

    source = EnvironmentSource(
        environment_id=environment_id,
        registration_id=registration.id,
        source_kind=source_kind,
        uri=normalized_uri,
        label=label,
        enabled=enabled,
        sync_schedule_enabled=False,
        sync_interval_minutes=1 if source_kind == "logs" else None,
        source_config_json=json.dumps(canonical_config, sort_keys=True),
    )
    apply_binding(source, binding)
    session.add(source)
    session.flush()
    reset_observation(
        session,
        source.id,
        due_at=datetime.now(timezone.utc)
        + timedelta(seconds=source_check_interval_seconds(session)),
    )
    invalidate_environment_derived_caches(
        environment_id,
        structural=source_kind != "logs",
        identity=source_kind in {"metadata", "logs"},
    )
    return source


def source_for_registration(
    session: Session,
    registration_id: int,
    source_kind: SourceKind | str,
    uri: str,
) -> EnvironmentSource | None:
    target = normalized_source_uri(uri)
    candidates = session.scalars(
        select(EnvironmentSource).where(
            EnvironmentSource.registration_id == registration_id,
            EnvironmentSource.source_kind == source_kind,
        )
    )
    return next(
        (
            source
            for source in candidates
            if normalized_source_uri(source.uri) == target
        ),
        None,
    )


def storage_identity_from_binding(
    uri: str,
    binding: object | None,
) -> tuple[str, dict[str, object]]:
    if isinstance(binding, StorageBinding):
        provider = binding.provider
        options = binding.options
    elif isinstance(binding, dict):
        provider = str(binding.get("provider") or parse_storage_uri(uri).provider)
        raw_options = binding.get("options")
        options = raw_options if isinstance(raw_options, dict) else {}
    else:
        provider = parse_storage_uri(uri).provider
        options = {}
    return provider, storage_identity_scope(provider, options)


def sql_root_for_uri(uri: str) -> str:
    normalized = uri.strip().rstrip("/\\")
    if not normalized.lower().endswith(".sql"):
        return normalized
    parsed = parse_storage_uri(normalized)
    if parsed.is_local and parsed.local_path is not None:
        return str(parsed.local_path.parent)
    return normalized.rsplit("/", 1)[0]

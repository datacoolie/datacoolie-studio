from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, SourceRegistration
from datacoolie_studio.domains.credentials.store import (
    CredentialSecretStore,
    KeyringCredentialSecretStore,
)
from datacoolie_studio.domains.sources.discovery import (
    DiscoveredSource,
    discover_datacoolie_project_sources,
    discover_metadata_sources,
)
from datacoolie_studio.domains.sources.persistence import (
    create_source as persist_source,
    source_for_registration as _source_for_registration,
)
from datacoolie_studio.domains.sources.mutation_support import (
    delete_registration_if_orphan as _delete_registration_if_orphan,
    json_object as _json_object,
)
from datacoolie_studio.domains.sources.project_config import (
    ProjectConfigError,
    resolve_project_component_paths,
)
from datacoolie_studio.domains.sources.project_reconciliation import (
    _initialize_discovered_sources,
    _mark_missing_project_code_sources,
    _mark_missing_project_metadata_sources,
    _mark_missing_project_sql_sources,
    _project_code_reconciliation_allowed,
    _project_metadata_reconciliation_allowed,
    _project_sql_reconciliation_allowed,
    _refresh_managed_project_code_source,
    _refresh_managed_project_metadata_source,
    _refresh_managed_project_sql_source,
    source_import_item,
)
from datacoolie_studio.domains.sources.registration import (
    configured_location_dict,
    get_or_create_registration,
)
from datacoolie_studio.domains.sources.storage_binding import validate_and_normalize_binding
from datacoolie_studio.domains.storage.errors import StorageConfigurationError
from datacoolie_studio.domains.storage.factory import create_storage_adapter
from datacoolie_studio.domains.storage.uri import join_uri


def import_metadata_sources(
    session: Session,
    environment_id: int,
    *,
    uri: str,
    label: str | None = None,
    enabled: bool = True,
    storage: dict | None = None,
) -> dict:
    normalized_uri, binding = validate_and_normalize_binding(
        session, uri=uri, storage=storage, source_config=None
    )
    registration, _ = get_or_create_registration(
        session,
        environment_id=environment_id,
        purpose="metadata",
        input_uri=uri,
        canonical_uri=normalized_uri,
        binding=binding,
    )
    if storage and storage.get("provider") != "local":
        prior = _source_for_registration(
            session, registration.id, "metadata", normalized_uri
        )
        if prior is not None:
            source = prior
            if not registration.sources:
                session.delete(registration)
        else:
            source = persist_source(
                session,
                environment_id,
                source_kind="metadata",
                uri=normalized_uri,
                label=label,
                enabled=enabled,
                source_config={"discovery_mode": "direct"},
                storage=storage,
                registration=registration,
                input_uri=uri,
            )
        status = "existing" if prior is not None else "created"
        item = {
            "status": status,
            "id": source.id,
            "source_kind": source.source_kind,
            "uri": source.uri,
            "label": source.label,
            "record_counts": {},
            "source_config": _json_object(source.source_config_json),
            "configured_location": configured_location_dict(source),
        }
        return {
            "created": [item] if status == "created" else [],
            "existing": [item] if status == "existing" else [],
            "errors": [],
            "summary": {
                "created": 1 if status == "created" else 0,
                "existing": 1 if status == "existing" else 0,
                "errors": 0,
                "metadata_sources": 1,
                "code_artifacts": 0,
                "auto_validated": 0,
                "auto_synced": 0,
            },
        }
    discovery = discover_metadata_sources(normalized_uri, label=label)
    return _import_discovered_sources(
        session,
        environment_id,
        metadata_sources=discovery.metadata_sources,
        code_artifacts=[],
        errors=discovery.errors,
        enabled=enabled,
        materialize_created_sources=True,
        registration=registration,
    )


def import_datacoolie_project_sources(
    session: Session,
    environment_id: int,
    *,
    project_uri: str,
    metadata_subpath: str | None = None,
    sql_subpaths: Sequence[str] | None = None,
    code_subpaths: Sequence[str] | None = None,
    metadata_uri: str | None = None,
    code_uri: str | None = None,
    code_discovery_mode: str = "auto",
    enabled: bool = True,
    storage: dict | None = None,
    secret_store: CredentialSecretStore | None = None,
) -> dict:
    # Keep the caller's overrides separate from the effective paths.  Passing
    # the resolved paths back as overrides would incorrectly mark every
    # component as ``explicit`` and prevent later datacoolie.yml changes from
    # being observed by SQL, metadata, and function consumers.
    requested_metadata_subpath = metadata_subpath
    requested_sql_subpaths = sql_subpaths
    requested_code_subpaths = code_subpaths
    normalized_project_uri, binding = validate_and_normalize_binding(
        session,
        uri=project_uri,
        storage=storage,
        source_config=None,
    )
    binding_payload = {
        "provider": binding.provider,
        "auth_mode": binding.auth_mode,
        "credential_profile_id": binding.credential_profile_id,
        "options": binding.options,
    }
    adapter = (
        None
        if binding.provider == "local"
        else create_storage_adapter(
            binding,
            uri=normalized_project_uri,
            session=session,
            secret_store=secret_store or KeyringCredentialSecretStore(),
        )
    )
    try:
        component_paths = resolve_project_component_paths(
            normalized_project_uri,
            adapter=adapter,
            metadata_override=metadata_subpath,
            sql_override=requested_sql_subpaths,
            functions_override=requested_code_subpaths,
        )
    except ProjectConfigError as exc:
        raise StorageConfigurationError(str(exc)) from exc
    metadata_subpath = component_paths.metadata_path
    normalized_metadata_uri = _canonical_location(
        session, metadata_uri, binding_payload
    ) if metadata_uri else None
    normalized_code_uri = _canonical_location(
        session, code_uri, binding_payload
    ) if code_uri else None
    input_location_values = {
        "metadata_uri": (
            [metadata_uri.strip()]
            if metadata_uri
            else [join_uri(project_uri, path) for path in component_paths.metadata_paths]
        ),
        "sql_root_uri": [
            join_uri(project_uri, path) for path in component_paths.sql_paths
        ],
        "code_uri": (
            [code_uri.strip()]
            if code_uri
            else [
                join_uri(project_uri, path)
                for path in component_paths.functions_paths
            ]
        ),
    }
    canonical_location_values = {
        "metadata_uri": (
            [normalized_metadata_uri]
            if normalized_metadata_uri
            else [
                _canonical_location(session, join_uri(normalized_project_uri, path), binding_payload)
                for path in component_paths.metadata_paths
            ]
        ),
        "sql_root_uri": [
            _canonical_location(session, join_uri(normalized_project_uri, path), binding_payload)
            for path in component_paths.sql_paths
        ],
        "code_uri": (
            [normalized_code_uri]
            if normalized_code_uri
            else [
                _canonical_location(session, join_uri(normalized_project_uri, path), binding_payload)
                for path in component_paths.functions_paths
            ]
        ),
    }
    input_locations = {
        key: _one_or_many(values) for key, values in input_location_values.items()
    }
    canonical_locations = {
        key: _one_or_many(values)
        for key, values in canonical_location_values.items()
    }
    registration, _ = get_or_create_registration(
        session,
        environment_id=environment_id,
        purpose="project",
        input_uri=project_uri,
        canonical_uri=normalized_project_uri,
        binding=binding,
        input_locations=input_locations,
        canonical_locations=canonical_locations,
    )
    # Keep explicit URI overrides explicit.  When the caller omitted them,
    # discovery must receive ``None`` so an environment manifest can expose
    # all configured functions roots instead of being reduced to the first
    # resolved first root above.
    discovery_metadata_uri = normalized_metadata_uri if metadata_uri else None
    discovery_code_uri = normalized_code_uri if code_uri else None
    discovery = discover_datacoolie_project_sources(
        normalized_project_uri,
        metadata_subpath=requested_metadata_subpath,
        sql_subpaths=requested_sql_subpaths,
        code_subpaths=requested_code_subpaths,
        metadata_uri=discovery_metadata_uri,
        code_uri=discovery_code_uri,
        code_discovery_mode=code_discovery_mode,
        adapter=adapter,
    )
    return _import_discovered_sources(
        session,
        environment_id,
        metadata_sources=discovery.metadata_sources,
        code_artifacts=discovery.code_artifacts,
        sql_sources=discovery.sql_sources,
        errors=discovery.errors,
        enabled=enabled,
        # The application facade queues initial validation/materialization in
        # the same transaction after discovery has produced source IDs.
        materialize_created_sources=False,
        materialize_existing_sources=False,
        storage=binding_payload,
        secret_store=secret_store,
        registration=registration,
        reconcile_project_metadata=_project_metadata_reconciliation_allowed(discovery.errors),
        reconcile_project_sql=_project_sql_reconciliation_allowed(discovery.errors),
        reconcile_project_code=_project_code_reconciliation_allowed(discovery.errors),
    )




def _import_discovered_sources(
    session: Session,
    environment_id: int,
    *,
    metadata_sources: list[DiscoveredSource],
    code_artifacts: list[DiscoveredSource],
    sql_sources: list[DiscoveredSource] | None = None,
    errors: list[dict],
    enabled: bool,
    materialize_created_sources: bool = False,
    materialize_existing_sources: bool = False,
    storage: dict | None = None,
    secret_store: CredentialSecretStore | None = None,
    registration: SourceRegistration | None = None,
    reconcile_project_metadata: bool = False,
    reconcile_project_sql: bool = False,
    reconcile_project_code: bool = False,
) -> dict:
    created: list[dict] = []
    created_sources: list[EnvironmentSource] = []
    existing: list[dict] = []
    existing_sources: list[EnvironmentSource] = []
    stale: list[dict] = []

    for discovered in [*metadata_sources, *(sql_sources or []), *code_artifacts]:
        current = (
            _source_for_registration(
                session,
                registration.id,
                discovered.source_kind,
                discovered.uri,
            )
            if registration is not None
            else None
        )
        if current is not None:
            if (
                discovered.source_kind == "metadata"
                and registration is not None
                and registration.purpose == "project"
            ):
                _refresh_managed_project_metadata_source(
                    session,
                    current,
                    discovered,
                    enabled=enabled,
                    registration=registration,
                )
            elif (
                discovered.source_kind == "code"
                and registration is not None
                and registration.purpose == "project"
            ):
                _refresh_managed_project_code_source(
                    session,
                    current,
                    discovered,
                    enabled=enabled,
                    registration=registration,
                )
            elif (
                discovered.source_kind == "sql"
                and registration is not None
                and registration.purpose == "project"
            ):
                _refresh_managed_project_sql_source(
                    session,
                    current,
                    discovered,
                    enabled=enabled,
                    registration=registration,
                )
            existing_sources.append(current)
            existing.append(source_import_item(current, discovered, "existing"))
            continue
        current = _add_discovered_source(
            session,
            environment_id,
            discovered,
            enabled,
            storage=storage,
            registration=registration,
        )
        created_sources.append(current)
        created.append(source_import_item(current, discovered, "created"))

    seen_source_ids = {
        source.id for source in [*created_sources, *existing_sources]
    }
    if reconcile_project_metadata and registration is not None:
        stale.extend(
            _mark_missing_project_metadata_sources(
                session,
                registration,
                seen_source_ids=seen_source_ids,
                storage=storage,
            )
        )
    if reconcile_project_code and registration is not None:
        stale.extend(
            _mark_missing_project_code_sources(
                session,
                registration,
                seen_source_ids=seen_source_ids,
                storage=storage,
            )
        )
    if reconcile_project_sql and registration is not None:
        stale.extend(
            _mark_missing_project_sql_sources(
                session,
                registration,
                seen_source_ids=seen_source_ids,
                storage=storage,
            )
        )

    sources_to_initialize = [
        *(created_sources if materialize_created_sources else []),
        *(existing_sources if materialize_existing_sources else []),
    ]
    initialization = _initialize_discovered_sources(
        session, sources_to_initialize, secret_store=secret_store
    ) if sources_to_initialize else {
        "auto_validated": 0,
        "auto_synced": 0,
        "auto_sync_errors": [],
    }
    all_errors = [*errors, *initialization["auto_sync_errors"]]
    if registration is not None:
        _delete_registration_if_orphan(session, registration.id)
    summary = {
        "created": len(created),
        "existing": len(existing),
        "errors": len(all_errors),
        "metadata_sources": sum(1 for item in [*created, *existing] if item["source_kind"] == "metadata"),
        "code_artifacts": sum(1 for item in [*created, *existing] if item["source_kind"] == "code"),
        "auto_validated": initialization["auto_validated"],
        "auto_synced": initialization["auto_synced"],
    }
    sql_count = sum(
        1 for item in [*created, *existing] if item["source_kind"] == "sql"
    )
    if sql_count:
        summary["sql_sources"] = sql_count
    if stale:
        summary["stale"] = len(stale)
    return {
        "created": created,
        "existing": existing,
        "stale": stale,
        "errors": all_errors,
        "summary": summary,
    }




def _add_discovered_source(
    session: Session,
    environment_id: int,
    discovered: DiscoveredSource,
    enabled: bool,
    *,
    storage: dict | None = None,
    registration: SourceRegistration | None = None,
) -> EnvironmentSource:
    if discovered.source_kind not in {"metadata", "sql", "code"}:
        raise ValueError(f"Unsupported discovered source kind: {discovered.source_kind}")
    return persist_source(
        session,
        environment_id,
        source_kind=discovered.source_kind,
        uri=discovered.uri,
        label=discovered.label,
        enabled=enabled,
        source_config=discovered.source_config,
        storage=storage,
        registration=registration,
        input_uri=discovered.uri,
    )


def _canonical_location(session: Session, value: str, storage: dict) -> str:
    canonical, _ = validate_and_normalize_binding(
        session,
        uri=value,
        storage=storage,
        source_config=None,
    )
    return canonical


def _one_or_many(values: list[str]) -> str | list[str]:
    return values[0] if len(values) == 1 else values

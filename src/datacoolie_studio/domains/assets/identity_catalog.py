"""Compact published-metadata identity catalog and log endpoint resolver.

The catalog is intentionally smaller and cheaper than the Assets/Lineage
graph.  It reads enabled published editor materializations only, projects the
endpoint fields needed for destination identity, and keeps duplicate lookup
candidates instead of collapsing them in a dictionary.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.core.identity import (
    identity_name,
    name_to_uuid,
    normalize_workspace_id,
)
from datacoolie_studio.db.models import EnvironmentSource, MetadataMaterialization
from datacoolie_studio.domains.assets.endpoint_identity import (
    ASSETS_ENDPOINT_IDENTITY_VERSION,
    DESTINATION_CONFIGURE_IDENTITY_FIELDS,
    asset_seed_sort_key,
    destination_signature,
    endpoint_identity,
    endpoint_identity_quality,
    project_endpoint_identity,
    primary_asset_identifier,
)
from datacoolie_studio.domains.assets.identifiers import (
    build_asset_identifiers,
    canonical_asset_id,
    normalize_physical_path,
    api_endpoint_url,
)
from datacoolie_studio.domains.assets.registry import AssetRegistry
from datacoolie_studio.domains.read_models.cache import (
    cached_read_model,
    empty_parameters_fingerprint,
    read_model_build_lock,
    read_model_generation,
    replace_read_model,
)


IDENTITY_CATALOG_NAMESPACE = "assets.identity-catalog"
IDENTITY_CATALOG_PROJECTOR_VERSION = (
    # Presentation candidates are cached beside the compact identity rows, so
    # changing their projection must invalidate only this catalog family.  The
    # endpoint identity version remains the input fence for the identity map.
    f"assets-identity-catalog-v4-workspace:{ASSETS_ENDPOINT_IDENTITY_VERSION}"
)

# These fields are presentation data.  They deliberately do not participate
# in endpoint identity, destination signatures, or canonical asset IDs.
LAYOUT_PRESENTATION_FIELDS: tuple[str, ...] = (
    "format",
    "load_type",
    "partition_columns",
    "partition_expressions",
)


@dataclass(frozen=True, slots=True)
class IdentityCatalog:
    environment_id: int
    version: str
    rows: tuple[dict[str, Any], ...]
    _context_index: dict[tuple[str, str], tuple[int, ...]] = field(
        init=False, repr=False, compare=False
    )
    _identifier_index: dict[tuple[str, str], tuple[int, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        context: dict[tuple[str, str], set[int]] = defaultdict(set)
        identifiers: dict[tuple[str, str], set[int]] = defaultdict(set)
        for index, row in enumerate(self.rows):
            endpoint = row.get("endpoint") or {}
            for key, value in (
                ("dataflow_id", row.get("dataflow_id")),
                ("dataflow_name", row.get("dataflow_name")),
                ("workspace_id", row.get("workspace_id")),
                ("connection_id", endpoint.get("connection_id")),
                ("connection_name", endpoint.get("connection_name")),
                ("connection_type", endpoint.get("connection_type")),
            ):
                text = _text(value).casefold()
                if text:
                    context[(key, text)].add(index)
            for identifier in (
                []
                if endpoint.get("query") or endpoint.get("python_function")
                else build_asset_identifiers(endpoint)
            ):
                key = (str(identifier.get("kind")), str(identifier.get("normalized_value")))
                identifiers[key].add(index)
        object.__setattr__(
            self,
            "_context_index",
            {key: tuple(sorted(values)) for key, values in context.items()},
        )
        object.__setattr__(
            self,
            "_identifier_index",
            {key: tuple(sorted(values)) for key, values in identifiers.items()},
        )

    def context_candidates(self, values: Mapping[str, Any]) -> list[dict[str, Any]]:
        captured = _captured_endpoint(values)
        indices: set[int] = set()
        for key in (
            # Workspace/type alone are broad dimensions and cannot anchor a
            # fallback candidate.  They are checked after an ID/name anchor.
            "dataflow_id", "dataflow_name", "connection_id", "connection_name",
        ):
            text = _text(captured.get(key)).casefold()
            if text:
                indices.update(self._context_index.get((key, text), ()))
        return [self.rows[index] for index in sorted(indices)]

    def exact_identifier_candidates(self, captured: Mapping[str, Any]) -> list[dict[str, Any]]:
        indices: set[int] = set()
        for item in build_asset_identifiers(dict(captured)):
            key = (str(item.get("kind")), str(item.get("normalized_value")))
            indices.update(self._identifier_index.get(key, ()))
        return [self.rows[index] for index in sorted(indices)]

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "assets.identity-catalog.v2",
            "environment_id": self.environment_id,
            "version": self.version,
            "rows": [_json_safe(row) for row in self.rows],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "IdentityCatalog":
        return cls(
            environment_id=int(payload.get("environment_id") or 0),
            version=str(payload.get("version") or ""),
            rows=tuple(
                dict(row)
                for row in (payload.get("rows") or [])
                if isinstance(row, Mapping)
            ),
        )


def identity_revision(session: Session, environment_id: int) -> str:
    return metadata_catalog_revision(session, environment_id=environment_id)


def metadata_catalog_revision(session: Session, *, environment_id: int | None = None, source_ids: list[int] | None = None) -> str:
    """Return a cheap committed metadata-only revision token.

    The projection intentionally avoids loading ORM materialization objects or
    their JSON documents.  It follows the Environment Context catalog fence:
    source identity/configuration plus the published materialization
    fingerprint, restricted to enabled metadata sources in this environment.
    """

    probe = Session(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    try:
        rows = probe.execute(
            select(
                EnvironmentSource.id,
                EnvironmentSource.uri,
                EnvironmentSource.source_config_json,
                MetadataMaterialization.materialization_fingerprint,
            )
            .outerjoin(
                MetadataMaterialization,
                MetadataMaterialization.source_id == EnvironmentSource.id,
            )
            .where(
                (EnvironmentSource.environment_id == int(environment_id) if environment_id is not None else EnvironmentSource.id.in_(source_ids or [])),
                EnvironmentSource.source_kind == "metadata",
                EnvironmentSource.enabled.is_(True),
            )
            .order_by(EnvironmentSource.id)
        ).all()
    finally:
        probe.close()
    payload = [
        {
            "source_id": int(source_id),
            "uri": uri,
            "config": config,
            "materialization_fingerprint": materialization_fingerprint,
        }
        for source_id, uri, config, materialization_fingerprint in rows
    ]
    return hashlib.sha256(json.dumps({"version": 1, "payload": payload}, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def identity_version(
    session: Session,
    environment_id: int,
    analytics_context: Any | None = None,
) -> str:
    """Return the cache fence used by Monitoring before map construction."""

    revision = identity_revision(session, environment_id)
    return identity_version_from_inputs(
        revision,
        environment_id,
        analytics_context=analytics_context,
    )


def identity_version_from_inputs(
    revision: str,
    environment_id: int,
    *,
    source_ids: Iterable[int] | None = None,
    analytics_generation: Any | None = None,
    analytics_context: Any | None = None,
) -> str:
    if analytics_context is not None:
        source_ids = source_ids if source_ids is not None else _context_value(
            analytics_context, "source_ids", "enabled_source_ids"
        )
        analytics_generation = (
            analytics_generation
            if analytics_generation is not None
            else _context_value(analytics_context, "generation", "analytics_generation")
        )
    payload = {
        "version": ASSETS_ENDPOINT_IDENTITY_VERSION,
        "environment_id": int(environment_id),
        "metadata_revision": str(revision),
        "source_ids": sorted({int(value) for value in (source_ids or [])}),
        "analytics_generation": analytics_generation,
    }
    return _sha256(payload)


def load_or_build_identity_catalog(
    session: Session,
    environment_id: int,
    *,
    revision: str | None = None,
) -> IdentityCatalog:
    """Load a compact catalog or build it from published editor documents."""

    revision = revision or identity_revision(session, environment_id)
    parameters_fingerprint = empty_parameters_fingerprint()
    cached = cached_read_model(
        environment_id=environment_id,
        model_key=IDENTITY_CATALOG_NAMESPACE,
        parameters_fingerprint=parameters_fingerprint,
        input_fingerprint=revision,
        producer_version=IDENTITY_CATALOG_PROJECTOR_VERSION,
    )
    if cached is not None:
        return IdentityCatalog.from_payload(cached.payload)

    for _attempt in range(3):
        revision = identity_revision(session, environment_id)
        cached = cached_read_model(
            environment_id=environment_id,
            model_key=IDENTITY_CATALOG_NAMESPACE,
            parameters_fingerprint=parameters_fingerprint,
            input_fingerprint=revision,
            producer_version=IDENTITY_CATALOG_PROJECTOR_VERSION,
        )
        if cached is not None:
            return IdentityCatalog.from_payload(cached.payload)
        build_key = f"{environment_id}:{IDENTITY_CATALOG_NAMESPACE}:{revision}"
        with read_model_build_lock(build_key):
            revision = identity_revision(session, environment_id)
            cached = cached_read_model(
                environment_id=environment_id,
                model_key=IDENTITY_CATALOG_NAMESPACE,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=revision,
                producer_version=IDENTITY_CATALOG_PROJECTOR_VERSION,
            )
            if cached is not None:
                return IdentityCatalog.from_payload(cached.payload)
            generation = read_model_generation(
                environment_id=environment_id,
                model_key=IDENTITY_CATALOG_NAMESPACE,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=revision,
                producer_version=IDENTITY_CATALOG_PROJECTOR_VERSION,
            )
            rows = _published_endpoint_rows(session, environment_id)
            current_revision = identity_revision(session, environment_id)
            if current_revision != revision:
                continue
            catalog = IdentityCatalog(
                environment_id=int(environment_id),
                version=revision,
                rows=tuple(rows),
            )
            replace_read_model(
                environment_id=environment_id,
                model_key=IDENTITY_CATALOG_NAMESPACE,
                parameters_fingerprint=parameters_fingerprint,
                input_fingerprint=revision,
                producer_version=IDENTITY_CATALOG_PROJECTOR_VERSION,
                payload=catalog.to_payload(),
                expected_generation=generation,
            )
            if read_model_generation(
                environment_id=environment_id, model_key=IDENTITY_CATALOG_NAMESPACE,
                parameters_fingerprint=parameters_fingerprint, input_fingerprint=revision,
                producer_version=IDENTITY_CATALOG_PROJECTOR_VERSION,
            ) != generation or identity_revision(session, environment_id) != revision:
                continue
            return catalog
    raise RuntimeError("Unable to build a stable Assets identity catalog")


def resolve_destination_signature(
    environment_id: int,
    values: Mapping[str, Any],
    catalog: IdentityCatalog,
    *,
    signature: str | None = None,
    identity_version_value: str | None = None,
) -> dict[str, Any]:
    """Resolve one captured destination with conservative fallback semantics."""

    signature = signature or destination_signature(values)
    version = identity_version_value or catalog.version
    captured = _captured_endpoint(values)
    context_candidates = _context_candidates(values, catalog)
    if not context_candidates:
        context_candidates = _exact_identifier_candidates(captured, catalog)
    compatible = [
        row for row in context_candidates
        if _captured_locators_compatible(captured, row.get("endpoint") or {})
    ]
    if not compatible:
        compatible = [row for row in _exact_identifier_candidates(captured, catalog)
                      if _captured_locators_compatible(captured, row.get("endpoint") or {})]
    if context_candidates and not compatible:
        direct_identifiers = build_asset_identifiers(captured)
        if _has_qualified_captured_identity(direct_identifiers):
            identifier = _primary_identifier_from_identifiers(direct_identifiers)
            resolved = _resolved_row(
                environment_id,
                signature,
                version,
                {
                    "endpoint": captured,
                    "asset_id": canonical_asset_id(environment_id, identifier),
                    "metadata_source_ids": [],
                },
                captured,
                status="resolved",
                metadata_rows=[],
            )
            resolved["identity_reason"] = "captured locator differs from metadata; fallback disabled"
            resolved["identity_provenance"] = _field_provenance(captured, {}, mismatch=True)
            return resolved
        return _unresolved_row(
            environment_id,
            signature,
            version,
            "captured metadata locator mismatch",
            captured,
            status="conflict",
        )

    direct_identifiers = build_asset_identifiers(captured)
    if compatible:
        if _metadata_identity_ambiguous(compatible, captured):
            return _unresolved_row(
                environment_id,
                signature,
                version,
                "multiple metadata connection identities",
                captured,
                status="conflict",
                metadata_rows=compatible,
            )
        candidate_ids = sorted({str(row.get("asset_id")) for row in compatible if row.get("asset_id")})
        if len(candidate_ids) > 1:
            return _unresolved_row(
                environment_id,
                signature,
                version,
                "multiple compatible metadata bindings",
                captured,
                status="conflict",
                metadata_rows=compatible,
            )
        if candidate_ids:
            row = compatible[0]
            return _resolved_row(
                environment_id,
                signature,
                version,
                row,
                captured,
                status="resolved",
                metadata_rows=compatible,
            )

    if direct_identifiers and _has_qualified_captured_identity(direct_identifiers):
        identifier = _primary_identifier_from_identifiers(direct_identifiers)
        return {
            **_resolved_row(
                environment_id,
                signature,
                version,
                {
                    "endpoint": captured,
                    "asset_id": canonical_asset_id(environment_id, identifier),
                    "metadata_source_ids": [],
                },
                captured,
                status="resolved",
                metadata_rows=[],
            ),
            "identity_provenance": _field_provenance(captured, {}),
        }
    if direct_identifiers:
        return _unresolved_row(
            environment_id,
            signature,
            version,
            "captured endpoint is local or weakly scoped",
            captured,
            status="unresolved",
        )

    if len(compatible) == 1:
        return _resolved_row(
            environment_id,
            signature,
            version,
            compatible[0],
            captured,
            status="resolved",
            metadata_rows=compatible,
        )
    return _unresolved_row(
        environment_id,
        signature,
        version,
        "no qualified endpoint identity",
        captured,
        status="unresolved",
    )


def _published_endpoint_rows(session: Session, environment_id: int) -> list[dict[str, Any]]:
    probe = Session(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    try:
        materializations = probe.execute(
            select(
                EnvironmentSource.id,
                EnvironmentSource.uri,
                MetadataMaterialization.editor_document_json,
                MetadataMaterialization.materialization_fingerprint,
            )
            .join(
                MetadataMaterialization,
                MetadataMaterialization.source_id == EnvironmentSource.id,
            )
            .where(
                EnvironmentSource.environment_id == int(environment_id),
                EnvironmentSource.source_kind == "metadata",
                EnvironmentSource.enabled.is_(True),
            )
            .order_by(EnvironmentSource.id)
        ).all()
    finally:
        probe.close()
    rows: list[dict[str, Any]] = []
    prepared: list[tuple[int, str, str, Mapping[str, Any]]] = []
    global_connections: list[dict[str, Any]] = []
    for source_id, source_uri, editor_json, materialization_fingerprint in materializations:
        try:
            document = json.loads(editor_json or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(document, dict):
            continue
        raw = _editor_document_to_raw_metadata(document)
        prepared.append((
            int(source_id),
            str(source_uri),
            str(materialization_fingerprint or ""),
            raw,
        ))
    # Editor materializations are source shards.  Resolve inheritance against
    # the combined snapshot before generating fallback IDs, so a flow shard
    # can inherit the sole authored workspace from a connection shard.
    from datacoolie_studio.domains.metadata.document_lifecycle import (
        recompose_workspace_documents,
    )
    recompose_workspace_documents([raw for _source_id, _uri, _fingerprint, raw in prepared])
    for source_id, source_uri, materialization_fingerprint, raw in prepared:
        for connection in (raw.get("connections") or []):
            if not isinstance(connection, Mapping):
                continue
            if connection.get("_identity_workspace_error"):
                continue
            candidate = dict(connection)
            candidate["_metadata_source_ids"] = [int(source_id)]
            candidate["_materialization_fingerprints"] = [str(materialization_fingerprint or "")]
            candidate["_metadata_sources"] = [{
                "source_id": int(source_id),
                "revision": str(materialization_fingerprint or "") or None,
            }]
            global_connections.append(candidate)
    for source_id, source_uri, materialization_fingerprint, raw in prepared:
        rows.extend(
            _endpoint_rows_from_raw(
                int(environment_id),
                int(source_id),
                str(source_uri),
                str(materialization_fingerprint or ""),
                raw,
                connections=global_connections,
            )
        )
    rows = sorted(rows, key=_catalog_row_sort_key)
    return _canonicalize_catalog_asset_ids(int(environment_id), rows)


def _editor_document_to_raw_metadata(document: dict[str, Any]) -> dict[str, Any]:
    # Keep the catalog dependency one-way: importing this pure adapter lazily
    # avoids making the Assets package import the Metadata service at startup.
    from datacoolie_studio.domains.metadata.document_normalization import (
        editor_document_to_raw_metadata,
        metadata_payload,
    )
    from datacoolie_studio.domains.metadata.document_lifecycle import (
        normalize_document_workspace,
    )

    raw = metadata_payload(editor_document_to_raw_metadata(document))
    try:
        return normalize_document_workspace(raw)
    except ValueError:
        # Studio keeps malformed authored metadata editable.  Do not derive a
        # new valid identity from an invalid workspace field; the row remains
        # unresolved until DataCoolie semantic validation reports it.
        return raw


def _endpoint_rows_from_raw(
    environment_id: int,
    source_id: int,
    source_uri: str,
    materialization_fingerprint: str,
    raw: Mapping[str, Any],
    *,
    connections: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    from datacoolie_studio.domains.metadata.document_lifecycle import (
        normalize_document_workspace,
    )

    try:
        raw = normalize_document_workspace(dict(raw))
    except ValueError:
        raw = dict(raw)
    local_connections = [
        dict(item)
        for item in (raw.get("connections") or [])
        if isinstance(item, Mapping)
        and not item.get("_identity_workspace_error")
    ]
    connections = connections or local_connections
    rows: list[dict[str, Any]] = []
    for index, dataflow in enumerate(raw.get("dataflows") or []):
        if not isinstance(dataflow, Mapping) or not _is_active(dataflow.get("is_active", True)):
            continue
        if dataflow.get("_identity_workspace_error"):
            # Keep the authored row editable, but do not publish a derived
            # asset for an omitted scope in a mixed-workspace snapshot.
            continue
        try:
            workspace_id = normalize_workspace_id(dataflow.get("workspace_id"))
        except ValueError:
            continue
        dataflow_name_value = dataflow.get("name") or f"dataflow_{index + 1}"
        dataflow_name_identity = identity_name(dataflow_name_value)
        dataflow_id = str(
            dataflow.get("dataflow_id")
            if _identity_reference(dataflow.get("dataflow_id"))
            else (
                name_to_uuid(dataflow_name_identity, workspace_id)
                if dataflow_name_identity
                else ""
            )
        )
        dataflow_name = str(dataflow.get("name") or dataflow_id)
        for role in ("source", "destination"):
            endpoint = dataflow.get(role)
            for resolved in _resolve_raw_endpoint(
                endpoint,
                connections,
                workspace_id=workspace_id,
            ):
                resolved["dataflow_id"] = dataflow_id
                resolved["dataflow_name"] = dataflow_name
                if resolved.get("workspace_id") is None:
                    resolved["workspace_id"] = workspace_id
                # Scope only weak declarations by their metadata source. A
                # qualified physical/logical identifier remains source
                # independent, while local/unbound declarations must not be
                # merged across unrelated source documents.
                resolved["scope_source_id"] = source_id
                identifiers = (
                    []
                    if resolved.get("query") or resolved.get("python_function")
                    else build_asset_identifiers(resolved)
                )
                identity_type, identity, label = endpoint_identity(resolved)
                primary = _primary_identifier_from_identifiers(identifiers, identity_type, identity, label)
                metadata_source_ids = sorted({
                    source_id,
                    *{
                        int(value)
                        for value in (resolved.get("_metadata_source_ids") or [])
                        if value is not None
                    },
                })
                metadata_fingerprints = sorted({
                    materialization_fingerprint,
                    *{
                        str(value)
                        for value in (resolved.get("_materialization_fingerprints") or [])
                        if value
                    },
                })
                metadata_sources = _merge_metadata_sources(
                    resolved.get("_metadata_sources") or [],
                    [{
                        "source_id": source_id,
                        "revision": materialization_fingerprint or None,
                    }],
                )
                rows.append({
                    "metadata_source_id": source_id,
                    "metadata_source_uri": source_uri,
                    "materialization_fingerprint": materialization_fingerprint,
                    "materialization_fingerprints": metadata_fingerprints,
                    "metadata_sources": metadata_sources,
                    "role": role,
                    "dataflow_id": dataflow_id,
                    "dataflow_name": dataflow_name,
                    "workspace_id": resolved.get("workspace_id"),
                    "connection_id": resolved.get("connection_id"),
                    "connection_name": resolved.get("connection_name"),
                    "endpoint": _endpoint_projection(resolved),
                    "identifiers": identifiers,
                    "identity_type": identity_type,
                    "identity_quality": endpoint_identity_quality(resolved, identity_type),
                    "identity": identity,
                    "label": label,
                    "asset_id": canonical_asset_id(environment_id, primary),
                    "metadata_source_ids": metadata_source_ids,
                     # Layout is presentation data and must survive identity
                     # equivalence/deduplication as its own candidate set.
                     "layout_candidates": _layout_candidates_for_row(
                         resolved,
                         source_id=source_id,
                         materialization_fingerprint=materialization_fingerprint,
                     ),
                })
    return _dedupe_catalog_rows(rows)


def _resolve_raw_endpoint(
    value: Any,
    connections: list[dict[str, Any]],
    *,
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    from datacoolie_studio.domains.metadata.normalizer import (
        _index_connections,
        _resolve_endpoint,
    )

    # Keep the complete ID-owner index available even when a name lookup is
    # narrowed to one workspace.  A globally conflicting explicit ID must
    # invalidate named and inline references too; passing a singleton map
    # would hide the other owner from the resolver.
    connection_by_id = _index_connections(connections, "connection_id")
    endpoint = dict(value) if isinstance(value, Mapping) else {}
    reference = endpoint.get("connection")
    inline = reference if isinstance(reference, Mapping) else None
    if inline is not None:
        try:
            resolved = _resolve_endpoint(
                endpoint,
                {},
                connection_by_id,
                workspace_id=workspace_id,
            )
        except ValueError as exc:
            resolved = dict(endpoint)
            if workspace_id is not None:
                resolved.setdefault("workspace_id", workspace_id)
            resolved["_identity_error"] = str(exc)
        _attach_layout_candidates(resolved, endpoint, inline)
        return [resolved]
    connection_id_reference = _identity_reference(endpoint.get("connection_id"))
    connection_name_reference = _identity_reference(endpoint.get("connection_name"))
    reference_field = (
        "connection_id" if connection_id_reference
        else "connection_name" if connection_name_reference
        else "connection"
    )
    reference_text = _identity_reference(
        endpoint.get(reference_field) or ""
    )
    explicit_id = reference_field == "connection_id"
    all_id_candidates = [
        connection
        for connection in connections
        if explicit_id
        and _identity_reference(connection.get("connection_id")) == reference_text
    ]
    if explicit_id and _conflicting_connection_id_candidates(all_id_candidates):
        resolved = dict(endpoint)
        if workspace_id is not None:
            resolved.setdefault("workspace_id", workspace_id)
        resolved["_identity_error"] = "connection_id has conflicting workspace owners"
        _attach_layout_candidates(resolved, endpoint, {})
        return [resolved]
    candidates = [
        connection
        for connection in connections
        if reference_text
        and (
            _identity_reference(connection.get("connection_id")) == reference_text
            if explicit_id
            else _identity_reference(connection.get("name")) == reference_text
        )
        and _candidate_in_scope(connection, workspace_id, explicit_id=explicit_id)
    ]
    if not candidates:
        try:
            resolved = _resolve_endpoint(
                endpoint,
                {},
                connection_by_id,
                workspace_id=workspace_id,
            )
        except ValueError as exc:
            resolved = dict(endpoint)
            if workspace_id is not None:
                resolved.setdefault("workspace_id", workspace_id)
            resolved["_identity_error"] = str(exc)
        _attach_layout_candidates(resolved, endpoint, {})
        return [resolved]
    # Equivalent declarations are one candidate. Distinct bindings remain
    # separate so the resolver can report ambiguity instead of picking one.
    unique: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        try:
            merged = _resolve_endpoint(
                endpoint,
                {reference_text: [candidate]},
                connection_by_id,
                workspace_id=workspace_id,
            )
        except ValueError as exc:
            merged = dict(endpoint)
            if workspace_id is not None:
                merged.setdefault("workspace_id", workspace_id)
            merged["_identity_error"] = str(exc)
        _attach_layout_candidates(merged, endpoint, candidate)
        for key in ("_metadata_source_ids", "_materialization_fingerprints", "_metadata_sources"):
            if candidate.get(key):
                merged[key] = list(candidate[key])
        key = json.dumps(_endpoint_projection(merged), sort_keys=True, default=str, separators=(",", ":"))
        existing = unique.get(key)
        if existing is None:
            unique[key] = merged
        else:
            # Equivalent connection declarations can be present in multiple
            # enabled metadata documents. Keep one endpoint candidate while
            # retaining every source/fingerprint that actually contributed it.
            existing["_metadata_source_ids"] = sorted({
                int(value)
                for values in (
                    existing.get("_metadata_source_ids") or [],
                    merged.get("_metadata_source_ids") or [],
                )
                for value in values
                if value is not None
            })
            existing["_materialization_fingerprints"] = sorted({
                str(value)
                for values in (
                    existing.get("_materialization_fingerprints") or [],
                    merged.get("_materialization_fingerprints") or [],
                )
                for value in values
                if value
            })
            existing["_metadata_sources"] = _merge_metadata_sources(
                existing.get("_metadata_sources") or [],
                merged.get("_metadata_sources") or [],
            )
            existing["_layout_candidates"] = _merge_layout_candidates(
                existing.get("_layout_candidates") or [],
                merged.get("_layout_candidates") or [],
            )
    return list(unique.values())


def _merge_endpoint(endpoint: Mapping[str, Any], connection: Mapping[str, Any]) -> dict[str, Any]:
    resolved = project_endpoint_identity(endpoint, connection)
    for key in (
        "_metadata_source_ids",
        "_materialization_fingerprints",
        "_metadata_sources",
    ):
        if connection.get(key):
            resolved[key] = list(connection[key])
    return resolved


def _endpoint_projection(endpoint: Mapping[str, Any]) -> dict[str, Any]:
    projected = {
        key: endpoint.get(key)
        for key in (
            "connection_id", "connection_name", "connection_type", "connection_instance",
            "database_type", "host", "port", "workspace_id", "base_path", "base_url",
            "namespace", "bucket", "container",
            "catalog", "database", "schema_name", "table", "full_table", "path",
            "api_endpoint", "method", "query", "python_function", "scope_source_id",
        )
        if endpoint.get(key) is not None
    }
    if endpoint.get("configure") is not None:
        projected["configure"] = _safe_identity_configure(endpoint)
    return projected


def _safe_identity_configure(endpoint: Mapping[str, Any]) -> dict[str, Any]:
    """Project endpoint configure without retaining arbitrary source data.

    ``_resolve_endpoint`` preserves the authored endpoint configure for
    metadata consumers.  The compact identity catalog has a narrower cache
    contract: only scalar binding fields admitted by the shared identity
    projector may cross this boundary.  Layout values are carried separately
    in ``layout_candidates`` and never copied from configure here.
    """

    configure = endpoint.get("configure")
    if not isinstance(configure, Mapping):
        return {}
    return {
        field: value
        for field in DESTINATION_CONFIGURE_IDENTITY_FIELDS
        if (value := _identity_scalar(configure.get(field))) is not None
    }


def _attach_layout_candidates(
    resolved: dict[str, Any],
    raw_endpoint: Mapping[str, Any],
    connection: Mapping[str, Any],
) -> None:
    """Attach layout observations before endpoint candidates are deduped."""

    candidate = _layout_candidate(raw_endpoint, connection, resolved)
    resolved["_layout_candidates"] = _merge_layout_candidates(
        resolved.get("_layout_candidates") or [],
        [candidate],
    )


def _layout_candidates_for_row(
    resolved: Mapping[str, Any],
    *,
    source_id: int,
    materialization_fingerprint: str,
) -> list[dict[str, Any]]:
    candidates = resolved.get("_layout_candidates") or [{}]
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        # Preserve the source/revision attached to this presentation
        # candidate.  Equivalent layout values merge their own provenance in
        # ``_merge_layout_candidates``; alternatives must never inherit a
        # fabricated Cartesian union of all connection sources.
        sources = _merge_metadata_sources(
            candidate.get("metadata_sources") or [],
            [{"source_id": source_id, "revision": materialization_fingerprint or None}],
        )
        source_ids = sorted({int(item["source_id"]) for item in sources})
        revisions = sorted({str(item["revision"]) for item in sources if item.get("revision")})
        item = {
            field: _json_safe(candidate.get(field))
            for field in LAYOUT_PRESENTATION_FIELDS
        }
        item["metadata_sources"] = sources
        item["metadata_source_ids"] = source_ids
        item["metadata_revisions"] = revisions
        item["invalid_fields"] = sorted({
            str(field)
            for field in (candidate.get("invalid_fields") or [])
            if field
        })
        item["errors"] = _merge_layout_errors(candidate.get("errors") or [])
        result.append(item)
    return _merge_layout_candidates(result)


def _layout_candidate(
    raw_endpoint: Mapping[str, Any],
    connection: Mapping[str, Any],
    resolved: Mapping[str, Any],
) -> dict[str, Any]:
    """Read only safe presentation values, preserving key presence semantics."""

    def scalar_present(*names: str) -> tuple[str | None, bool, bool]:
        for source in (raw_endpoint, connection):
            for name in names:
                if name in source:
                    raw = source.get(name)
                    normalized = _layout_scalar(raw)
                    if normalized is not None:
                        return normalized, True, False
                    if _layout_input_is_missing(raw, scalar=True):
                        continue
                    # A malformed authored value must not be replaced by a
                    # connection default while building the presentation
                    # candidate.
                    return None, True, True
        return None, False, False

    def list_present(*names: str) -> tuple[list[str] | None, bool, bool]:
        for source in (raw_endpoint, connection):
            for name in names:
                if name not in source:
                    continue
                raw = source.get(name)
                normalized = _layout_list(raw)
                if normalized is not None:
                    return normalized, True, False
                if _layout_input_is_missing(raw, scalar=False):
                    continue
                return None, True, True
        return None, False, False

    def partition_present(
        *names: str,
    ) -> tuple[list[str] | None, list[str] | None, bool, bool]:
        for source in (raw_endpoint, connection):
            for name in names:
                if name not in source:
                    continue
                columns, expressions, missing, invalid = _layout_partition_spec(
                    source.get(name)
                )
                if missing:
                    continue
                return columns, expressions, True, invalid
        return None, None, False, False

    format_value, format_present, format_invalid = scalar_present("format")
    if not format_present:
        format_value = _layout_scalar(resolved.get("format"))
    load_value, _load_present, load_invalid = scalar_present("load_type")
    partition_value, derived_expressions, _partition_present, partition_invalid = partition_present(
        "partition_columns", "destination_partition_columns"
    )
    expression_value, _expression_present, expression_invalid = list_present(
        "partition_expressions", "partition_expression",
        "destination_partition_expressions", "destination_partition_expression",
    )
    if not _expression_present and derived_expressions is not None:
        expression_value = derived_expressions
        _expression_present = True
    invalid_fields = [
        field
        for field, invalid in (
            ("format", format_invalid),
            ("load_type", load_invalid),
            ("partition_columns", partition_invalid),
            ("partition_expressions", expression_invalid),
        )
        if invalid
    ]
    return {
        "format": format_value,
        "load_type": load_value,
        # A missing key remains None.  The normalizer's default ``[]`` is
        # intentionally never consulted for this field.
        "partition_columns": partition_value,
        "partition_expressions": expression_value,
        "metadata_sources": _layout_metadata_sources(connection),
        "invalid_fields": invalid_fields,
        "errors": [
            {"field": field, "code": "invalid_metadata_value"}
            for field in invalid_fields
        ],
    }


def _layout_input_is_missing(value: Any, *, scalar: bool) -> bool:
    if value is None:
        return True
    if scalar:
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return False
        text = str(value).strip()
        return not text or text.casefold() in {"unknown", "null", "none"}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return True
        try:
            return json.loads(text) is None
        except (TypeError, ValueError):
            return False
    return False


def _layout_partition_spec(
    value: Any,
) -> tuple[list[str] | None, list[str] | None, bool, bool]:
    """Normalize partition declarations and retain embedded expressions."""

    if value is None:
        return None, None, True, False
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, None, True, False
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            return None, None, False, True
    if not isinstance(value, (list, tuple)):
        return None, None, False, True
    columns: list[str] = []
    expressions: list[str] = []
    for item in value:
        if isinstance(item, str):
            column = item.strip()
            if not column:
                return None, None, False, True
            columns.append(column)
            continue
        if not isinstance(item, Mapping):
            return None, None, False, True
        column_value = item.get("column", item.get("name"))
        if not isinstance(column_value, str) or not column_value.strip():
            return None, None, False, True
        columns.append(column_value.strip())
        if "expression" not in item or item.get("expression") is None:
            continue
        expression = item.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            return None, None, False, True
        expressions.append(expression.strip())
    return columns, expressions or None, False, False


def _layout_metadata_sources(connection: Mapping[str, Any]) -> list[dict[str, Any]]:
    explicit = connection.get("_metadata_sources")
    if isinstance(explicit, list):
        return _merge_metadata_sources(explicit)
    # The published catalog attaches explicit records.  If a direct caller
    # supplies only private IDs, retain a source without inventing a pairing
    # between unrelated revision lists.
    source_ids = connection.get("_metadata_source_ids") or []
    return [
        {"source_id": int(source_id), "revision": None}
        for source_id in source_ids
        if source_id is not None
    ]


def _merge_layout_candidates(
    *groups: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Merge equal layout values while retaining distinct alternatives."""

    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for candidate in group:
            if not isinstance(candidate, Mapping):
                continue
            item = {
                field: _json_safe(candidate.get(field))
                for field in LAYOUT_PRESENTATION_FIELDS
            }
            key = json.dumps(item, sort_keys=True, separators=(",", ":"))
            existing = merged.get(key)
            if existing is None:
                existing = item
                merged[key] = existing
            existing["metadata_sources"] = _merge_metadata_sources(
                existing.get("metadata_sources") or [],
                candidate.get("metadata_sources") or [],
            )
            existing["metadata_source_ids"] = sorted({
                int(value)
                for values in (
                    existing.get("metadata_source_ids") or [],
                    candidate.get("metadata_source_ids") or [],
                )
                for value in values
                if value is not None
            })
            existing["metadata_revisions"] = sorted({
                str(value)
                for values in (
                    existing.get("metadata_revisions") or [],
                    candidate.get("metadata_revisions") or [],
                )
                for value in values
                if value
            })
            existing["invalid_fields"] = sorted({
                str(value)
                for values in (
                    existing.get("invalid_fields") or [],
                    candidate.get("invalid_fields") or [],
                )
                for value in values
                if value
            })
            existing["errors"] = _merge_layout_errors(
                existing.get("errors") or [],
                candidate.get("errors") or [],
            )
    return [merged[key] for key in sorted(merged)]


def _merge_layout_errors(*groups: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    for group in groups:
        for error in group:
            if not isinstance(error, Mapping):
                continue
            field = str(error.get("field")) if error.get("field") else None
            code = str(error.get("code")) if error.get("code") else None
            merged[(field, code)] = {"field": field, "code": code}
    return [
        merged[key]
        for key in sorted(merged, key=lambda item: (item[0] or "", item[1] or ""))
    ]


def _layout_scalar(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text.casefold() if text else None
    return None


def _layout_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, (list, tuple)):
        return None
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        text = item.strip()
        if not text:
            return None
        result.append(text)
    return result


def _context_candidates(values: Mapping[str, Any], catalog: IdentityCatalog) -> list[dict[str, Any]]:
    captured = _captured_endpoint(values)
    rows = catalog.context_candidates(values)
    captured_workspace = _safe_workspace(captured.get("workspace_id"))
    if captured_workspace is not None:
        rows = [
            row for row in rows
            if _safe_workspace(row.get("workspace_id")) == captured_workspace
        ]
    else:
        # A name-only captured endpoint cannot select one of several scoped
        # declarations. Explicit IDs are globally unique and remain valid.
        has_explicit_connection_id = bool(_identity_reference(captured.get("connection_id")))
        if not has_explicit_connection_id:
            rows = [
                row for row in rows
                if _safe_workspace(row.get("workspace_id")) is None
            ]
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        endpoint = row.get("endpoint") or {}
        captured_connection_id = _text(captured.get("connection_id")).casefold()
        candidate_connection_id = _text(
            _first_nonmissing(row.get("connection_id"), endpoint.get("connection_id"))
        ).casefold()
        captured_connection_name = _text(captured.get("connection_name")).casefold()
        candidate_connection_name = _text(
            _first_nonmissing(row.get("connection_name"), endpoint.get("connection_name"))
        ).casefold()
        captured_dataflow_id = _text(captured.get("dataflow_id")).casefold()
        candidate_dataflow_id = _text(row.get("dataflow_id")).casefold()
        captured_dataflow_name = _text(captured.get("dataflow_name")).casefold()
        candidate_dataflow_name = _text(row.get("dataflow_name")).casefold()

        score = 0
        connection_anchor = False
        if captured_connection_id and candidate_connection_id:
            if captured_connection_id != candidate_connection_id:
                continue
            # A stable connection ID is the strongest context anchor. A
            # changed/derived dataflow ID must not discard that match.
            score += 8
            connection_anchor = True
        elif captured_connection_name and candidate_connection_name:
            if captured_connection_name != candidate_connection_name:
                continue
            score += 4
            connection_anchor = True
        if captured_dataflow_id and candidate_dataflow_id:
            if captured_dataflow_id == candidate_dataflow_id:
                score += 4
            elif not connection_anchor:
                continue
        elif captured_dataflow_name and candidate_dataflow_name:
            if captured_dataflow_name == candidate_dataflow_name:
                score += 2
            elif not connection_anchor:
                continue
        if score:
            scored.append((score, dict(row)))
    if not scored:
        return []
    compatible = [(score, row) for score, row in scored
                  if _captured_locators_compatible(captured, row.get("endpoint") or {})]
    pool = compatible or scored
    highest = max(score for score, _ in pool)
    return [row for score, row in pool if score == highest]


def _exact_identifier_candidates(
    captured: Mapping[str, Any],
    catalog: IdentityCatalog,
) -> list[dict[str, Any]]:
    incoming = {
        (str(item.get("kind")), str(item.get("normalized_value")))
        for item in build_asset_identifiers(dict(captured))
    }
    if not incoming:
        return []
    matches: list[dict[str, Any]] = []
    for row in catalog.exact_identifier_candidates(captured):
        matches.append(dict(row))
    return matches


def _captured_endpoint(values: Mapping[str, Any]) -> dict[str, Any]:
    def get(column: str, *aliases: str) -> Any:
        for key in (column, *aliases):
            if key in values:
                value = _identity_scalar(values[key])
                if value is not None:
                    return value
        return None

    raw_configure = values.get("destination_configure")
    configure_invalid = bool(values.get("destination_configure_invalid"))
    configure = raw_configure
    if isinstance(raw_configure, str):
        try:
            configure = json.loads(raw_configure)
        except (TypeError, ValueError):
            configure = {}
            configure_invalid = bool(raw_configure.strip())
    elif raw_configure is not None and not isinstance(raw_configure, Mapping):
        configure = {}
        configure_invalid = True
    if raw_configure is None:
        configure = {}
    elif not isinstance(configure, Mapping):
        configure = {}
        configure_invalid = True
    configure = dict(configure)

    binding = values.get("destination_configure_binding")
    if binding is not None:
        if isinstance(binding, (list, tuple)):
            for index, field in enumerate(DESTINATION_CONFIGURE_IDENTITY_FIELDS):
                if index >= len(binding):
                    break
                value = _identity_scalar(binding[index])
                if value is None and binding[index] is not None:
                    configure_invalid = True
                elif value is not None:
                    configure[field] = value
        else:
            configure_invalid = True

    result = {
        "connection_id": get("destination_id", "connection_id"),
        "connection_name": get("destination_name", "connection_name"),
        "connection_type": get("destination_connection_type", "connection_type"),
        "workspace_id": get("workspace_id"),
        "dataflow_id": get("dataflow_id"),
        "dataflow_name": get("dataflow_name"),
        "scope_source_id": get("_source_id", "source_id"),
        "catalog": get("destination_catalog", "catalog"),
        "database": get("destination_database", "database"),
        "schema_name": get("destination_schema", "schema_name", "schema"),
        "table": get("destination_table", "table"),
        "full_table": get("destination_full_table", "full_table"),
        "path": get("destination_path", "path"),
        "_configure_invalid": configure_invalid,
    }
    for key in DESTINATION_CONFIGURE_IDENTITY_FIELDS:
        endpoint_key = "api_endpoint" if key == "endpoint" else (
            "schema_name" if key == "schema" else key
        )
        value = _identity_scalar(configure.get(key))
        if value is not None:
            # A fact may expose both a top-level locator and a configure
            # locator. Keep the top-level declaration when they disagree;
            # configure fills only a missing field.
            if not _present(result.get(endpoint_key)):
                result[endpoint_key] = value
        elif configure.get(key) is not None:
            result["_configure_invalid"] = True
    return result


def _captured_locators_compatible(captured: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    incoming_url = api_endpoint_url(captured.get("base_url"), captured.get("api_endpoint"))
    candidate_url = api_endpoint_url(candidate.get("base_url"), candidate.get("api_endpoint"))
    if incoming_url and candidate_url and incoming_url != candidate_url:
        return False
    for left, right in (
        ("catalog", "catalog"),
        ("database", "database"),
        ("schema_name", "schema_name"),
        ("table", "table"),
        ("full_table", "full_table"),
        ("path", "path"),
        ("connection_type", "connection_type"),
        ("workspace_id", "workspace_id"),
        ("host", "host"),
        ("port", "port"),
        ("database_type", "database_type"),
        ("connection_instance", "connection_instance"),
        ("base_url", "base_url"),
        ("base_path", "base_path"),
        ("namespace", "namespace"),
        ("bucket", "bucket"),
        ("container", "container"),
        ("api_endpoint", "api_endpoint"),
        ("method", "method"),
    ):
        if left in {"base_url", "api_endpoint"} and incoming_url and candidate_url:
            continue
        incoming = _text(captured.get(left))
        existing = _text(candidate.get(right))
        if not incoming or not existing:
            continue
        if left == "path":
            if normalize_physical_path(incoming) != normalize_physical_path(existing):
                return False
        elif left in {"host", "port", "database_type", "connection_instance", "base_url", "base_path", "api_endpoint"}:
            if incoming != existing:
                return False
        elif incoming.casefold() != existing.casefold():
            return False
    return True


def _metadata_identity_ambiguous(
    rows: Iterable[Mapping[str, Any]],
    captured: Mapping[str, Any],
) -> bool:
    """Keep distinct metadata owners ambiguous even when assets coalesce."""

    candidates = [row for row in rows if isinstance(row, Mapping)]
    captured_id = _identity_reference(captured.get("connection_id"))
    if captured_id:
        candidates = [
            row for row in candidates
            if _metadata_connection_id(row) == captured_id
        ]
    owners = {
        (
            _metadata_connection_id(row),
            _safe_workspace(
                row.get("workspace_id")
                if row.get("workspace_id") is not None
                else (row.get("endpoint") or {}).get("workspace_id")
            ),
            _identity_reference(
                row.get("connection_name")
                if row.get("connection_name") is not None
                else (row.get("endpoint") or {}).get("connection_name")
            ),
        )
        for row in candidates
    }
    return len(owners) > 1


def _metadata_connection_id(row: Mapping[str, Any]) -> str:
    endpoint = row.get("endpoint") or {}
    return _identity_reference(
        row.get("connection_id")
        if row.get("connection_id") is not None
        else endpoint.get("connection_id")
    )


def _matching_qualified_identifier(left, right):
    left_keys = {(item["kind"], item["normalized_value"]) for item in left
                 if item.get("qualification") in {"physical_exact", "connection_bound"}}
    return any((item["kind"], item["normalized_value"]) in left_keys for item in right
               if item.get("qualification") in {"physical_exact", "connection_bound"})


def _resolved_row(
    environment_id: int,
    signature: str,
    version: str,
    metadata_row: Mapping[str, Any],
    captured: Mapping[str, Any],
    *,
    status: str,
    metadata_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    metadata_rows = tuple(metadata_rows)
    metadata_endpoint = dict(metadata_row.get("endpoint") or {})
    endpoint = dict(metadata_endpoint)
    for key, value in captured.items():
        if key.startswith("_"):
            continue
        if value is not None and value != "":
            endpoint[key] = value
    metadata_sources = _metadata_source_records(metadata_rows)
    merged_identifiers = (
        []
        if endpoint.get("query") or endpoint.get("python_function")
        else build_asset_identifiers(endpoint)
    )
    metadata_identifiers = (
        []
        if metadata_endpoint.get("query") or metadata_endpoint.get("python_function")
        else build_asset_identifiers(metadata_endpoint)
    )
    merged_identity_type, merged_identity, merged_label = endpoint_identity(endpoint)
    merged_quality = endpoint_identity_quality(endpoint, merged_identity_type)
    matching_qualified = _matching_qualified_identifier(
        merged_identifiers,
        metadata_identifiers,
    )
    if matching_qualified:
        target = metadata_row.get("asset_id")
    elif merged_quality in {"physical_exact", "connection_bound"}:
        target = canonical_asset_id(
            environment_id,
            _primary_identifier_from_identifiers(
                merged_identifiers,
                merged_identity_type,
                merged_identity,
                merged_label,
            ),
        )
    elif merged_quality == "computational":
        target = canonical_asset_id(environment_id, _primary_identifier_from_identifiers(
            [], merged_identity_type, merged_identity, merged_label,
        ))
    else:
        # A compatible local/relative declaration is still unresolved. Keep
        # its target scoped to the captured/source declaration instead of
        # reusing a weak metadata row from another producer.
        target = _weak_target(environment_id, endpoint)
    actual_status = (
        "resolved"
        if merged_quality in {"physical_exact", "connection_bound", "computational"}
        else "unresolved"
    )
    fields = _field_provenance(captured, endpoint)
    captured_source = any(value == "captured" for value in fields.values())
    used_metadata = any(value == "metadata" for value in fields.values())
    if not used_metadata:
        metadata_sources = []
    if actual_status == "resolved":
        reason = "captured endpoint" if captured_source else "filled from compatible published metadata"
    else:
        reason = "compatible metadata is local or weakly scoped"
    if captured.get("_configure_invalid"):
        reason += "; invalid endpoint configure identity fields ignored"
    return {
        "signature": signature,
        "target": target,
        "identity_status": actual_status,
        "identity_reason": reason,
        "identity_version": version,
        "identity_provenance": fields,
        "identity_metadata_sources": metadata_sources,
        **_display_fields(endpoint),
    }


def _unresolved_row(
    environment_id: int,
    signature: str,
    version: str,
    reason: str,
    captured: Mapping[str, Any],
    *,
    status: str,
    metadata_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    metadata_rows = tuple(metadata_rows)
    metadata_sources: list[dict[str, Any]] = []
    if captured.get("_configure_invalid"):
        reason += "; invalid endpoint configure identity fields ignored"
    target = _weak_target(environment_id, captured)
    return {
        "signature": signature,
        "target": target,
        "identity_status": status,
        "identity_reason": reason,
        "identity_version": version,
        "identity_provenance": _field_provenance(captured, {}),
        "identity_metadata_sources": metadata_sources,
        **_display_fields(captured),
    }


def _weak_target(environment_id: int, endpoint: Mapping[str, Any]) -> str | None:
    if not any(
        _present(endpoint.get(key))
        for key in (
            "connection_id", "connection_name", "connection_type", "workspace_id",
            "catalog", "database", "schema_name", "table", "full_table", "path",
            "base_url", "api_endpoint", "namespace", "bucket", "container",
            "host", "port", "database_type", "connection_instance", "base_path",
            "query", "python_function",
        )
    ):
        return None
    identity_type, identity, label = endpoint_identity(endpoint)
    primary = _primary_identifier_from_identifiers(build_asset_identifiers(dict(endpoint)), identity_type, identity, label)
    return canonical_asset_id(environment_id, primary)


def _field_provenance(
    captured: Mapping[str, Any],
    endpoint: Mapping[str, Any],
    *,
    mismatch: bool = False,
) -> dict[str, str]:
    fields = {
        "connection_id": "connection_id",
        "connection_name": "connection_name",
        "connection_type": "connection_type",
        "host": "host",
        "port": "port",
        "database_type": "database_type",
        "connection_instance": "connection_instance",
        "workspace_id": "workspace_id",
        "base_path": "base_path",
        "base_url": "base_url",
        "namespace": "namespace",
        "bucket": "bucket",
        "container": "container",
        "api_endpoint": "api_endpoint",
        "method": "method",
        "query": "query",
        "python_function": "python_function",
        "catalog": "catalog",
        "database": "database",
        "schema_name": "schema_name",
        "table": "table",
        "full_table": "full_table",
        "path": "path",
    }
    result: dict[str, str] = {}
    for public, key in fields.items():
        captured_value = captured.get(key)
        metadata_value = endpoint.get(key)
        if _present(captured_value):
            result[public] = "captured"
        elif _present(metadata_value) and not mismatch:
            result[public] = "metadata"
    return result


def _metadata_source_records(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows:
        explicit = row.get("metadata_sources")
        if isinstance(explicit, list):
            records.extend(
                item
                for item in explicit
                if isinstance(item, Mapping) and item.get("source_id") is not None
            )
            continue
        source_ids = [row.get("metadata_source_id")]
        source_ids.extend(row.get("metadata_source_ids") or [])
        revisions = [row.get("materialization_fingerprint")]
        revisions.extend(row.get("materialization_fingerprints") or [])
        revisions = [str(value) for value in revisions if value]
        for source_id in source_ids:
            if source_id is None:
                continue
            for revision in revisions or [None]:
                records.append({
                    "source_id": int(source_id),
                    "revision": revision,
                })
    return _merge_metadata_sources(records)


def _merge_metadata_sources(*groups: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[int, str | None], dict[str, Any]] = {}
    for group in groups:
        for item in group:
            if not isinstance(item, Mapping) or item.get("source_id") is None:
                continue
            source_id = int(item["source_id"])
            revision_value = item.get("revision")
            revision = str(revision_value) if revision_value else None
            merged[(source_id, revision)] = {
                "source_id": source_id,
                "revision": revision,
            }
    return [
        merged[key]
        for key in sorted(merged, key=lambda item: (item[0], item[1] or ""))
    ]


def _has_qualified_captured_identity(identifiers: list[dict[str, str]]) -> bool:
    return any(
        item.get("qualification") in {"physical_exact", "connection_bound"}
        for item in identifiers
    )


def _display_fields(endpoint: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "destination_catalog": endpoint.get("catalog"),
        "destination_database": endpoint.get("database"),
        "destination_schema": endpoint.get("schema_name"),
        "destination_table": endpoint.get("table"),
        "destination_full_table": endpoint.get("full_table"),
        "destination_path": endpoint.get("path"),
        "destination_connection_type": endpoint.get("connection_type"),
        "destination_id": endpoint.get("connection_id"),
        "destination_name": endpoint.get("connection_name"),
    }


def _primary_identifier_from_identifiers(
    identifiers: list[dict[str, str]],
    identity_type: str | None = None,
    identity: str | None = None,
    label: str | None = None,
) -> dict[str, str]:
    identifier_map = {
        (str(item.get("kind")), str(item.get("normalized_value"))): dict(item)
        for item in identifiers
        if item.get("kind") and item.get("normalized_value")
    }
    return primary_asset_identifier(
        {
            "identity_type": identity_type,
            "identity": identity,
            "label": label,
        },
        identifier_map,
    )


def _dedupe_catalog_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (
            row.get("role"),
            row.get("dataflow_id"),
            row.get("workspace_id"),
            row.get("asset_id"),
            json.dumps(row.get("endpoint") or {}, sort_keys=True, default=str, separators=(",", ":")),
        )
        existing = unique.get(key)
        if existing is None:
            unique[key] = row
            continue
        # Identity-equivalent metadata rows may disagree on presentation
        # layout.  Keep all distinct alternatives and combine provenance for
        # equal values instead of allowing first-row order to win.
        existing["layout_candidates"] = _merge_layout_candidates(
            existing.get("layout_candidates") or [],
            row.get("layout_candidates") or [],
        )
        existing["metadata_source_ids"] = sorted({
            int(value)
            for values in (
                existing.get("metadata_source_ids") or [],
                row.get("metadata_source_ids") or [],
            )
            for value in values
            if value is not None
        })
        existing["metadata_sources"] = _merge_metadata_sources(
            existing.get("metadata_sources") or [],
            row.get("metadata_sources") or [],
        )
        existing["materialization_fingerprints"] = sorted({
            str(value)
            for values in (
                existing.get("materialization_fingerprints") or [],
                row.get("materialization_fingerprints") or [],
            )
            for value in values
            if value
        })
    return list(unique.values())


def _canonicalize_catalog_asset_ids(
    environment_id: int,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply the shared Assets alias registry to compact metadata rows.

    Computing an ID independently from each endpoint would leave a
    logical-table declaration and a path-only declaration for the same object
    with different targets.  Seed the existing Assets registry in stable order
    so the same alias and conflict rules used by Lineage determine the compact
    catalog's canonical target. Computational query/function rows have no
    locator aliases and retain their existing IDs.
    """
    registry = AssetRegistry(environment_id)
    observations: list[tuple[dict[str, Any], int, str]] = []
    for index, row in enumerate(rows):
        identifiers = [
            dict(item)
            for item in (row.get("identifiers") or [])
            if isinstance(item, Mapping) and item.get("kind")
        ]
        if not identifiers:
            continue
        node = {
            "id": row.get("identity") or row.get("asset_id"),
            "_catalog_index": index,
            "identity": row.get("identity"),
            "identity_type": row.get("identity_type"),
            "label": row.get("label"),
            "identifiers": identifiers,
            "role": row.get("role"),
        }
        observations.append((
            node,
            int(row.get("metadata_source_id") or 0),
            str(row.get("metadata_source_uri") or ""),
        ))
    if not observations:
        return rows
    ordered = sorted(
        observations,
        key=lambda item: (
            # Match AssetRegistry.add_metadata_observations exactly.
            asset_seed_sort_key(item[0]),
            int(item[1]),
            str(item[2]),
        ),
    )
    group_ids = registry.add_metadata_observations(ordered)
    for (node, _source_id, _source_uri), group_id in zip(ordered, group_ids, strict=True):
        row_index = node["_catalog_index"]
        rows[row_index]["asset_id"] = group_id
    return rows


def _catalog_row_sort_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    endpoint = row.get("endpoint") or {}
    return (
        _text(row.get("role")),
        _text(row.get("dataflow_id")),
        _text(row.get("metadata_source_id")),
        _text(row.get("asset_id")),
        _text(endpoint.get("connection_id")),
        _text(endpoint.get("path")),
        _text(endpoint.get("table")),
    )


def _context_value(context: Any, *keys: str) -> Any:
    if isinstance(context, Mapping):
        for key in keys:
            if key in context:
                return context[key]
        return None
    if isinstance(context, (tuple, list)):
        if any(key in {"source_ids", "enabled_source_ids"} for key in keys) and len(context) > 1:
            return context[1]
        if any(key in {"generation", "analytics_generation"} for key in keys) and len(context) > 2:
            return context[2]
        return None
    for key in keys:
        value = getattr(context, key, None)
        if value is not None:
            return value
    return None


def _sha256(value: Any) -> str:
    canonical = json.dumps(_json_safe(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _is_active(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "n", "off", "inactive"}
    return bool(value)


def _present(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _identity_reference(value: Any) -> str:
    value = identity_name(value)
    return value or ""


def _candidate_in_scope(
    connection: Mapping[str, Any],
    workspace_id: str | None,
    *,
    explicit_id: bool,
) -> bool:
    scope = _safe_workspace(workspace_id)
    if scope is not None:
        return _safe_workspace(connection.get("workspace_id")) == scope
    # A flow with no workspace cannot adopt a scoped connection from a mixed
    # snapshot. Explicit IDs remain globally unique, but endpoint ownership
    # still requires the flow scope to agree.
    return _safe_workspace(connection.get("workspace_id")) is None


def _safe_workspace(value: Any) -> str | None:
    try:
        return normalize_workspace_id(value)
    except ValueError:
        return None


def _conflicting_connection_id_candidates(
    candidates: list[dict[str, Any]],
) -> bool:
    if len(candidates) < 2:
        return False
    fingerprints = {
        (
            _safe_workspace(candidate.get("workspace_id")),
            _identity_reference(candidate.get("name")),
            json.dumps(
                _endpoint_projection(candidate),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ),
        )
        for candidate in candidates
    }
    return len(fingerprints) > 1


def _first_nonmissing(*values: Any) -> Any:
    """Select the first supplied identity value without truthiness loss."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (Mapping, list, tuple, set)) and not value:
            continue
        return value
    return None


def _identity_scalar(value: Any) -> Any:
    """Keep binding scalars, dropping nested/credential-bearing containers."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


__all__ = [
    "IDENTITY_CATALOG_NAMESPACE",
    "IDENTITY_CATALOG_PROJECTOR_VERSION",
    "LAYOUT_PRESENTATION_FIELDS",
    "IdentityCatalog",
    "identity_revision",
    "identity_version",
    "identity_version_from_inputs",
    "load_or_build_identity_catalog",
    "resolve_destination_signature",
]

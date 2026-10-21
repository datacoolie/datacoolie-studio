"""Application facade for the Assets read model."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import Environment
from datacoolie_studio.domains.assets.catalog import (
    ASSETS_PROJECTOR_VERSION,
    load_or_build_assets_catalog,
)
from datacoolie_studio.domains.assets.definitions import (
    describe_asset_definition,
    read_asset_definition,
)
from datacoolie_studio.domains.assets.detail import build_asset_detail
from datacoolie_studio.domains.assets.filtering import (
    filter_asset_rows,
    filter_reference_rows,
    sort_asset_rows,
    sort_reference_rows,
)
from datacoolie_studio.domains.assets.inventory import build_assets_inventory
from datacoolie_studio.domains.assets.models import AssetAttention, AssetsCatalog
from datacoolie_studio.domains.assets.project_reference_registry import (
    build_project_reference_registry,
)
from datacoolie_studio.domains.assets.reference_source import build_reference_occurrence_source
from datacoolie_studio.domains.assets.serialization import (
    compact_asset_row,
    compact_reference_row,
)
from datacoolie_studio.domains.lineage.query_service import load_or_build_lineage_graph
from datacoolie_studio.domains.reference_mappings import repository as reference_mapping_repository
from datacoolie_studio.domains.sources import repository as source_repository


def list_project_reference_registry(session: Session, project_id: int) -> dict[str, Any]:
    mappings = reference_mapping_repository.list_project_reference_mappings(session, project_id)
    environments = list(session.scalars(
        select(Environment)
        .where(Environment.project_id == project_id)
        .order_by(Environment.name, Environment.id)
    ))
    snapshots: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for environment in environments:
        try:
            catalog = load_or_build_assets_catalog(session, environment.id)
            assets = sort_asset_rows(list(catalog.payload.get("assets") or []), sort_by="display_name", sort_dir="asc")
            references = sort_reference_rows(
                list(catalog.payload.get("reference_groups") or []),
                sort_by="display_name",
                sort_dir="asc",
            )
            snapshots.append({
                "environment": {"id": environment.id, "name": environment.name},
                "assets": [compact_asset_row(item) for item in assets],
                "reference_groups": [compact_reference_row(item) for item in references],
                "catalog_version": catalog.input_fingerprint,
            })
        except Exception as exc:  # Keep one broken Environment from hiding the rest of the Project registry.
            failures.append({
                "environment_id": environment.id,
                "environment_name": environment.name,
                "message": str(exc) or "The environment asset registry could not be loaded.",
            })
    registry = build_project_reference_registry(snapshots, mappings)
    return {
        "project_id": project_id,
        "mappings": mappings,
        **registry,
        "failures": failures,
    }

def list_environment_assets(
    session: Session,
    environment_id: int,
    *,
    query: str | None = None,
    connection: str | None = None,
    format_name: str | None = None,
    asset_type: str | None = None,
    role: str | None = None,
    attention_state: str | None = None,
    scope: str | None = None,
    sort_by: str = "display_name",
    sort_dir: str = "asc",
    input_fingerprint: str | None = None,
) -> dict[str, Any]:
    catalog = load_or_build_assets_catalog(session, environment_id, input_fingerprint=input_fingerprint)
    rows = filter_asset_rows(
        list(catalog.payload.get("assets") or []),
        query=query,
        connection=connection,
        format_name=format_name,
        asset_type=asset_type,
        role=role,
        attention_state=attention_state,
        scope=scope,
    )
    rows = sort_asset_rows(rows, sort_by=sort_by, sort_dir=sort_dir)
    return {
        "summary": catalog.payload.get("summary") or {},
        "items": [compact_asset_row(item) for item in rows],
        "filter_options": (catalog.payload.get("filter_options") or {}).get("assets") or {},
        "catalog_version": catalog.input_fingerprint,
    }

def list_environment_asset_references(
    session: Session,
    environment_id: int,
    *,
    query: str | None = None,
    reference_type: str | None = None,
    provenance: str | None = None,
    resolution_state: str | None = None,
    attention_state: str | None = None,
    sort_by: str = "display_name",
    sort_dir: str = "asc",
    input_fingerprint: str | None = None,
) -> dict[str, Any]:
    catalog = load_or_build_assets_catalog(session, environment_id, input_fingerprint=input_fingerprint)
    rows = filter_reference_rows(
        list(catalog.payload.get("reference_groups") or []),
        query=query,
        reference_type=reference_type,
        provenance=provenance,
        resolution_state=resolution_state,
        attention_state=attention_state,
    )
    rows = sort_reference_rows(rows, sort_by=sort_by, sort_dir=sort_dir)
    return {
        "items": [compact_reference_row(item) for item in rows],
        "filter_options": (catalog.payload.get("filter_options") or {}).get("reference_groups") or {},
        "catalog_version": catalog.input_fingerprint,
    }

def get_environment_asset_reference(
    session: Session,
    environment_id: int,
    reference_id: str,
    *,
    input_fingerprint: str | None = None,
) -> dict[str, Any] | None:
    catalog = load_or_build_assets_catalog(session, environment_id, input_fingerprint=input_fingerprint)
    reference = next(
        (item for item in catalog.payload.get("reference_groups") or [] if str(item.get("id") or "") == reference_id),
        None,
    )
    if reference is None:
        return None
    return {
        "reference": reference,
        "occurrences": [
            item
            for item in catalog.payload.get("reference_occurrences") or []
            if str(item.get("reference_id") or "") == reference_id
        ],
        "catalog_version": catalog.input_fingerprint,
    }

def get_environment_asset(
    session: Session,
    environment_id: int,
    asset_id: str,
    *,
    input_fingerprint: str | None = None,
) -> dict[str, Any] | None:
    catalog = load_or_build_assets_catalog(session, environment_id, input_fingerprint=input_fingerprint)
    lineage = load_or_build_lineage_graph(session, environment_id, input_fingerprint=input_fingerprint)
    detail = build_asset_detail(asset_id, catalog.payload, lineage, include_definition=False)
    if detail is not None:
        detail["definition"] = describe_asset_definition(detail["asset"])
    return detail

def get_environment_asset_source(session: Session, environment_id: int, asset_id: str) -> dict[str, Any] | None:
    catalog = load_or_build_assets_catalog(session, environment_id)
    asset = next(
        (item for item in catalog.payload.get("assets") or [] if str(item.get("id") or "") == asset_id),
        None,
    )
    if asset is None:
        return None
    definition = read_asset_definition(
        asset,
        source_repository.list_sources(session, environment_id, "code"),
        session=session,
    )
    if definition is None:
        return None
    return {"definition": definition, "catalog_version": catalog.input_fingerprint}

def get_reference_occurrence_source(
    session: Session,
    environment_id: int,
    occurrence_id: str,
) -> dict[str, Any] | None:
    catalog = load_or_build_assets_catalog(session, environment_id)
    code_artifacts = source_repository.list_sources(session, environment_id, "code")
    occurrence = next(
        (item for item in catalog.payload.get("reference_occurrences") or [] if str(item.get("id") or "") == occurrence_id),
        None,
    )
    if occurrence is None:
        return None
    consumer_asset_id = str(occurrence.get("consumer_asset_id") or "")
    consumer_asset = next(
        (item for item in catalog.payload.get("assets") or [] if str(item.get("id") or "") == consumer_asset_id),
        None,
    )
    return build_reference_occurrence_source(
        occurrence, consumer_asset, code_artifacts, session=session
    )

__all__ = [
    "ASSETS_PROJECTOR_VERSION",
    "AssetAttention",
    "AssetsCatalog",
    "build_asset_detail",
    "build_assets_inventory",
    "get_environment_asset",
    "get_environment_asset_reference",
    "get_environment_asset_source",
    "get_reference_occurrence_source",
    "list_environment_asset_references",
    "list_environment_assets",
    "list_project_reference_registry",
    "load_or_build_assets_catalog",
]

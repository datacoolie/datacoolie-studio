"""Environment-level metadata merge, routing, and source lifecycle helpers."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path, PureWindowsPath
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.credentials.store import CredentialSecretStore
from datacoolie_studio.domains.metadata.document_columns import (
    canonical_editor_columns,
    column_display_name,
)
from datacoolie_studio.domains.metadata.document_lifecycle import (
    document_sheets_as_rows,
)
from datacoolie_studio.domains.metadata.document_primitives import string_value
from datacoolie_studio.domains.metadata.document_schema import (
    CONNECTION_COLUMNS,
    DATAFLOW_COLUMNS,
    REQUIRED_SHEETS,
    SCHEMA_HINT_COLUMNS,
    VISIBLE_STUDIO_ROUTING_COLUMN,
)
from datacoolie_studio.domains.metadata.ordering import canonicalize_editor_document
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.domains.metadata.revisions import row_source_id, source_revision
from datacoolie_studio.domains.metadata.source_policy import (
    metadata_format,
    metadata_root_uri,
    metadata_source_name,
    source_is_read_only,
)
from datacoolie_studio.domains.metadata.storage_io import (
    conditional_create,
    storage_for_source,
)
from datacoolie_studio.domains.sources.storage_binding import (
    apply_binding,
    binding_from_source,
)
from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.storage.uri import join_uri, parse_storage_uri


def resolve_environment_document_sources(
    session: Session,
    environment_id: int,
    sources: list[EnvironmentSource],
    document: dict[str, Any],
    *,
    secret_store: CredentialSecretStore | None = None,
    persist_new: bool = True,
) -> list[EnvironmentSource]:
    registry = {source.id: source for source in sources}
    tokens = metadata_source_tokens(sources)
    for rows in document_sheets_as_rows(document).values():
        for row in rows:
            source_id = row_source_id(row)
            if source_id in registry:
                apply_row_routing(row, registry[source_id])
                continue
            source_name = string_value(row.get("__metadata_source_name"))
            if not source_name:
                raise MetadataReadError(
                    "metadata_source is required for every metadata row"
                )
            token = source_token(source_name)
            if token in tokens and tokens[token] is None:
                raise MetadataReadError(f"Ambiguous metadata source target: {source_name}")
            source = tokens.get(token)
            if source is None:
                if persist_new:
                    source = create_metadata_source_for_routing(
                        session,
                        environment_id,
                        source_name,
                        list(registry.values()),
                        secret_store=secret_store,
                    )
                else:
                    source = build_metadata_source_for_routing(
                        session,
                        environment_id,
                        source_name,
                        list(registry.values()),
                    )
                registry[source.id] = source
                tokens = metadata_source_tokens(list(registry.values()))
            apply_row_routing(row, source)
    return list(registry.values())


def build_metadata_source_for_routing(
    session: Session,
    environment_id: int,
    source_name: str,
    existing_sources: list[EnvironmentSource],
) -> EnvironmentSource:
    """Build an unpersisted target descriptor for a prepare-only operation."""

    cloud_parents = [
        source
        for source in existing_sources
        if source.storage_provider != "local"
    ]
    if len(cloud_parents) > 1:
        raise MetadataReadError(
            "An explicit metadata target is required when an environment has "
            "multiple cloud metadata roots"
        )
    cloud_parent = cloud_parents[0] if cloud_parents else None
    if cloud_parent is not None:
        if source_is_read_only(cloud_parent):
            raise MetadataReadError(
                "The configured metadata provider is read-only for source creation"
            )
        root_uri = metadata_root_uri_text(cloud_parent)
        if not root_uri:
            raise MetadataReadError(
                "metadata_root_uri is required to create a cloud metadata object"
            )
        relative_name = safe_metadata_object_name(source_name)
        uri = join_uri(root_uri, relative_name)
        source = EnvironmentSource(
            environment_id=environment_id,
            source_kind="metadata",
            uri=uri,
            label=relative_name,
            enabled=True,
            source_config_json=json.dumps(
                {
                    "discovery_mode": "metadata_path",
                    "metadata_root_uri": root_uri,
                },
                sort_keys=True,
            ),
        )
        apply_binding(source, binding_from_source(cloud_parent))
        source._publication_storage_owner = cloud_parent
    else:
        base_dir = metadata_source_base_dir(existing_sources)
        uri = metadata_source_uri_for_new_name(source_name, base_dir)
        path = Path(uri).expanduser()
        if path.exists() and (not path.is_file() or path.is_symlink()):
            raise MetadataReadError(f"Metadata source must be a regular file: {uri}")
        source = EnvironmentSource(
            environment_id=environment_id,
            source_kind="metadata",
            uri=str(path),
            label=new_metadata_source_label(source_name, path, base_dir),
            enabled=True,
            source_config_json=json.dumps(
                {
                    "discovery_mode": "metadata_path",
                    "metadata_root_uri": str(base_dir),
                },
                sort_keys=True,
            ),
        )
        apply_binding(
            source,
            binding_from_source(existing_sources[0])
            if existing_sources
            else StorageBinding(provider="local", auth_mode="none"),
        )
        if existing_sources:
            source._publication_storage_owner = existing_sources[0]

    source.id = _temporary_source_id(source_name, existing_sources)
    return source


def _temporary_source_id(
    source_name: str,
    existing_sources: list[EnvironmentSource],
) -> int:
    used = {int(source.id) for source in existing_sources if source.id is not None}
    candidate = -1
    while candidate in used:
        candidate -= 1
    return candidate


def metadata_source_tokens(
    sources: list[EnvironmentSource],
) -> dict[str, EnvironmentSource | None]:
    result: dict[str, EnvironmentSource | None] = {}
    for source in sources:
        path = Path(source.uri).expanduser()
        values = [
            source.uri,
            path.name,
            path.stem,
            metadata_source_name(source),
            source.label or "",
        ]
        for value in values:
            token = source_token(value)
            if token:
                if token not in result:
                    result[token] = source
                elif result[token] is not source:
                    result[token] = None
    return result


def source_token(value: str) -> str:
    return value.strip().replace("\\", "/").lower()


def _is_absolute_or_rooted_path(value: str) -> bool:
    """Reject native and Windows path anchors on every host platform."""

    native = Path(value)
    windows = PureWindowsPath(value)
    return (
        native.is_absolute()
        or bool(native.drive)
        or windows.is_absolute()
        or bool(windows.drive)
        or bool(windows.root)
    )


def apply_row_routing(row: dict[str, Any], source: EnvironmentSource) -> None:
    row["__metadata_source_id"] = source.id
    row["__metadata_source_name"] = metadata_source_name(source)
    row["__metadata_source_uri"] = source.uri
    row["__metadata_source_kind"] = "metadata"
    row["__metadata_source_read_only"] = source_is_read_only(source)


def create_metadata_source_for_routing(
    session: Session,
    environment_id: int,
    source_name: str,
    existing_sources: list[EnvironmentSource],
    *,
    secret_store: CredentialSecretStore | None = None,
) -> EnvironmentSource:
    cloud_parents = [
        source
        for source in existing_sources
        if source.storage_provider != "local"
    ]
    if len(cloud_parents) > 1:
        raise MetadataReadError(
            "An explicit metadata target is required when an environment has "
            "multiple cloud metadata roots"
        )
    cloud_parent = cloud_parents[0] if cloud_parents else None
    if cloud_parent is not None:
        if source_is_read_only(cloud_parent):
            raise MetadataReadError(
                "The configured metadata provider is read-only for source creation"
            )
        root_uri = metadata_root_uri_text(cloud_parent)
        if not root_uri:
            raise MetadataReadError(
                "metadata_root_uri is required to create a cloud metadata object"
            )
        relative_name = safe_metadata_object_name(source_name)
        uri = join_uri(root_uri, relative_name)
        source = EnvironmentSource(
            environment_id=environment_id,
            source_kind="metadata",
            uri=uri,
            label=relative_name,
            enabled=True,
            source_config_json=json.dumps(
                {
                    "discovery_mode": "metadata_path",
                    "metadata_root_uri": root_uri,
                },
                sort_keys=True,
            ),
        )
        apply_binding(source, binding_from_source(cloud_parent))
        session.add(source)
        session.flush()
        storage = storage_for_source(
            session, source, secret_store=secret_store, writable=True
        )
        empty = json.dumps(
            {"connections": [], "dataflows": [], "schema_hints": []},
            indent=2,
        ).encode("utf-8")
        try:
            conditional_create(storage, source, empty)
            session.commit()
        except Exception:
            session.rollback()
            raise
        session.refresh(source)
        return source

    base_dir = metadata_source_base_dir(existing_sources)
    uri = metadata_source_uri_for_new_name(source_name, base_dir)
    path = Path(uri).expanduser()
    if path.exists() and not path.is_file():
        raise MetadataReadError(f"Metadata source must be a file: {uri}")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"connections": [], "dataflows": [], "schema_hints": []},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    source = EnvironmentSource(
        environment_id=environment_id,
        source_kind="metadata",
        uri=str(path),
        label=new_metadata_source_label(source_name, path, base_dir),
        enabled=True,
        source_config_json=json.dumps(
            {
                "discovery_mode": "metadata_path",
                "metadata_root_uri": str(base_dir),
            },
            sort_keys=True,
        ),
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def safe_metadata_object_name(source_name: str) -> str:
    normalized = source_name.strip().replace("\\", "/")
    parsed = parse_storage_uri(normalized)
    candidate = Path(normalized)
    if (
        parsed.provider != "local"
        or _is_absolute_or_rooted_path(normalized)
        or any(
            part in {"", ".", ".."} for part in normalized.split("/")
        )
    ):
        raise MetadataReadError(
            "New metadata source name must be a relative path under metadata_root_uri"
        )
    if not candidate.suffix:
        normalized = f"{normalized}.json"
    return normalized


def metadata_root_uri_text(source: EnvironmentSource) -> str | None:
    try:
        config = json.loads(source.source_config_json or "{}")
    except json.JSONDecodeError:
        return None
    value = config.get("metadata_root_uri") if isinstance(config, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def metadata_source_uri_for_new_name(source_name: str, base_dir: Path) -> str:
    normalized = source_name.strip().replace("\\", "/")
    candidate = Path(normalized)
    if (
        not normalized
        or _is_absolute_or_rooted_path(normalized)
        or any(part in {"", ".", ".."} for part in normalized.split("/") )
    ):
        raise MetadataReadError(
            "New metadata source name must be a relative path under the "
            "configured metadata root"
        )
    if not candidate.suffix:
        candidate = candidate.with_suffix(".json")
    root = base_dir.expanduser().resolve()
    target = (root / candidate).resolve(strict=False)
    if target == root or not target.is_relative_to(root):
        raise MetadataReadError(
            "New metadata source name must remain under the configured metadata root"
        )
    return str(target)


def metadata_source_base_dir(existing_sources: list[EnvironmentSource]) -> Path:
    for source in existing_sources:
        metadata_root = metadata_root_uri(source)
        if metadata_root:
            return metadata_root
    for source in existing_sources:
        path = Path(source.uri).expanduser()
        if path.exists() and path.is_file():
            return path.parent
        if path.suffix:
            return path.parent
    return Path.cwd()


def new_metadata_source_label(
    source_name: str,
    path: Path,
    base_dir: Path,
) -> str:
    raw = source_name.strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            return candidate.as_posix()
    try:
        return path.relative_to(base_dir).as_posix()
    except ValueError:
        return path.name


def environment_document_for_source(
    document: dict[str, Any],
    base_document: dict[str, Any],
    source: EnvironmentSource,
) -> dict[str, Any]:
    source_document = {
        "source": dict(
            base_document.get("source")
            or {
                "source_id": source.id,
                "environment_id": source.environment_id,
                "project_id": (
                    source.environment.project_id if source.environment else None
                ),
                "uri": source.uri,
                "name": metadata_source_name(source),
                "format": metadata_format(Path(source.uri).expanduser()),
                "revision": source_revision(source.uri),
            }
        ),
        "sheets": {},
        "issues": [],
        "metadata_document": deepcopy(base_document.get("metadata_document") or {}),
    }
    source_document["source"]["scope"] = "source"
    source_document["source"].pop("read_only", None)
    for sheet_name in REQUIRED_SHEETS:
        env_sheet = (document.get("sheets") or {}).get(sheet_name) or {}
        base_sheet = (base_document.get("sheets") or {}).get(sheet_name) or {}
        rows = [
            dict(row)
            for row in document_sheets_as_rows(document).get(sheet_name, [])
            if _routed_source_id(row) == source.id
        ]
        source_document["sheets"][sheet_name] = {
            "columns": merge_source_columns(
                sheet_name,
                base_sheet.get("columns") or [],
                env_sheet.get("columns") or [],
                rows,
            ),
            "rows": rows,
        }
    source_document["issues"] = []
    return source_document


def _routed_source_id(row: dict[str, Any]) -> int | None:
    """Return positive persisted and negative prepare-only source IDs."""

    value = row.get("__metadata_source_id")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def merge_source_columns(
    sheet_name: str,
    base_columns: list[dict[str, Any]],
    env_columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    seen: set[str] = set()
    row_keys = {key for row in rows for key in row}
    for column in [*base_columns, *env_columns]:
        if not isinstance(column, dict):
            continue
        key = column.get("key")
        if not key or str(key) in seen:
            continue
        if (
            str(key).startswith("__")
            or key in row_keys
            or is_preferred_column(str(key))
        ):
            seen.add(str(key))
            columns.append(dict(column))
    for key in sorted(row_keys - seen):
        seen.add(key)
        columns.append({"key": key, "name": column_display_name(key)})
    if VISIBLE_STUDIO_ROUTING_COLUMN not in seen:
        columns.append(
            {
                "key": VISIBLE_STUDIO_ROUTING_COLUMN,
                "name": column_display_name(VISIBLE_STUDIO_ROUTING_COLUMN),
            }
        )
    return canonical_editor_columns(sheet_name, columns, rows)


def merge_editor_sheet_documents(
    documents: list[dict[str, Any]],
    sheet_name: str,
) -> dict[str, Any]:
    """Merge one editor sheet while preserving the canonical column order."""

    columns: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    seen_columns: set[str] = set()
    for document in documents:
        sheet = (document.get("sheets") or {}).get(sheet_name) or {}
        for column in sheet.get("columns") or []:
            if not isinstance(column, dict):
                continue
            key = column.get("key")
            if not key or str(key) in seen_columns:
                continue
            seen_columns.add(str(key))
            columns.append(dict(column))
        rows.extend(row for row in sheet.get("rows") or [] if isinstance(row, dict))
    return {
        "columns": canonical_editor_columns(sheet_name, columns, rows),
        "rows": rows,
    }


def is_preferred_column(key: str) -> bool:
    return key in CONNECTION_COLUMNS or key in DATAFLOW_COLUMNS or key in SCHEMA_HINT_COLUMNS


def source_document_changed(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    return normalized_document_sheets(
        canonicalize_editor_document(left)
    ) != normalized_document_sheets(canonicalize_editor_document(right))


def normalized_document_sheets(document: dict[str, Any]) -> dict[str, Any]:
    sheets = document.get("sheets") or {}
    return {
        sheet_name: {
            "columns": [
                column.get("key")
                for column in (sheets.get(sheet_name) or {}).get("columns", [])
                if (
                    isinstance(column, dict)
                    and column.get("key")
                    and not str(column.get("key")).startswith("__")
                )
            ],
            "rows": [
                {key: value for key, value in row.items() if not key.startswith("__")}
                for row in document_sheets_as_rows(document).get(sheet_name, [])
            ],
        }
        for sheet_name in REQUIRED_SHEETS
    }


def apply_studio_routing_fields(
    sheets: dict[str, list[dict[str, Any]]],
    source: EnvironmentSource | None,
    *,
    source_id: int | None = None,
    source_uri: str | None = None,
    source_name: str | None = None,
    source_read_only: bool = False,
) -> None:
    if source is not None:
        source_id = source.id
        source_uri = source.uri
        source_name = metadata_source_name(source)
        source_read_only = source_is_read_only(source)
    routing = {
        "__metadata_source_id": source_id,
        "__metadata_source_name": source_name or source_uri or "",
        "__metadata_source_uri": source_uri or "",
        "__metadata_source_kind": "metadata",
        "__metadata_source_read_only": source_read_only,
    }
    for rows in sheets.values():
        for row in rows:
            for key, value in routing.items():
                row[key] = value

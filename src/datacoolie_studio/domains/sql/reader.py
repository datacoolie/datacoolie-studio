"""Resolve and read DataCoolie SQL references without executing SQL."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.credentials.store import (
    CredentialSecretStore,
    KeyringCredentialSecretStore,
)
from datacoolie_studio.domains.metadata.storage_io import storage_for_source
from datacoolie_studio.domains.metadata.storage_io import read_storage_bytes
from datacoolie_studio.domains.sources.project_config import (
    ProjectComponentPaths,
    ProjectConfigError,
    resolve_project_component_paths,
)
from datacoolie_studio.domains.sources.service import sql_root_uri_for_source
from datacoolie_studio.domains.storage.adapters import StorageAdapter
from datacoolie_studio.domains.storage.uri import join_uri, parse_storage_uri, uri_basename


_SHORTHAND_RE = re.compile(
    r"^[\w.-]+(?:[/\\][\w.-]+)*\.sql$",
    flags=re.IGNORECASE,
)
_ABSOLUTE_PATH_RE = re.compile(r"^(?:[A-Za-z]:[/\\]|\\\\|/)")
_URI_PATH_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:/")


class SqlReferenceError(ValueError):
    code = "sql_reference_invalid"


@dataclass(frozen=True)
class SqlReference:
    declared_query: str
    kind: str
    content: str
    file_uri: str | None = None
    base_uri: str | None = None
    base_kind: str | None = None
    relative_path: str | None = None
    source_uri: str | None = None
    revision: dict[str, object] | None = None
    config_revision: dict[str, object] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "declared_query": self.declared_query,
            "kind": self.kind,
            "content": self.content,
            "file_uri": self.file_uri,
            "base_uri": self.base_uri,
            "base_kind": self.base_kind,
            "relative_path": self.relative_path,
            "source_uri": self.source_uri,
            "revision": self.revision,
            "config_revision": self.config_revision,
        }


def resolve_sql_reference(
    session: Session,
    source: EnvironmentSource,
    query: object,
    *,
    secret_store: CredentialSecretStore | None = None,
) -> dict[str, Any]:
    if query is None:
        return SqlReference(declared_query="", kind="inline", content="", source_uri=source.uri).as_dict()
    if not isinstance(query, str):
        raise SqlReferenceError("source_query must be a string or null")
    value = query.strip()
    if not _looks_like_file_reference(value):
        return SqlReference(
            declared_query=query,
            kind="inline",
            content=query,
            source_uri=source.uri,
        ).as_dict()

    store = secret_store or KeyringCredentialSecretStore()
    target, storage_source = resolve_sql_file_target_with_source(
        source, value, session=session, secret_store=store
    )
    relative_path = str(target["relative_path"])
    base_uri = str(target["base_uri"])
    base_kind = str(target["base_kind"])
    file_uri = str(target["file_uri"])
    try:
        storage = storage_for_source(
            session,
            storage_source,
            secret_store=store,
        )
        raw, revision = read_storage_bytes(storage, file_uri)
    except Exception as exc:
        raise SqlReferenceError(
            f"SQL file could not be read: {relative_path} ({file_uri})"
        ) from exc
    if isinstance(raw, bytes):
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SqlReferenceError(f"SQL file is not valid UTF-8: {file_uri}") from exc
    else:
        content = str(raw)
    if not content.strip():
        raise SqlReferenceError(f"SQL file is empty: {file_uri}")
    return SqlReference(
        declared_query=query,
        kind="file",
        content=content,
        file_uri=file_uri,
        base_uri=base_uri,
        base_kind=base_kind,
        relative_path=relative_path,
        source_uri=source.uri,
        revision=revision,
        config_revision=target.get("config_revision"),
    ).as_dict()


def resolve_sql_file_target(
    source: EnvironmentSource,
    value: str,
    *,
    session: Session | None = None,
    secret_store: CredentialSecretStore | None = None,
) -> dict[str, Any]:
    """Resolve a declared SQL file reference without reading the object."""

    target, _ = resolve_sql_file_target_with_source(
        source,
        value,
        session=session,
        secret_store=secret_store,
    )
    return target


def resolve_sql_file_target_with_source(
    source: EnvironmentSource,
    value: str,
    *,
    session: Session | None = None,
    secret_store: CredentialSecretStore | None = None,
) -> tuple[dict[str, Any], EnvironmentSource]:
    (
        relative_path,
        base_uri,
        base_kind,
        config_revision,
        storage_source,
    ) = _reference_target(
        source,
        value,
        session=session,
        secret_store=secret_store,
    )
    return (
        {
            "relative_path": relative_path,
            "base_uri": base_uri,
            "base_kind": base_kind,
            "file_uri": join_uri(base_uri, relative_path),
            "config_revision": config_revision,
        },
        storage_source,
    )


def _looks_like_file_reference(value: str) -> bool:
    if not value or value.startswith("--") or value.startswith("/*"):
        return False
    if value.startswith("artifact:/"):
        return True
    if _SHORTHAND_RE.fullmatch(value):
        return True
    lowered = value.lower()
    looks_like_file = (
        lowered.endswith(".sql")
        or ".sql?" in lowered
        or ".sql#" in lowered
    )
    if looks_like_file and (
        _ABSOLUTE_PATH_RE.match(value)
        or _URI_PATH_RE.match(value)
        or "://" in value
        or ".sql?" in lowered
        or ".sql#" in lowered
    ):
        raise SqlReferenceError(
            "absolute, URI-shaped, or query/fragment SQL paths are unsupported; "
            "use a relative path below the SQL root or artifact:/<relative-path>"
        )
    return False


def _reference_target(
    source: EnvironmentSource,
    value: str,
    *,
    session: Session | None,
    secret_store: CredentialSecretStore | None,
) -> tuple[str, str, str, dict[str, object] | None, EnvironmentSource]:
    config = _source_config(source)
    component_paths = _current_project_component_paths(
        source,
        config,
        session=session,
        secret_store=secret_store,
    )
    storage_source = source
    manual_root_sources: tuple[tuple[str, EnvironmentSource], ...] = ()
    if component_paths is not None:
        project_uri = component_paths.project_uri
        sql_roots = component_paths.sql_uris
        config_revision = component_paths.config_revision
    else:
        project_uri = (
            _clean(config.get("project_uri"))
            or _parent_uri(_clean(config.get("metadata_root_uri")) or source.uri)
        )
        configured_roots = config.get("sql_root_uris")
        if isinstance(configured_roots, list):
            sql_roots = tuple(
                value.strip()
                for value in configured_roots
                if isinstance(value, str) and value.strip()
            )
        elif isinstance(config.get("sql_root_uri"), str) and config["sql_root_uri"].strip():
            sql_roots = (config["sql_root_uri"].strip(),)
        else:
            if session is not None:
                sql_sources = list(
                    session.scalars(
                        select(EnvironmentSource)
                        .where(
                            EnvironmentSource.environment_id == source.environment_id,
                            EnvironmentSource.source_kind == "sql",
                            EnvironmentSource.enabled.is_(True),
                        )
                        .order_by(EnvironmentSource.id)
                    )
                )
                manual_root_sources = tuple(
                    (sql_root_uri_for_source(item), item) for item in sql_sources
                )
            sql_roots = (
                tuple(root for root, _ in manual_root_sources)
                if manual_root_sources
                else (join_uri(project_uri, "sql"),)
            )
        config_revision = (
            config.get("config_revision")
            if isinstance(config.get("config_revision"), dict)
            else None
        )
    if value.startswith("artifact:"):
        raw_relative = value[len("artifact:") :]
        if not raw_relative.startswith("/") or raw_relative.startswith("//"):
            raise SqlReferenceError("artifact SQL references must use artifact:/<relative-path>")
        return _relative(raw_relative[1:]), project_uri, "project_root", config_revision, source
    if manual_root_sources:
        relative_path, sql_root, storage_source = _select_sql_root_with_sources(
            manual_root_sources, value
        )
        return relative_path, sql_root, "sql_root", config_revision, storage_source
    relative_path, sql_root = _select_sql_root(sql_roots, value)
    return relative_path, sql_root, "sql_root", config_revision, storage_source


def _select_sql_root_with_sources(
    root_sources: tuple[tuple[str, EnvironmentSource], ...],
    value: str,
) -> tuple[str, str, EnvironmentSource]:
    relative_path, sql_root = _select_sql_root(
        tuple(root for root, _ in root_sources), value
    )
    storage_source = next(
        source for root, source in root_sources if root == sql_root
    )
    return relative_path, sql_root, storage_source


def _select_sql_root(roots: tuple[str, ...], value: str) -> tuple[str, str]:
    """Select a configured SQL root using DataCoolie's prefix contract."""

    if not roots:
        raise SqlReferenceError("No SQL root is configured for this project")
    normalized = _relative(value)
    parts = normalized.split("/")
    if len(roots) == 1:
        root = roots[0]
        prefix = uri_basename(root).casefold()
        if len(parts) > 1 and parts[0].casefold() == prefix:
            return "/".join(parts[1:]), root
        return normalized, root

    prefixes = {uri_basename(root).casefold(): root for root in roots}
    if len(prefixes) != len(roots):
        raise SqlReferenceError(
            "SQL roots have ambiguous prefixes or storage owners; use distinct root names"
        )
    root = prefixes.get(parts[0].casefold())
    if root is None:
        expected = ", ".join(uri_basename(item) for item in roots)
        raise SqlReferenceError(
            f"SQL file reference {value!r} must start with one of [{expected}]"
        )
    if len(parts) == 1:
        raise SqlReferenceError(
            f"SQL file reference {value!r} must include a file below {parts[0]!r}"
        )
    return "/".join(parts[1:]), root


def _current_project_component_paths(
    source: EnvironmentSource,
    config: dict[str, Any],
    *,
    session: Session | None,
    secret_store: CredentialSecretStore | None,
) -> ProjectComponentPaths | None:
    project_uri = _clean(config.get("project_uri"))
    if not project_uri or config.get("discovery_mode") != "datacoolie_project":
        return None
    adapter: StorageAdapter | None = None
    if source.storage_provider != "local":
        if session is None:
            # A target-only caller cannot authenticate a remote config read.  It
            # can still use the last registered roots; read/publish callers pass
            # a session and re-resolve the config below.
            return None
        adapter = storage_for_source(
            session,
            source,
            secret_store=secret_store or KeyringCredentialSecretStore(),
        ).adapter
    try:
        return resolve_project_component_paths(
            project_uri,
            adapter=adapter,
            metadata_override=_explicit_component_override(config, "metadata"),
            sql_override=_explicit_component_override(config, "sql"),
            functions_override=_explicit_component_override(config, "functions"),
        )
    except ProjectConfigError as exc:
        raise SqlReferenceError(str(exc)) from exc


def _explicit_component_override(config: dict[str, Any], name: str) -> str | list[str] | None:
    full_paths = config.get("component_paths_all")
    paths = config.get("component_paths")
    if not isinstance(paths, dict):
        paths = {}
    provenance = config.get("component_path_provenance")
    if isinstance(provenance, dict) and provenance.get(name) not in {
        "default_input", "explicit", "explicit_uri"
    }:
        return None
    if name in {"sql", "functions"} and isinstance(full_paths, dict) and name in full_paths:
        # Keep malformed arrays visible to the canonical component validator.
        return full_paths[name]
    value = paths.get(name)
    return str(value) if isinstance(value, str) and value.strip() else None


def _relative(value: str) -> str:
    raw = normalize_sql_relative_path(value)
    return raw


def normalize_sql_relative_path(value: str) -> str:
    """Validate a path using the same containment rules as DataCoolie."""

    if not isinstance(value, str):
        raise SqlReferenceError("SQL path must be a string")
    raw = value.replace("\\", "/")
    if (
        not raw
        or raw.strip() != raw
        or any(ord(char) < 32 or ord(char) == 127 for char in raw)
        or "?" in raw
        or "#" in raw
        or "%2f" in raw.lower()
        or "%5c" in raw.lower()
        or "%2e" in raw.lower()
        or raw.startswith("~")
        or raw.startswith("/")
        or PurePosixPath(raw).is_absolute()
        or PureWindowsPath(raw).is_absolute()
        or PureWindowsPath(raw).drive
        or ":" in raw.split("/", 1)[0]
        or _URI_PATH_RE.match(raw)
        or "://" in raw
    ):
        raise SqlReferenceError(f"SQL path must be project-relative: {value!r}")
    parts = raw.split("/")
    if any(part == ".." for part in parts):
        raise SqlReferenceError(f"SQL path must not escape its configured root: {value!r}")
    parts = [part for part in parts if part not in {"", "."}]
    if not parts:
        raise SqlReferenceError(f"SQL path must contain a file name: {value!r}")
    return "/".join(parts)


def _source_config(source: EnvironmentSource) -> dict[str, Any]:
    try:
        value = json.loads(source.source_config_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parent_uri(uri: str) -> str:
    parsed = parse_storage_uri(uri)
    if parsed.is_local and parsed.local_path is not None:
        return str(parsed.local_path.parent)
    stripped = uri.rstrip("/\\")
    if "/" not in stripped:
        return stripped
    return stripped.rsplit("/", 1)[0]

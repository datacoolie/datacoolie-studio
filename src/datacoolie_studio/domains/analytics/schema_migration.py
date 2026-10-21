"""SQL-only migrations for the disposable analytics cache.

The analytics cache is a projection and is therefore safe to rebuild from a
writer-fenced copy of itself.  This module deliberately has no log/source
imports: a migration reads DuckDB rows already present in the cache and never
opens a source file.  Source replay is a separate operation owned by the
upgrade orchestrator.

The migration boundary is intentionally small:

* schema 17 is the last typed-column cache.  Its integer leaves may be BIGINT
  or BIGNUM and its optional-key marker is ``_present_paths``;
* schema 18 is the compiler-owned native mapping.  Integer leaves are HUGEINT,
  numeric leaves are DOUBLE and presence markers are removed;
* a mapping document may describe a later additive revision.  New optional
  columns are added to a candidate and old rows receive SQL NULL.  Required,
  removed, renamed, incompatible or unsupported recursive changes are blocked.

All operations are candidate based, including a root or STRUCT ``ADD COLUMN``.
That keeps one recovery model and avoids exposing a mixed generation while a
dependency or serving fact is being rebuilt.  The live writer lock is held by
the caller across the snapshot, candidate validation and swap.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Mapping

import duckdb

from datacoolie_studio.domains.analytics import schema
from datacoolie_studio.domains.analytics.connections import analytics_connections
from datacoolie_studio.domains.analytics.schema_compiler import contract_fingerprint
from datacoolie_studio.domains.analytics.sql import quote_identifier


LEGACY_SCHEMA_VERSION = 17
_REQUIRED_PROVENANCE_COLUMNS = (
    "_source_id",
    "_file_uri",
    "_file_kind",
    "_record_line",
)

MigrationKind = Literal[
    "ready",
    "additive_ddl",
    "stored_column_rewrite",
    "schema17_native_rewrite",
    "unsupported",
]


class SchemaMigrationError(RuntimeError):
    """A SQL-only migration cannot safely produce a complete candidate."""

    code = "analytics_schema_migration_failed"

    def __init__(self, message: str, *, reason_code: str = "migration_failed") -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class MappingDiff:
    """A deterministic compatibility result for two mapping documents."""

    kind: MigrationKind
    reason_code: str
    added_columns: tuple[str, ...] = ()
    removed_columns: tuple[str, ...] = ()
    changed_columns: tuple[str, ...] = ()
    required_additions: tuple[str, ...] = ()
    previous_fingerprint: str | None = None
    target_fingerprint: str | None = None

    @property
    def supported(self) -> bool:
        return self.kind != "unsupported"

    @property
    def requires_rewrite(self) -> bool:
        return self.kind in {"stored_column_rewrite", "schema17_native_rewrite"}


@dataclass(frozen=True)
class MigrationSnapshot:
    """Counts and source coverage captured before a candidate rewrite."""

    dataflow_rows: int
    job_rows: int
    source_ids: tuple[int, ...]
    dataflow_nulls: Mapping[str, int]
    job_nulls: Mapping[str, int]


def _normalise_type(value: Any) -> str:
    return " ".join(str(value or "").replace('"', "").upper().split())


def _mapping_columns(document: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    if not isinstance(document, Mapping):
        return {}
    columns = document.get("columns")
    if not isinstance(columns, Mapping):
        return {}
    result: dict[str, dict[str, str]] = {}
    for kind, values in columns.items():
        if isinstance(values, Mapping):
            result[str(kind)] = {str(name): str(dtype) for name, dtype in values.items()}
    return result


def _schema_document(document: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(document, Mapping):
        return {}
    value = document.get("schema")
    return value if isinstance(value, Mapping) else {}


def _resolve_schema_ref(document: Mapping[str, Any], ref: str) -> Mapping[str, Any] | None:
    if not ref.startswith("#/"):
        return None
    value: Any = document
    try:
        for component in ref[2:].split("/"):
            value = value[component.replace("~1", "/").replace("~0", "~")]
    except (KeyError, IndexError, TypeError):
        return None
    return value if isinstance(value, Mapping) else None


def _record_definition(
    document: Mapping[str, Any] | None,
    record_type: str,
) -> Mapping[str, Any] | None:
    """Resolve a record branch by its ``_type`` tag, independent of def names."""

    schema_document = _schema_document(document)
    branches = schema_document.get("oneOf", ())
    if isinstance(branches, list):
        for branch in branches:
            if not isinstance(branch, Mapping):
                continue
            resolved = (
                _resolve_schema_ref(schema_document, str(branch["$ref"]))
                if isinstance(branch.get("$ref"), str)
                else branch
            )
            if resolved is None:
                continue
            candidate = {
                **resolved,
                **{key: value for key, value in branch.items() if key != "$ref"},
            }
            properties = candidate.get("properties", {})
            type_node = properties.get("_type") if isinstance(properties, Mapping) else None
            if isinstance(type_node, Mapping) and type_node.get("const") == record_type:
                return candidate
            if isinstance(type_node, Mapping) and isinstance(type_node.get("$ref"), str):
                resolved_type = _resolve_schema_ref(schema_document, type_node["$ref"])
                if resolved_type is not None and resolved_type.get("const") == record_type:
                    return candidate
    return None


def _node_required_paths(
    document: Mapping[str, Any],
    node: Mapping[str, Any],
    parent: str,
    *,
    refs: tuple[str, ...] = (),
) -> set[str]:
    """Return JSON-pointer paths that are required in a fixed object."""

    ref = node.get("$ref")
    if isinstance(ref, str):
        if ref in refs:
            return set()
        resolved = _resolve_schema_ref(document, ref)
        if resolved is None:
            return set()
        return _node_required_paths(document, resolved, parent, refs=(*refs, ref))
    raw_type = node.get("type")
    types = set(raw_type if isinstance(raw_type, list) else [raw_type]) - {None, "null"}
    if types != {"object"} or node.get("additionalProperties", True) is not False:
        return set()
    required = {str(item) for item in node.get("required", ())}
    paths: set[str] = set()
    for name, child in node.get("properties", {}).items():
        path = f"{parent}/{str(name).replace('~', '~0').replace('/', '~1')}"
        if name in required:
            paths.add(path)
        if isinstance(child, Mapping):
            paths.update(_node_required_paths(document, child, path, refs=refs))
    return paths


def _required_root_columns(document: Mapping[str, Any]) -> set[str]:
    """Read required root members from the pinned mapping document."""

    result: set[str] = set()
    for record_type in ("dataflow_run_log", "job_run_log"):
        definition = _record_definition(document, record_type)
        if isinstance(definition, Mapping):
            result.update(str(name) for name in definition.get("required", ()))
    return result


def _required_paths_by_kind(document: Mapping[str, Any]) -> dict[str, set[str]]:
    """Return required JSON-pointer paths for each execution record kind."""

    result: dict[str, set[str]] = {}
    for kind in ("dataflow_run_log", "job_run_log"):
        definition = _record_definition(document, kind)
        if isinstance(definition, Mapping):
            result[kind] = _node_required_paths(_schema_document(document), definition, "")
    return result


_SCHEMA_CONSTRAINT_KEYS = frozenset(
    {
        "additionalItems",
        "additionalProperties",
        "contains",
        "const",
        "dependentRequired",
        "dependentSchemas",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxContains",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minContains",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "pattern",
        "patternProperties",
        "prefixItems",
        "propertyNames",
        "unevaluatedItems",
        "unevaluatedProperties",
        "uniqueItems",
    }
)


def _resolved_schema_node(
    document: Mapping[str, Any],
    node: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Resolve local references while retaining per-node overrides."""

    current: Mapping[str, Any] = node
    seen: set[str] = set()
    while isinstance(current.get("$ref"), str):
        ref = str(current["$ref"])
        if ref in seen:
            break
        seen.add(ref)
        resolved = _resolve_schema_ref(document, ref)
        if resolved is None:
            break
        current = {
            **resolved,
            **{key: value for key, value in current.items() if key != "$ref"},
        }
    return current


def _schema_type_set(node: Mapping[str, Any]) -> frozenset[str]:
    raw_type = node.get("type")
    if isinstance(raw_type, list):
        return frozenset(str(value) for value in raw_type)
    if raw_type is None:
        return frozenset()
    return frozenset({str(raw_type)})


def _schema_breaking_changes(
    previous: Mapping[str, Any] | None,
    target: Mapping[str, Any],
) -> tuple[str, ...]:
    """Find constraints tightened on fields that already existed.

    Storage columns cannot express JSON Schema constraints such as ``enum`` or
    ``minimum``.  Treating those changes as an additive SQL migration could
    publish old rows that no longer satisfy the source contract, so a changed
    constraint is conservatively unsupported.  New optional fields are skipped
    here and are handled by the storage-column diff.
    """

    old_document = _schema_document(previous)
    new_document = _schema_document(target)
    if not old_document or not new_document:
        return ()
    changes: list[str] = []

    def walk(
        old_node: Mapping[str, Any],
        new_node: Mapping[str, Any],
        path: str,
        depth: int,
    ) -> None:
        # The pinned schema contains recursive typed-value definitions.  Their
        # first materialised level is enough to identify a changed constraint;
        # a finite depth also prevents malformed metadata from looping forever.
        if depth > 64:
            return
        old_resolved = _resolved_schema_node(old_document, old_node)
        new_resolved = _resolved_schema_node(new_document, new_node)
        old_types = _schema_type_set(old_resolved)
        new_types = _schema_type_set(new_resolved)
        if old_types and new_types and old_types != new_types:
            changes.append(path or "/")
        for key in _SCHEMA_CONSTRAINT_KEYS:
            if key in new_resolved and old_resolved.get(key) != new_resolved.get(key):
                changes.append(f"{path}/{key}" if path else f"/{key}")

        old_properties = old_resolved.get("properties", {})
        new_properties = new_resolved.get("properties", {})
        if not isinstance(old_properties, Mapping) or not isinstance(new_properties, Mapping):
            return
        old_required = {str(value) for value in old_resolved.get("required", ())}
        new_required = {str(value) for value in new_resolved.get("required", ())}
        for name in sorted(new_required - old_required):
            if name in new_properties:
                child_path = f"{path}/{name}" if path else f"/{name}"
                changes.append(child_path)
        for name in sorted(set(old_properties) & set(new_properties)):
            child_path = f"{path}/{name}" if path else f"/{name}"
            old_child = old_properties[name]
            new_child = new_properties[name]
            if isinstance(old_child, Mapping) and isinstance(new_child, Mapping):
                walk(old_child, new_child, child_path, depth + 1)
        old_items = old_resolved.get("items")
        new_items = new_resolved.get("items")
        if isinstance(old_items, Mapping) and isinstance(new_items, Mapping):
            walk(old_items, new_items, f"{path}[]", depth + 1)
        elif "items" in new_resolved and old_items != new_items:
            changes.append(f"{path}/items" if path else "/items")

    for record_type in ("dataflow_run_log", "job_run_log"):
        old_definition = _record_definition(previous, record_type)
        new_definition = _record_definition(target, record_type)
        if isinstance(old_definition, Mapping) and isinstance(new_definition, Mapping):
            walk(old_definition, new_definition, "", 0)
    return tuple(sorted(set(changes)))


def _struct_fields(dtype: str) -> dict[str, str] | None:
    """Parse the shallow STRUCT field declarations in a DuckDB type string."""

    value = _normalise_type(dtype)
    start = value.find("STRUCT(")
    if start < 0:
        return None
    end = value.rfind(")")
    if end <= start:
        return None
    inner = value[start + len("STRUCT("):end]
    fields: list[str] = []
    depth = 0
    current: list[str] = []
    for char in inner:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            fields.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        fields.append("".join(current).strip())
    result: dict[str, str] = {}
    for field in fields:
        match = re.match(r'"?([^" ]+)"?\s+(.+)$', field)
        if not match:
            return None
        result[match.group(1)] = _normalise_type(match.group(2))
    return result


def _safe_nested_addition(old_type: str, new_type: str) -> bool:
    """Whether a changed STRUCT/LIST type only appends existing fields."""

    old_value = _normalise_type(old_type)
    new_value = _normalise_type(new_type)
    old_fields = _struct_fields(old_value)
    new_fields = _struct_fields(new_value)
    if old_fields is None or new_fields is None:
        return False
    return (
        set(old_fields) <= set(new_fields)
        and all(new_fields[name] == old_type_value for name, old_type_value in old_fields.items())
    )


def diff_mapping_documents(
    previous: Mapping[str, Any] | None,
    target: Mapping[str, Any],
    *,
    previous_fingerprint: str | None = None,
    target_fingerprint: str | None = None,
) -> MappingDiff:
    """Classify a schema mapping change without touching a database.

    ``columns`` is the storage projection.  The full ``schema`` member is used
    to reject a required-field addition; a missing schema document is treated
    conservatively when a storage column appears to be added.
    """

    old_schema = _schema_document(previous)
    new_schema = _schema_document(target)
    if not old_schema or not new_schema:
        return MappingDiff(
            "unsupported",
            "mapping_schema_unavailable",
            previous_fingerprint=previous_fingerprint,
            target_fingerprint=target_fingerprint,
        )
    old_columns = _mapping_columns(previous)
    new_columns = _mapping_columns(target)
    if not old_columns:
        return MappingDiff(
            "unsupported",
            "previous_mapping_unavailable",
            previous_fingerprint=previous_fingerprint,
            target_fingerprint=target_fingerprint,
        )
    kinds = sorted(set(old_columns) | set(new_columns))
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []
    safe_nested_changes: list[str] = []
    required_additions: list[str] = []
    required_roots = _required_root_columns(target)
    for kind in kinds:
        old = old_columns.get(kind, {})
        new = new_columns.get(kind, {})
        for name in sorted(set(new) - set(old)):
            path = f"{kind}.{name}"
            added.append(path)
            if name in required_roots:
                required_additions.append(path)
        removed.extend(f"{kind}.{name}" for name in sorted(set(old) - set(new)))
        for name in sorted(set(old) & set(new)):
            if _normalise_type(old[name]) == _normalise_type(new[name]):
                continue
            path = f"{kind}.{name}"
            if _safe_nested_addition(old[name], new[name]):
                safe_nested_changes.append(path)
            else:
                changed.append(path)
    old_required = _required_paths_by_kind(previous)
    new_required = _required_paths_by_kind(target)
    tightened: list[str] = []
    for kind, paths in new_required.items():
        for path in sorted(paths - old_required.get(kind, set())):
            # A newly required path is breaking even when its storage type did
            # not change. New fields are handled separately as additions.
            root_name = path.split("/", 2)[1] if "/" in path else ""
            if root_name in old_columns.get(kind, {}):
                tightened.append(f"{kind}{path}")
    required_additions.extend(tightened)
    changed.extend(_schema_breaking_changes(previous, target))
    if removed or changed or required_additions:
        return MappingDiff(
            "unsupported",
            "breaking_schema_change",
            tuple(added), tuple(removed), tuple(changed), tuple(required_additions),
            previous_fingerprint, target_fingerprint,
        )
    if not added and not safe_nested_changes:
        if previous_fingerprint and target_fingerprint and previous_fingerprint != target_fingerprint:
            return MappingDiff(
                "additive_ddl",
                "contract_revision",
                previous_fingerprint=previous_fingerprint,
                target_fingerprint=target_fingerprint,
            )
        return MappingDiff(
            "ready",
            "mapping_unchanged",
            previous_fingerprint=previous_fingerprint,
            target_fingerprint=target_fingerprint,
        )
    # A STRUCT/LIST type is only represented by its root column in the mapping
    # document.  It therefore needs a candidate rewrite when a nested member is
    # added, even though the root column name remains unchanged in future docs.
    nested = bool(safe_nested_changes) or any(
        "." in path and (
            "STRUCT" in _normalise_type(new_columns.get(path.split(".", 1)[0], {}).get(path.split(".", 1)[-1], ""))
            or "[]" in _normalise_type(new_columns.get(path.split(".", 1)[0], {}).get(path.split(".", 1)[-1], ""))
        )
        for path in added
    )
    return MappingDiff(
        "stored_column_rewrite" if nested else "additive_ddl",
        "optional_fields_added",
        tuple(added),
        previous_fingerprint=previous_fingerprint,
        target_fingerprint=target_fingerprint,
    )


def _legacy_columns(table_name: str) -> dict[str, str]:
    """Return the immutable physical schema17 contract for ``table_name``.

    This metadata is deliberately loaded from the shipped schema17 snapshot,
    rather than derived from the current compiler mapping.  A later release
    may add optional roots or nested members before an old schema17 cache is
    upgraded; deriving the old contract from today's mapping would then reject
    that valid cache before the candidate rewrite could add NULL columns.
    """

    return dict(_schema17_table_columns(table_name))


@lru_cache(maxsize=1)
def _schema17_mapping() -> Mapping[str, Mapping[str, str]]:
    """Load column metadata for the historical schema17 contract."""

    resource = resources.files(__package__).joinpath("schema17.mapping.json")
    try:
        with resource.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("The packaged schema17 mapping metadata is unreadable") from exc
    if not isinstance(document, Mapping) or document.get("schema_version") != LEGACY_SCHEMA_VERSION:
        raise RuntimeError("The packaged schema17 mapping metadata has an invalid version")
    tables = document.get("tables")
    if not isinstance(tables, Mapping):
        raise RuntimeError("The packaged schema17 mapping metadata has no tables")
    result: dict[str, Mapping[str, str]] = {}
    for table_name, table_document in tables.items():
        if not isinstance(table_document, Mapping):
            raise RuntimeError("The packaged schema17 mapping metadata has an invalid table")
        columns = table_document.get("columns")
        if not isinstance(columns, Mapping) or not columns:
            raise RuntimeError("The packaged schema17 mapping metadata has invalid columns")
        result[str(table_name)] = {
            str(column_name): str(data_type)
            for column_name, data_type in columns.items()
        }
    return result


def _schema17_table_columns(table_name: str) -> Mapping[str, str]:
    try:
        return _schema17_mapping()[table_name]
    except KeyError as exc:
        raise RuntimeError(
            f"The packaged schema17 mapping has no table {table_name!r}"
        ) from exc


def _legacy_type_matches(actual: str, expected: str) -> bool:
    actual_norm = _normalise_type(actual)
    expected_norm = _normalise_type(expected)
    if actual_norm == expected_norm:
        return True
    if expected_norm == "TIMESTAMPTZ" and actual_norm == "TIMESTAMP WITH TIME ZONE":
        return True
    # A partially completed 17 cache may already have converted some integer
    # leaves to HUGEINT. It is safe to accept that equivalent representation;
    # the marker and complete column set still have to match exactly.
    if expected_norm == "BIGNUM" and actual_norm in {"HUGEINT", "BIGINT"}:
        return True
    if "BIGNUM" in expected_norm:
        return actual_norm.replace("HUGEINT", "BIGNUM") == expected_norm
    return False


def validate_schema17_cache(conn: Any) -> None:
    """Validate that ``conn`` is a complete, known schema17 cache.

    This guard intentionally does not accept an arbitrary pair of tables just
    because they contain ``job_id``.  It checks base-table shape, known legacy
    columns, technical provenance columns and the singleton metadata/source
    tables before any candidate rewrite begins.
    """

    if not schema.table_exists(conn, schema.ANALYTICS_META_TABLE):
        raise SchemaMigrationError("Schema17 cache is missing analytics metadata", reason_code="schema17_incomplete")
    expected_metadata_types = _legacy_columns(schema.ANALYTICS_META_TABLE)
    metadata_columns = set(schema.table_columns(conn, schema.ANALYTICS_META_TABLE))
    expected_metadata_columns = set(expected_metadata_types)
    if metadata_columns != expected_metadata_columns:
        raise SchemaMigrationError(
            "Schema17 analytics metadata has an incomplete or unsupported shape",
            reason_code="schema17_incomplete",
        )
    metadata_types = schema.table_column_types(conn, schema.ANALYTICS_META_TABLE)
    invalid_metadata_types = sorted(
        name
        for name, expected in expected_metadata_types.items()
        if not schema.duckdb_type_matches(metadata_types.get(name, ""), expected)
    )
    if invalid_metadata_types:
        raise SchemaMigrationError(
            f"Schema17 analytics metadata has incompatible types: {invalid_metadata_types}",
            reason_code="schema17_incompatible",
        )
    try:
        meta_rows = conn.execute(
            f"SELECT singleton_id, schema_version, build_state "
            f"FROM {schema.ANALYTICS_META_TABLE} ORDER BY singleton_id"
        ).fetchall()
    except duckdb.Error as exc:
        raise SchemaMigrationError(
            "Schema17 cache metadata is unreadable", reason_code="schema17_incomplete"
        ) from exc
    if meta_rows != [(1, LEGACY_SCHEMA_VERSION, "ready")]:
        raise SchemaMigrationError(
            "Schema17 cache metadata is not a complete ready generation",
            reason_code="schema17_incomplete",
        )

    for table_name in (schema.DATAFLOW_TABLE, schema.JOB_TABLE):
        if not schema.table_exists(conn, table_name):
            raise SchemaMigrationError(
                f"Schema17 cache is missing {table_name}", reason_code="schema17_incomplete"
            )
        table_kind = conn.execute(
            "SELECT table_type FROM information_schema.tables WHERE table_name = ?",
            [table_name],
        ).fetchall()
        if table_kind != [("BASE TABLE",)]:
            raise SchemaMigrationError(
                f"Schema17 {table_name} must be a base table", reason_code="schema17_incomplete"
            )
        columns = set(schema.table_columns(conn, table_name))
        expected_types = _legacy_columns(table_name)
        required = set(expected_types)
        if not required <= columns:
            missing = sorted(required - columns)
            raise SchemaMigrationError(
                f"Schema17 {table_name} is missing required columns: {missing[:12]}",
                reason_code="schema17_incomplete",
            )
        unknown = sorted(columns - set(expected_types))
        if unknown:
            raise SchemaMigrationError(
                f"Schema17 {table_name} contains unsupported columns: {unknown[:8]}",
                reason_code="schema17_incompatible",
            )
        actual_types = schema.table_column_types(conn, table_name)
        invalid_types = sorted(
            name
            for name, expected in expected_types.items()
            if not _legacy_type_matches(actual_types.get(name, ""), expected)
        )
        if invalid_types:
            raise SchemaMigrationError(
                f"Schema17 {table_name} has incompatible types: {invalid_types[:8]}",
                reason_code="schema17_incompatible",
            )
        provenance_columns = set(_REQUIRED_PROVENANCE_COLUMNS)
        if not provenance_columns <= columns:
            raise SchemaMigrationError(
                f"Schema17 {table_name} is missing provenance columns",
                reason_code="schema17_incomplete",
            )
        incomplete_provenance = int(
            conn.execute(
                f"SELECT count(*) FROM {quote_identifier(table_name)} WHERE "
                + " OR ".join(
                    (
                        f"{quote_identifier(column)} IS NULL"
                        if column == "_source_id"
                        else (
                            f"{quote_identifier(column)} IS NULL OR "
                            f"trim(CAST({quote_identifier(column)} AS VARCHAR)) = ''"
                            if column in {"_file_uri", "_file_kind"}
                            else f"{quote_identifier(column)} IS NULL OR {quote_identifier(column)} <= 0"
                        )
                    )
                    for column in ("_source_id", *_REQUIRED_PROVENANCE_COLUMNS)
                )
            ).fetchone()[0]
        )
        if incomplete_provenance:
            raise SchemaMigrationError(
                f"Schema17 {table_name} has rows without source/file/line provenance",
                reason_code="schema17_incomplete",
            )
    if not schema.table_exists(conn, schema.CACHE_SOURCES_TABLE):
        raise SchemaMigrationError("Schema17 cache is missing source coverage", reason_code="schema17_incomplete")
    expected_source_types = _legacy_columns(schema.CACHE_SOURCES_TABLE)
    source_columns = set(schema.table_columns(conn, schema.CACHE_SOURCES_TABLE))
    expected_source_columns = set(expected_source_types)
    if source_columns != expected_source_columns:
        raise SchemaMigrationError(
            "Schema17 source coverage table has an incompatible shape",
            reason_code="schema17_incomplete",
        )
    source_types = schema.table_column_types(conn, schema.CACHE_SOURCES_TABLE)
    if any(
        not schema.duckdb_type_matches(source_types.get(name, ""), expected)
        for name, expected in expected_source_types.items()
    ):
        raise SchemaMigrationError(
            "Schema17 source coverage table has incompatible types",
            reason_code="schema17_incompatible",
        )
    invalid_source_coverage = int(
        conn.execute(
            f"SELECT count(*) FROM {quote_identifier(schema.CACHE_SOURCES_TABLE)} "
            "WHERE source_id IS NULL OR generation IS NULL"
        ).fetchone()[0]
    )
    duplicate_source_coverage = conn.execute(
        f"SELECT source_id FROM {quote_identifier(schema.CACHE_SOURCES_TABLE)} "
        "GROUP BY source_id HAVING count(*) <> 1 LIMIT 1"
    ).fetchone()
    if invalid_source_coverage or duplicate_source_coverage is not None:
        raise SchemaMigrationError(
            "Schema17 source coverage has incomplete or duplicate source rows",
            reason_code="schema17_incomplete",
        )
    for table_name in (schema.DATAFLOW_TABLE, schema.JOB_TABLE):
        uncovered = int(
            conn.execute(
                f"SELECT count(*) FROM {quote_identifier(table_name)} AS rows "
                f"WHERE rows.{quote_identifier('_source_id')} IS NULL OR NOT EXISTS ("
                f"SELECT 1 FROM {quote_identifier(schema.CACHE_SOURCES_TABLE)} AS sources "
                f"WHERE sources.{quote_identifier('source_id')} = rows.{quote_identifier('_source_id')})"
            ).fetchone()[0]
        )
        if uncovered:
            raise SchemaMigrationError(
                f"Schema17 {table_name} has rows outside cached source coverage",
                reason_code="schema17_incomplete",
            )


def _table_counts(conn: Any) -> dict[str, int]:
    return {
        schema.DATAFLOW_TABLE: int(conn.execute(f"SELECT count(*) FROM {schema.DATAFLOW_TABLE}").fetchone()[0]),
        schema.JOB_TABLE: int(conn.execute(f"SELECT count(*) FROM {schema.JOB_TABLE}").fetchone()[0]),
    }


def _source_ids(conn: Any) -> tuple[int, ...]:
    if not schema.table_exists(conn, schema.CACHE_SOURCES_TABLE):
        return ()
    return tuple(
        sorted(int(value[0]) for value in conn.execute(
            f"SELECT source_id FROM {schema.CACHE_SOURCES_TABLE} WHERE source_id IS NOT NULL ORDER BY source_id"
        ).fetchall())
    )


def _null_counts(conn: Any, table_name: str) -> dict[str, int]:
    """Capture per-column NULL counts without materialising cache rows."""

    columns = schema.table_columns(conn, table_name)
    if not columns:
        return {}
    expressions = ", ".join(
        f"SUM(CASE WHEN {quote_identifier(column)} IS NULL THEN 1 ELSE 0 END)"
        for column in columns
    )
    values = conn.execute(
        f"SELECT {expressions} FROM {quote_identifier(table_name)}"
    ).fetchone()
    return {
        column: int(value or 0)
        for column, value in zip(columns, values or ())
    }


def snapshot_cache(conn: Any) -> MigrationSnapshot:
    """Capture row/source counts used by candidate validation."""

    counts = _table_counts(conn)
    return MigrationSnapshot(
        counts[schema.DATAFLOW_TABLE],
        counts[schema.JOB_TABLE],
        _source_ids(conn),
        _null_counts(conn, schema.DATAFLOW_TABLE),
        _null_counts(conn, schema.JOB_TABLE),
    )


def _is_numeric(dtype: str) -> bool:
    return any(token in _normalise_type(dtype) for token in ("BIGINT", "HUGEINT", "BIGNUM", "DOUBLE", "FLOAT", "DECIMAL", "UBIGINT"))


def _validate_numeric_cast(conn: Any, table: str, column: str, old_type: str, target_type: str) -> None:
    old = _normalise_type(old_type)
    target = _normalise_type(target_type)
    reference = quote_identifier(column)
    if target in {"HUGEINT", "BIGINT", "UBIGINT", "UHUGEINT"}:
        # DuckDB's CAST from DOUBLE rounds fractional values.  Reject those
        # rows explicitly; no migration may silently change a stored integer.
        if any(token in old for token in ("DOUBLE", "FLOAT", "DECIMAL")):
            fractional = conn.execute(
                f"SELECT count(*) FROM {table} WHERE {reference} IS NOT NULL "
                f"AND (NOT isfinite(CAST({reference} AS DOUBLE)) OR "
                f"CAST({reference} AS DOUBLE) != trunc(CAST({reference} AS DOUBLE)))"
            ).fetchone()[0]
            if int(fractional or 0):
                raise SchemaMigrationError(
                    f"Cannot cast fractional/non-finite {table}.{column} to {target_type}",
                    reason_code="numeric_overflow",
                )
        cast_input = (
            f"CAST({reference} AS VARCHAR)"
            if "BIGNUM" in old
            else reference
        )
        try:
            failures = conn.execute(
                f"SELECT count(*) FROM {table} WHERE {reference} IS NOT NULL "
                f"AND TRY_CAST({cast_input} AS {target_type}) IS NULL"
            ).fetchone()[0]
        except duckdb.Error as exc:
            raise SchemaMigrationError(
                f"{table}.{column} contains values outside {target_type}",
                reason_code="numeric_overflow",
            ) from exc
        if int(failures or 0):
            raise SchemaMigrationError(
                f"{table}.{column} contains values outside {target_type}",
                reason_code="numeric_overflow",
            )
    elif target in {"DOUBLE", "FLOAT", "REAL"} and (
        _is_numeric(old_type) or old == "JSON"
    ):
        try:
            failures = conn.execute(
                f"SELECT count(*) FROM {table} WHERE {reference} IS NOT NULL "
                f"AND ((TRY_CAST({reference} AS DOUBLE) IS NULL "
                f"AND COALESCE(json_type({reference}), 'NULL') <> 'NULL') "
                f"OR NOT isfinite(TRY_CAST({reference} AS DOUBLE)))"
            ).fetchone()[0]
        except duckdb.Error as exc:
            raise SchemaMigrationError(
                f"{table}.{column} contains values outside {target_type}",
                reason_code="numeric_overflow",
            ) from exc
        if int(failures or 0):
            raise SchemaMigrationError(
                f"{table}.{column} contains non-finite values for {target_type}",
                reason_code="numeric_overflow",
            )


def _replacement_table_name(table_name: str) -> str:
    return f"__analytics_migration_new_{table_name}"


def _finite_leaf_queries(
    table_name: str,
    expression: str,
    data_type: str,
    from_sql: str,
    path: str,
    *,
    depth: int = 0,
) -> list[tuple[str, str]]:
    """Build finite-value checks for DOUBLE leaves in STRUCT/LIST values."""

    if depth > 64:
        return []
    normalised = _normalise_type(data_type)
    if normalised.endswith("[]"):
        item_type = normalised[:-2].strip()
        alias = quote_identifier(f"__analytics_item_{depth}_{len(path)}")
        item_expression = f"{alias}.item"
        return _finite_leaf_queries(
            table_name,
            item_expression,
            item_type,
            f"{from_sql} CROSS JOIN UNNEST({expression}) AS {alias}(item)",
            f"{path}[]",
            depth=depth + 1,
        )
    if normalised.startswith("STRUCT("):
        fields = _struct_fields(normalised) or {}
        queries: list[tuple[str, str]] = []
        for field_name, field_type in fields.items():
            field_expression = f"({expression}).{quote_identifier(field_name)}"
            queries.extend(
                _finite_leaf_queries(
                    table_name,
                    field_expression,
                    field_type,
                    from_sql,
                    f"{path}.{field_name}",
                    depth=depth + 1,
                )
            )
        return queries
    if normalised not in {"DOUBLE", "FLOAT", "REAL"}:
        return []
    return [
        (
            f"SELECT count(*) FROM {from_sql} "
            f"WHERE {expression} IS NOT NULL "
            f"AND NOT isfinite(CAST({expression} AS DOUBLE))",
            path,
        )
    ]


def _validate_target_finite_values(
    conn: Any,
    table_name: str,
    target_types: Mapping[str, str],
) -> None:
    for column, data_type in target_types.items():
        expression = f"t.{quote_identifier(column)}"
        for query, path in _finite_leaf_queries(
            table_name,
            expression,
            data_type,
            f"{quote_identifier(table_name)} AS t",
            column,
        ):
            try:
                failures = conn.execute(query).fetchone()[0]
            except duckdb.Error as exc:
                raise SchemaMigrationError(
                    f"Cannot validate numeric values in {table_name}.{path}",
                    reason_code="numeric_overflow",
                ) from exc
            if int(failures or 0):
                raise SchemaMigrationError(
                    f"{table_name}.{path} contains non-finite values for DOUBLE",
                    reason_code="numeric_overflow",
                )


def _null_semantics_preserved(old_type: str, target_type: str) -> bool:
    """Whether SQL NULL counts have the same meaning across a cast."""

    old = _normalise_type(old_type)
    target = _normalise_type(target_type)
    if old == target:
        return True
    if old == "TIMESTAMP WITH TIME ZONE" and target == "TIMESTAMPTZ":
        return True
    if old == "BIGNUM" and target in {"HUGEINT", "BIGINT", "UBIGINT", "UHUGEINT"}:
        return True
    # A nested STRUCT/LIST remains SQL NULL as a whole even when one of its
    # leaves changes representation. JSON scalar ``null`` is handled by the
    # target cast as SQL NULL, so its count is intentionally not compared.
    if old.startswith("STRUCT(") and target.startswith("STRUCT("):
        return True
    if old.endswith("[]") and target.endswith("[]"):
        return True
    return False


def _native_cast_expression(
    expression: str,
    old_type: str,
    target_type: str,
    *,
    counter: list[int],
    depth: int = 0,
) -> str:
    """Cast a legacy value leaf by leaf rather than DuckDB's nested BIGNUM cast.

    DuckDB can cast a root BIGNUM through decimal text, but direct casts of a
    STRUCT/LIST containing BIGNUM leaves may report a false ``BIGNUM too
    large`` error even for small values.  Rebuilding the native value with
    ``struct_pack``/``list_transform`` keeps the conversion lossless and
    preserves NULL parents, NULL elements, empty lists and list order.
    """

    if depth > 64:
        return f"CAST({expression} AS {target_type})"
    old = _normalise_type(old_type)
    target = _normalise_type(target_type)
    if old == target:
        return expression

    if old.endswith("[]") and target.endswith("[]"):
        old_item = old[:-2].strip()
        target_item = target[:-2].strip()
        counter[0] += 1
        variable = f"__analytics_item_{counter[0]}"
        item_expression = _native_cast_expression(
            variable,
            old_item,
            target_item,
            counter=counter,
            depth=depth + 1,
        )
        transformed = (
            f"list_transform({expression}, lambda {variable}: {item_expression})"
        )
        return (
            f"CASE WHEN {expression} IS NULL THEN NULL::{target_type} "
            f"ELSE CAST({transformed} AS {target_type}) END"
        )

    old_fields = _struct_fields(old)
    target_fields = _struct_fields(target)
    if old_fields is not None and target_fields is not None:
        values: list[str] = []
        for name, target_field_type in target_fields.items():
            target_reference = f"({expression}).{quote_identifier(name)}"
            old_field_type = old_fields.get(name)
            field_expression = (
                f"NULL::{target_field_type}"
                if old_field_type is None
                else _native_cast_expression(
                    target_reference,
                    old_field_type,
                    target_field_type,
                    counter=counter,
                    depth=depth + 1,
                )
            )
            values.append(f"{quote_identifier(name)} := {field_expression}")
        packed = f"struct_pack({', '.join(values)})"
        return (
            f"CASE WHEN {expression} IS NULL THEN NULL::{target_type} "
            f"ELSE CAST({packed} AS {target_type}) END"
        )

    # DuckDB's decimal-text route is required for arbitrary-width integers.
    # JSON numeric leaves use the normal native conversion; preflight and the
    # post-insert finite scan reject malformed/non-finite values.
    if "BIGNUM" in old and target in {"HUGEINT", "BIGINT", "UBIGINT", "UHUGEINT"}:
        return f"CAST(CAST({expression} AS VARCHAR) AS {target_type})"
    return f"CAST({expression} AS {target_type})"


def _create_replacement_table(conn: Any, table_name: str) -> None:
    old_types = schema.table_column_types(conn, table_name)
    target_types = schema.cache_table_column_types(
        schema.DATAFLOW_COLUMN_TYPES if table_name == schema.DATAFLOW_TABLE else schema.JOB_COLUMN_TYPES
    )
    replacement = _replacement_table_name(table_name)
    conn.execute(f"DROP TABLE IF EXISTS {quote_identifier(replacement)}")
    definitions = ", ".join(
        f"{quote_identifier(name)} {dtype}" for name, dtype in target_types.items()
    )
    conn.execute(f"CREATE TABLE {quote_identifier(replacement)} ({definitions})")
    expressions: list[str] = []
    cast_counter = [0]
    for name, target_type in target_types.items():
        reference = quote_identifier(name)
        if name not in old_types:
            expressions.append(f"NULL::{target_type}")
            continue
        old_type = old_types[name]
        _validate_numeric_cast(conn, table_name, name, old_type, target_type)
        old_normalised = _normalise_type(old_type)
        target_normalised = _normalise_type(target_type)
        if old_normalised == target_normalised:
            expressions.append(reference)
        else:
            expressions.append(
                _native_cast_expression(
                    reference,
                    old_type,
                    target_type,
                    counter=cast_counter,
                )
            )
    try:
        conn.execute(
            f"INSERT INTO {quote_identifier(replacement)} ({', '.join(quote_identifier(name) for name in target_types)}) "
            f"SELECT {', '.join(expressions)} FROM {quote_identifier(table_name)}"
        )
    except duckdb.Error as exc:
        raise SchemaMigrationError(
            f"Cannot convert {table_name} rows to the native schema",
            reason_code="numeric_overflow" if any(
                _normalise_type(old_types.get(name, ""))
                != _normalise_type(target_type)
                for name, target_type in target_types.items()
                if name in old_types
            ) else "schema17_incompatible",
        ) from exc
    _validate_target_finite_values(conn, replacement, target_types)
    old_count = int(conn.execute(f"SELECT count(*) FROM {quote_identifier(table_name)}").fetchone()[0])
    new_count = int(conn.execute(f"SELECT count(*) FROM {quote_identifier(replacement)}").fetchone()[0])
    if old_count != new_count:
        raise SchemaMigrationError(
            f"Row count changed while migrating {table_name}: {old_count} -> {new_count}",
            reason_code="row_count_changed",
        )
    conn.execute(f"DROP TABLE {quote_identifier(table_name)}")
    conn.execute(
        f"ALTER TABLE {quote_identifier(replacement)} RENAME TO {quote_identifier(table_name)}"
    )


def migrate_connection(conn: Any, *, source_version: int | None = LEGACY_SCHEMA_VERSION) -> MigrationSnapshot:
    """Rewrite a complete schema17/native candidate in one DuckDB transaction."""

    if source_version == LEGACY_SCHEMA_VERSION:
        validate_schema17_cache(conn)
    before = snapshot_cache(conn)
    before_types = {
        table_name: schema.table_column_types(conn, table_name)
        for table_name in (schema.DATAFLOW_TABLE, schema.JOB_TABLE)
    }
    try:
        conn.execute("BEGIN TRANSACTION")
        _create_replacement_table(conn, schema.DATAFLOW_TABLE)
        _create_replacement_table(conn, schema.JOB_TABLE)
        # The generated serving facts and filter values are dependencies of the
        # root tables.  Publication rebuilds them in the same candidate before
        # validation; source coverage and row counts remain untouched here.
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except duckdb.Error:
            pass
        raise
    after = snapshot_cache(conn)
    after_types = {
        table_name: schema.table_column_types(conn, table_name)
        for table_name in (schema.DATAFLOW_TABLE, schema.JOB_TABLE)
    }
    if (
        before.dataflow_rows != after.dataflow_rows
        or before.job_rows != after.job_rows
        or before.source_ids != after.source_ids
        or any(
            after.dataflow_nulls.get(column) != null_count
            for column, null_count in before.dataflow_nulls.items()
            if (
                column in after.dataflow_nulls
                and _null_semantics_preserved(
                    before_types[schema.DATAFLOW_TABLE].get(column, ""),
                    after_types[schema.DATAFLOW_TABLE].get(column, ""),
                )
            )
        )
        or any(
            after.job_nulls.get(column) != null_count
            for column, null_count in before.job_nulls.items()
            if (
                column in after.job_nulls
                and _null_semantics_preserved(
                    before_types[schema.JOB_TABLE].get(column, ""),
                    after_types[schema.JOB_TABLE].get(column, ""),
                )
            )
        )
    ):
        raise SchemaMigrationError(
            "Schema migration changed cached coverage or row counts",
            reason_code="coverage_changed",
        )
    return before


def copy_live_candidate(live_path: Path, candidate_path: Path) -> None:
    """Create a checkpointed candidate copy without reading source storage."""

    if not live_path.exists():
        raise SchemaMigrationError("Live analytics cache is missing", reason_code="cache_unavailable")
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    # The candidate is an agent-owned path.  Remove both the database and a
    # stale WAL before copying so a failed prior attempt cannot be replayed into
    # the new candidate by DuckDB on its next open.
    with analytics_connections.exclusive_maintenance():
        for stale_path in (candidate_path, Path(f"{candidate_path}.wal")):
            if stale_path.exists():
                stale_path.unlink()
        conn = duckdb.connect(str(live_path))
        try:
            conn.execute("CHECKPOINT")
        finally:
            conn.close()
        try:
            required_bytes = max(1 << 20, live_path.stat().st_size)
            free_bytes = shutil.disk_usage(candidate_path.parent).free
        except OSError as exc:
            raise SchemaMigrationError(
                "Cannot inspect analytics cache disk capacity",
                reason_code="cache_copy_failed",
            ) from exc
        if free_bytes < required_bytes:
            raise SchemaMigrationError(
                "Insufficient disk space for the analytics migration candidate",
                reason_code="insufficient_disk_space",
            )
        shutil.copyfile(live_path, candidate_path)


def schema17_migration_reason(conn: Any) -> str | None:
    """Return a precise reason when a metadata-version17 cache is migratable."""

    try:
        meta_rows = conn.execute(
            f"SELECT schema_version FROM {schema.ANALYTICS_META_TABLE} WHERE singleton_id = 1"
        ).fetchall()
    except duckdb.Error:
        return "cache_unavailable"
    if not meta_rows or meta_rows[0][0] is None or int(meta_rows[0][0]) != LEGACY_SCHEMA_VERSION:
        return None
    try:
        validate_schema17_cache(conn)
    except SchemaMigrationError as exc:
        return exc.reason_code
    return "schema17_native_rewrite"


def migrate_database_copy(
    live_path: Path,
    candidate_path: Path,
    *,
    source_version: int | None = LEGACY_SCHEMA_VERSION,
) -> MigrationSnapshot:
    """Checkpoint, copy and migrate a live cache into ``candidate_path``."""

    copy_live_candidate(live_path, candidate_path)
    conn = duckdb.connect(str(candidate_path))
    try:
        return migrate_connection(conn, source_version=source_version)
    finally:
        conn.close()


__all__ = [
    "LEGACY_SCHEMA_VERSION",
    "MappingDiff",
    "MigrationKind",
    "MigrationSnapshot",
    "SchemaMigrationError",
    "copy_live_candidate",
    "contract_fingerprint",
    "diff_mapping_documents",
    "migrate_connection",
    "migrate_database_copy",
    "schema17_migration_reason",
    "snapshot_cache",
    "validate_schema17_cache",
]

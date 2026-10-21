"""Validation and canonicalization for SQL files staged in an editor draft."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from datacoolie_studio.domains.sql.reader import (
    SqlReferenceError,
    normalize_sql_relative_path,
)


class SqlOperationError(ValueError):
    code = "sql_operation_invalid"


def normalize_pending_sql_files(value: object) -> list[dict[str, Any]]:
    """Return safe, stable SQL file operations from an editor payload.

    A query reference is deliberately kept as the authored value.  The source
    configuration, rather than a client-supplied absolute URI, determines the
    actual storage target when the operation is committed.
    """

    if value is None:
        return []
    if not isinstance(value, list):
        raise SqlOperationError("sql_files must be a list")

    result: list[dict[str, Any]] = []
    seen: dict[tuple[int, str, str], tuple[str, str]] = {}
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise SqlOperationError(f"sql_files[{index}] must be an object")
        source_id = _positive_int(item.get("source_id"), f"sql_files[{index}].source_id")
        query = item.get("query", item.get("declared_query"))
        if not isinstance(query, str) or not query.strip():
            raise SqlOperationError(f"sql_files[{index}].query must be a non-empty string")
        query = query.strip()
        relative_path, base_kind = parse_sql_file_reference(query)
        content = item.get("content")
        if not isinstance(content, str):
            raise SqlOperationError(f"sql_files[{index}].content must be text")

        base_revision = item.get("base_revision", item.get("revision"))
        if base_revision is not None and not isinstance(base_revision, dict):
            raise SqlOperationError(
                f"sql_files[{index}].base_revision must be an object or null"
            )
        base_uri = item.get("base_uri")
        if base_uri is not None and (
            not isinstance(base_uri, str) or not base_uri.strip()
        ):
            raise SqlOperationError(
                f"sql_files[{index}].base_uri must be a non-empty string or null"
            )
        config_revision = item.get("config_revision")
        if config_revision is not None and not isinstance(config_revision, dict):
            raise SqlOperationError(
                f"sql_files[{index}].config_revision must be an object or null"
            )
        publish_status = item.get("publish_status")
        if publish_status is not None and publish_status not in {"pending", "applied"}:
            raise SqlOperationError(
                f"sql_files[{index}].publish_status must be 'pending' or 'applied'"
            )
        published_revision = item.get("published_revision")
        if published_revision is not None and not isinstance(published_revision, dict):
            raise SqlOperationError(
                f"sql_files[{index}].published_revision must be an object or null"
            )
        published_content_hash = item.get("published_content_hash")
        if published_content_hash is not None and (
            not isinstance(published_content_hash, str)
            or not published_content_hash.strip()
        ):
            raise SqlOperationError(
                f"sql_files[{index}].published_content_hash must be a non-empty string or null"
            )
        dedup_key = (source_id, base_kind, relative_path)
        previous = seen.get(dedup_key)
        if previous is not None and previous[1] != content:
            raise SqlOperationError(
                f"sql_files[{index}] conflicts with another operation for the same SQL file: {query!r}"
            )
        seen[dedup_key] = (query, content)
        result.append(
            {
                "source_id": source_id,
                "query": query,
                "content": content,
                "base_revision": deepcopy(base_revision),
                "base_kind": base_kind,
                "relative_path": relative_path,
                "base_uri": base_uri.strip() if isinstance(base_uri, str) else None,
                "config_revision": deepcopy(config_revision),
                "publish_status": publish_status or "pending",
                "published_revision": deepcopy(published_revision),
                "published_content_hash": (
                    published_content_hash.strip()
                    if isinstance(published_content_hash, str)
                    else None
                ),
            }
        )

    # Keep the payload deterministic when a UI submits the same operation more
    # than once after a retry.
    deduplicated: dict[tuple[int, str, str], dict[str, Any]] = {}
    for item in result:
        deduplicated[(item["source_id"], item["base_kind"], item["relative_path"])] = item
    return list(deduplicated.values())


def parse_sql_file_reference(value: str) -> tuple[str, str]:
    raw = value.strip().replace("\\", "/")
    if raw.startswith("artifact:"):
        if not raw.startswith("artifact:/") or raw.startswith("artifact://"):
            raise SqlOperationError(
                "artifact SQL references must use artifact:/<relative-path>"
            )
        relative = raw[len("artifact:/") :]
        base_kind = "project_root"
    else:
        relative = raw
        base_kind = "sql_root"
    try:
        normalized = normalize_sql_relative_path(relative)
    except SqlReferenceError as exc:
        raise SqlOperationError(str(exc)) from exc
    if base_kind == "sql_root" and not normalized.lower().endswith(".sql"):
        raise SqlOperationError(f"SQL file path must end with .sql: {value!r}")
    return normalized, base_kind


def pending_sql_file_issues(
    document: dict[str, Any],
) -> list[tuple[int, str]]:
    """Return ``(row_index, message)`` validation errors for staged files."""

    value = document.get("sql_files")
    if value in (None, []):
        return []
    try:
        operations = normalize_pending_sql_files(value)
    except SqlOperationError as exc:
        return [(0, str(exc))]

    rows = ((document.get("sheets") or {}).get("dataflows") or {}).get("rows") or []
    references: set[tuple[int, str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_id = _positive_int_or_none(row.get("__metadata_source_id"))
        query = row.get("source_query")
        if source_id is None or not isinstance(query, str) or not query.strip():
            continue
        try:
            relative_path, base_kind = parse_sql_file_reference(query.strip())
        except SqlOperationError:
            # Inline SQL is not a file operation and therefore has no pending
            # file target to match.
            continue
        references.add((source_id, base_kind, relative_path))
    issues: list[tuple[int, str]] = []
    for index, operation in enumerate(operations):
        key = (
            operation["source_id"],
            operation["base_kind"],
            operation["relative_path"],
        )
        if key not in references:
            issues.append(
                (
                    index,
                    f"SQL file operation is not referenced by a dataflow: {operation['query']}",
                )
            )
    return issues


def _positive_int(value: object, label: str) -> int:
    result = _positive_int_or_none(value)
    if result is None:
        raise SqlOperationError(f"{label} must be a positive integer")
    return result


def _positive_int_or_none(value: object) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None

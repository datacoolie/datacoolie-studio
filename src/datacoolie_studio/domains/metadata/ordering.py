from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit


REQUIRED_EDITOR_SHEETS = ("connections", "dataflows", "schema_hints")
_STAGE_FAMILIES = ("source", "bronze", "silver", "gold")
_NATURAL_PARTS = re.compile(r"(\d+)")


def canonicalize_editor_document(document: dict[str, Any]) -> dict[str, Any]:
    """Return an editor document with the approved environment display order.

    The function deliberately does not add or remove row fields. Runtime routing
    fields remain available to the environment editor, while serializers can
    continue to strip them at the source boundary.
    """

    canonical = deepcopy(document)
    sheets = canonical.get("sheets") or {}
    for sheet_name in REQUIRED_EDITOR_SHEETS:
        sheet = sheets.get(sheet_name)
        if not isinstance(sheet, dict):
            continue
        rows = sheet.get("rows")
        if not isinstance(rows, list):
            continue
        sheet["rows"] = canonicalize_rows(sheet_name, rows)
    return canonical


def canonicalize_rows(sheet_name: str, rows: list[Any]) -> list[Any]:
    """Sort rows while preserving raw order for equal canonical keys."""

    if sheet_name not in REQUIRED_EDITOR_SHEETS:
        return list(rows)

    return [
        row
        for _, row in sorted(
            enumerate(rows),
            key=lambda indexed: _row_sort_key(sheet_name, indexed[1]),
        )
    ]


def same_canonical_document(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Compare documents after removing order-only differences."""

    return canonicalize_editor_document(left) == canonicalize_editor_document(right)


def stage_family_rank(value: Any) -> int:
    normalized = normalize_sort_text(value)
    for rank, prefix in enumerate(_STAGE_FAMILIES):
        if normalized.startswith(prefix):
            return rank
    return len(_STAGE_FAMILIES)


def normalize_sort_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().casefold()


def natural_sort_key(value: Any) -> tuple[tuple[int, int | str], ...]:
    normalized = normalize_sort_text(value)
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in _NATURAL_PARTS.split(normalized)
        if part
    )


def source_filename(value: Any) -> str:
    """Return a normalized filename/stem for a local path or storage URI."""

    raw = normalize_sort_text(value)
    if not raw:
        return ""
    raw = unquote(raw.split("#", 1)[0].split("?", 1)[0]).rstrip("/")
    path = urlsplit(raw).path or raw
    basename = PurePosixPath(path.replace("\\", "/")).name or raw
    suffix = PurePosixPath(basename).suffix
    return basename[: -len(suffix)] if suffix else basename


def metadata_source_sort_key(row: Any) -> tuple[Any, ...]:
    if not isinstance(row, dict):
        return (len(_STAGE_FAMILIES), True, (), ())

    uri = row.get("__metadata_source_uri")
    name = row.get("__metadata_source_name")
    source_file = source_filename(uri or name)
    source_identity = row.get("__metadata_source_id") or uri or name or ""
    return (
        stage_family_rank(source_file),
        not bool(source_file),
        natural_sort_key(source_file),
        natural_sort_key(source_identity),
    )


def _row_sort_key(sheet_name: str, row: Any) -> tuple[Any, ...]:
    source_key = metadata_source_sort_key(row)
    if not isinstance(row, dict):
        return source_key

    if sheet_name == "connections":
        return source_key
    if sheet_name == "dataflows":
        return source_key + (
            *_stage_sort_key(row.get("stage")),
        )
    return source_key + (
        _text_key(row.get("connection_name")),
        _text_key(row.get("schema_name")),
        _text_key(row.get("table_name")),
        _ordinal_key(row.get("ordinal_position")),
    )


def _text_key(value: Any) -> tuple[Any, ...]:
    normalized = normalize_sort_text(value)
    return (not bool(normalized), natural_sort_key(normalized))


def _stage_sort_key(value: Any) -> tuple[Any, ...]:
    normalized = normalize_sort_text(value)
    return (stage_family_rank(normalized), not bool(normalized), natural_sort_key(normalized))


def _ordinal_key(value: Any) -> tuple[int, Decimal]:
    if value is None or isinstance(value, bool):
        return (1, Decimal(0))
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return (1, Decimal(0))
    if not parsed.is_finite():
        return (1, Decimal(0))
    return (0, parsed)

"""Pure editor column ordering and schema composition."""

from __future__ import annotations

from typing import Any

from datacoolie_studio.domains.metadata.document_schema import (
    CONNECTION_COLUMNS,
    DATAFLOW_COLUMNS,
    SCHEMA_HINT_COLUMNS,
    VISIBLE_STUDIO_ROUTING_COLUMN,
)


def ordered_columns(
    sheet_name: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, str]]:
    preferred = {
        "connections": CONNECTION_COLUMNS,
        "dataflows": DATAFLOW_COLUMNS,
        "schema_hints": SCHEMA_HINT_COLUMNS,
    }[sheet_name]
    seen = {key for row in rows for key in row}
    if sheet_name == "dataflows":
        keys = ordered_dataflow_columns(preferred, seen)
    else:
        keys = list(preferred)
        keys.extend(sorted(key for key in seen - set(keys) if not key.startswith("__")))
    if VISIBLE_STUDIO_ROUTING_COLUMN not in keys:
        keys.append(VISIBLE_STUDIO_ROUTING_COLUMN)
    return [{"key": key, "name": column_display_name(key)} for key in keys]


def canonical_editor_columns(
    sheet_name: str,
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing = {
        str(column["key"]): dict(column)
        for column in columns
        if isinstance(column, dict) and column.get("key")
    }
    key_sentinel = {key: None for key in existing}
    ordered = ordered_columns(sheet_name, [*rows, key_sentinel])
    return [
        {**column, **existing.get(column["key"], {}), "key": column["key"]}
        for column in ordered
    ]


def ordered_dataflow_columns(preferred: list[str], seen: set[str]) -> list[str]:
    keys: list[str] = []
    known = set(preferred)
    source_extra = sorted(key for key in seen - known if key.startswith("source_"))
    transform_extra = sorted(
        key for key in seen - known if key.startswith("transform_")
    )
    destination_extra = sorted(
        key for key in seen - known if key.startswith("destination_")
    )
    for key in preferred:
        keys.append(key)
        if key == "source_configure":
            keys.extend(source_extra)
        elif key == "transform_configure":
            keys.extend(transform_extra)
        elif key == "destination_configure":
            keys.extend(destination_extra)
    keys.extend(sorted(key for key in seen - set(keys) if not key.startswith("__")))
    if VISIBLE_STUDIO_ROUTING_COLUMN in seen and VISIBLE_STUDIO_ROUTING_COLUMN not in keys:
        keys.append(VISIBLE_STUDIO_ROUTING_COLUMN)
    return keys


def column_display_name(key: str) -> str:
    if key == VISIBLE_STUDIO_ROUTING_COLUMN:
        return "metadata_source"
    return key

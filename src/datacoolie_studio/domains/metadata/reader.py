from __future__ import annotations

import json
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

import yaml

from datacoolie_studio.domains.storage.uri import StorageProviderNotEnabled, require_local_path


class MetadataReadError(RuntimeError):
    pass


MetadataFragmentShape = Literal["document", "list", "record"]


@dataclass(frozen=True)
class MetadataDocumentRead:
    """Decoded metadata plus source-shape information needed for round trips."""

    payload: dict[str, Any]
    wrapper: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)
    envelope_extras: dict[str, Any] = field(default_factory=dict)
    fragment_section: str | None = None
    fragment_shape: MetadataFragmentShape = "document"


def read_metadata_file(
    uri: str,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    return read_metadata_document_file(uri, strict=strict).payload


def read_metadata_document_file(
    uri: str,
    *,
    strict: bool = False,
) -> MetadataDocumentRead:
    try:
        path = require_local_path(uri)
    except StorageProviderNotEnabled as exc:
        raise MetadataReadError(str(exc)) from exc
    if not path.exists():
        raise MetadataReadError(f"Metadata file not found: {uri}")
    if path.is_symlink():
        raise MetadataReadError(f"Metadata file must not be a symlink: {uri}")
    try:
        return read_metadata_document_bytes(uri, path.read_bytes(), strict=strict)
    except MetadataReadError:
        raise
    except Exception as exc:
        raise MetadataReadError(f"Cannot parse metadata file: {uri}") from exc


def read_metadata_bytes(
    uri: str,
    content: bytes,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    return read_metadata_document_bytes(uri, content, strict=strict).payload


def read_metadata_document_bytes(
    uri: str,
    content: bytes,
    *,
    strict: bool = False,
) -> MetadataDocumentRead:
    suffix = Path(uri.split("?", 1)[0]).suffix.lower()
    try:
        if suffix in {".yaml", ".yml"}:
            data = yaml.safe_load(content.decode("utf-8")) or {}
        elif suffix == ".xlsx":
            data = _read_excel_bytes(content, uri)
        elif suffix == ".xls":
            data = _read_excel_bytes(content, uri)
        else:
            data = json.loads(content.decode("utf-8"))
    except MetadataReadError:
        raise
    except Exception as exc:
        raise MetadataReadError(f"Cannot parse metadata file: {uri}") from exc
    if isinstance(data, list):
        section = _metadata_fragment_section(uri)
        if section is None:
            raise MetadataReadError("Metadata root must be an object")
        return MetadataDocumentRead(
            {section: data},
            fragment_section=section,
            fragment_shape="list",
        )
    if not isinstance(data, dict):
        raise MetadataReadError("Metadata root must be an object")

    wrapper: str | None = None
    envelope_extras: dict[str, Any] = {}
    if "metadata" in data:
        envelope_extras = {key: value for key, value in data.items() if key != "metadata"}
        wrapper = "metadata"
        data = data["metadata"]
        if not isinstance(data, dict):
            raise MetadataReadError("Metadata wrapper must contain a mapping")

    known_sections = {"connections", "dataflows", "schema_hints"}
    present_sections = known_sections.intersection(data)
    # Canonical modular projects allow a fragment to contain one dataflow
    # object instead of wrapping it in a ``dataflows`` list.  Preserve the
    # existing full-document contract while normalizing that fragment shape
    # at the storage boundary.
    if not wrapper and not present_sections:
        section = _metadata_fragment_section(uri)
        if section is not None:
            return MetadataDocumentRead(
                {section: [data]},
                fragment_section=section,
                fragment_shape="record",
            )
    extras = {
        key: value
        for key, value in data.items()
        if key not in known_sections
    }
    return MetadataDocumentRead(data, wrapper=wrapper, extras=extras, envelope_extras=envelope_extras)


def _metadata_fragment_section(uri: str) -> str | None:
    """Infer a section for list/single-record modular metadata fragments."""
    normalized = uri.split("?", 1)[0].replace("\\", "/").rstrip("/")
    parts = [part.lower() for part in normalized.split("/") if part]
    filename = parts[-1] if parts else ""
    if "dataflows" in parts or filename == "dataflows.json":
        return "dataflows"
    if filename == "connections.json":
        return "connections"
    if filename in {"schema_hints.json", "schema-hints.json"}:
        return "schema_hints"
    return None








def _read_excel_bytes(content: bytes, uri: str) -> dict[str, Any]:
    import openpyxl

    workbook = openpyxl.load_workbook(
        BytesIO(content), read_only=True, data_only=True
    )
    try:
        _assert_supported_excel_workbook(workbook, uri)
        return {
            "connections": _sheet_rows(workbook, "connections"),
            "dataflows": _excel_dataflows(workbook),
            "schema_hints": _excel_schema_hints(workbook),
        }
    finally:
        workbook.close()


def _assert_supported_excel_workbook(workbook: Any, uri: str) -> None:
    supported = {"connections", "dataflows", "schema_hints"}
    if not supported.intersection(workbook.sheetnames):
        raise MetadataReadError(
            "Excel metadata file contains none of the supported sheets: "
            f"{uri}"
        )


def _sheet_rows(workbook: Any, sheet_name: str) -> list[dict[str, Any]]:
    if sheet_name not in workbook.sheetnames:
        return []
    sheet = workbook[sheet_name]
    header_row = next(sheet.iter_rows(min_row=1, max_row=1), None)
    if header_row is None:
        return []
    headers = [str(cell.value).strip() if cell.value is not None else "" for cell in header_row]
    rows: list[dict[str, Any]] = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if all(value is None for value in row):
            continue
        item = {headers[index]: _cast_cell(value) for index, value in enumerate(row) if index < len(headers) and headers[index]}
        rows.append({key: value for key, value in item.items() if value is not None})
    return rows


def _cast_cell(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        if value.startswith(("{", "[")):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        if "," in value:
            return [part.strip() for part in value.split(",") if part.strip()]
    return value


def _excel_dataflows(workbook: Any) -> list[dict[str, Any]]:
    dataflows = []
    for row in _sheet_rows(workbook, "dataflows"):
        df: dict[str, Any] = {}
        source: dict[str, Any] = {}
        destination: dict[str, Any] = {}
        transform: dict[str, Any] = {}
        for key, value in row.items():
            if key.startswith("source_"):
                source[key.removeprefix("source_")] = value
            elif key.startswith("destination_"):
                destination[key.removeprefix("destination_")] = value
            elif key.startswith("transform_"):
                transform[key.removeprefix("transform_")] = value
            else:
                df[key] = value
        if source:
            df["source"] = source
        if destination:
            df["destination"] = destination
        if transform:
            df["transform"] = transform
        if df:
            dataflows.append(df)
    return dataflows


def _excel_schema_hints(workbook: Any) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    for row in _sheet_rows(workbook, "schema_hints"):
        connection_name = row.get("connection_name")
        connection_id = row.get("connection_id")
        conn = connection_name or connection_id
        table = row.get("table_name")
        schema = row.get("schema_name")
        if not conn or not table:
            continue
        reference_key = (
            f"id:{connection_id}"
            if connection_id
            else f"name:{connection_name}"
        )
        key = (reference_key, str(table), str(schema) if schema else None)
        group = grouped.setdefault(
            key,
            {
                "table_name": table,
                "hints": [],
                **({"connection_name": connection_name} if connection_name else {}),
                **({"connection_id": connection_id} if connection_id else {}),
            },
        )
        if connection_name:
            group.setdefault("connection_name", connection_name)
        if connection_id:
            group.setdefault("connection_id", connection_id)
        if schema:
            group["schema_name"] = schema
        hint = {
            k: v
            for k, v in row.items()
            if k not in {"connection_name", "connection_id", "table_name", "schema_name"}
        }
        if hint:
            group["hints"].append(hint)
    return list(grouped.values())

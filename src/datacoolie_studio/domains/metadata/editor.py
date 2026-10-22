from __future__ import annotations

from pathlib import Path
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.metadata.reader import (
    MetadataDocumentRead,
    MetadataReadError,
    read_metadata_document_file,
)
from datacoolie_studio.domains.metadata.document_columns import (
    ordered_columns as _ordered_columns,
)
from datacoolie_studio.domains.metadata.document_normalization import (
    metadata_document_state as _metadata_document_state,
    metadata_payload as _metadata_payload,
    raw_metadata_to_sheets as _raw_metadata_to_sheets,
    stringify_complex_values as _stringify_complex_values,
)
from datacoolie_studio.domains.metadata.document_schema import (
    REQUIRED_SHEETS,
)
from datacoolie_studio.domains.metadata.document_lifecycle import (
    materialize_default_active_flags as _materialize_default_active_flags,
    materialize_generated_ids as _materialize_generated_ids,
)
from datacoolie_studio.domains.metadata.environment_documents import (
    apply_studio_routing_fields as _apply_studio_routing_fields,
)
from datacoolie_studio.domains.metadata.revisions import source_revision
from datacoolie_studio.domains.metadata.source_policy import (
    editor_source_config as _editor_source_config,
    metadata_format as _metadata_format,
    metadata_source_name as _metadata_source_name,
)



def load_editor_document_from_raw(
    source: EnvironmentSource,
    raw_metadata: dict[str, Any],
    revision: dict[str, Any],
    *,
    document_info: MetadataDocumentRead | None = None,
) -> dict[str, Any]:
    if source.source_kind != "metadata":
        raise MetadataReadError("Source is not a metadata source")
    original_metadata = raw_metadata
    if document_info is not None:
        raw_metadata = document_info.payload
    else:
        raw_metadata = _metadata_payload(raw_metadata)
    path = Path(source.uri).expanduser()
    raw_sheets = _stringify_complex_values(_raw_metadata_to_sheets(raw_metadata))
    _materialize_default_active_flags(raw_sheets)
    _materialize_generated_ids(raw_sheets)
    _apply_studio_routing_fields(raw_sheets, source)
    document = {
        "source": {
            "source_id": source.id,
            "environment_id": source.environment_id,
            "project_id": source.environment.project_id if source.environment else None,
            "uri": source.uri,
            "name": _metadata_source_name(source),
            "format": _metadata_format(path),
            "revision": revision,
            "record_counts": _metadata_sheet_counts(raw_sheets),
            **_editor_source_config(source),
        },
        "sheets": {
            name: {
                "columns": _ordered_columns(name, raw_sheets.get(name, [])),
                "rows": raw_sheets.get(name, []),
            }
            for name in REQUIRED_SHEETS
        },
        "metadata_document": _metadata_document_state(
            document_info,
            original_metadata,
        ),
    }
    document["issues"] = []
    return document


def load_editor_document_from_path(
    path: Path,
    *,
    source_id: int,
    environment_id: int,
    project_id: int | None,
    source_uri: str,
    backup_id: int | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    revision = source_revision(str(path))
    document_info = read_metadata_document_file(str(path), strict=strict)
    raw_sheets = _stringify_complex_values(_raw_metadata_to_sheets(document_info.payload))
    _materialize_default_active_flags(raw_sheets)
    _materialize_generated_ids(raw_sheets)
    _apply_studio_routing_fields(
        raw_sheets,
        None,
        source_id=source_id,
        source_uri=source_uri,
        source_name=Path(source_uri).name or source_uri,
    )
    document = {
        "source": {
            "source_id": source_id,
            "environment_id": environment_id,
            "project_id": project_id,
            "uri": source_uri,
            "name": Path(source_uri).name or source_uri,
            "format": _metadata_format(path),
            "revision": revision,
            **({"backup_id": backup_id, "backup_path": str(path)} if backup_id is not None else {}),
        },
        "sheets": {
            name: {
                "columns": _ordered_columns(name, raw_sheets.get(name, [])),
                "rows": raw_sheets.get(name, []),
            }
            for name in REQUIRED_SHEETS
        },
        "metadata_document": _metadata_document_state(
            document_info,
            document_info.payload,
        ),
    }
    document["issues"] = []
    return document


def _metadata_sheet_counts(sheets: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {name: len(sheets.get(name, [])) for name in REQUIRED_SHEETS}

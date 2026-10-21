from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from datacoolie_studio.domains.storage.capabilities import WriteSafety


class MetadataResponse(BaseModel):
    summary: dict[str, Any]
    sources: list[dict[str, Any]]
    connections: list[dict[str, Any]]
    dataflows: list[dict[str, Any]]
    schema_hints: list[dict[str, Any]]
    errors: list[dict[str, Any]]


class MetadataEditorDocumentResponse(BaseModel):
    source: dict[str, Any]
    sheets: dict[str, Any]
    issues: list[dict[str, Any]]
    metadata_document: dict[str, Any] = Field(default_factory=dict)
    metadata_documents: dict[str, dict[str, Any]] = Field(default_factory=dict)
    sql_files: list[dict[str, Any]] = Field(default_factory=list)


class MetadataEditorWorkspaceResponse(BaseModel):
    schema_version: Literal["metadata-editor-workspace.v1"]
    environment_id: int
    metadata_catalog_version: str
    document: MetadataEditorDocumentResponse
    draft: MetadataEditorDocumentResponse | None = None


class MetadataEditorPayloadRequest(BaseModel):
    source: dict[str, Any] | None = None
    sheets: dict[str, Any]
    issues: list[dict[str, Any]] = Field(default_factory=list)
    metadata_document: dict[str, Any] = Field(default_factory=dict)
    metadata_documents: dict[str, dict[str, Any]] = Field(default_factory=dict)
    sql_files: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("sheets")
    @classmethod
    def require_editor_sheet_shape(cls, sheets: dict[str, Any]) -> dict[str, Any]:
        for name in ("connections", "dataflows", "schema_hints"):
            sheet = sheets.get(name)
            if not isinstance(sheet, dict) or not isinstance(sheet.get("rows"), list):
                raise ValueError(f"Editor sheet {name!r} must contain a rows list")
            columns = sheet.get("columns", [])
            if not isinstance(columns, list) or any(
                not isinstance(column, dict) or not isinstance(column.get("key"), str)
                for column in columns
            ):
                raise ValueError(f"Editor sheet {name!r} columns must be objects with string keys")
            if any(not isinstance(row, dict) for row in sheet["rows"]):
                raise ValueError(f"Editor sheet {name!r} rows must be objects")
        return sheets



class MetadataBackupResponse(BaseModel):
    id: int
    project_id: int
    environment_id: int
    source_id: int
    source_uri: str
    backup_path: str | None = None
    backup_ref: str | None = None
    backup_hash: str | None = None
    backup_size: int | None = None
    source_revision: dict[str, Any] | None = None
    saved_revision: dict[str, Any] | None = None
    created_at: datetime


class MetadataBackupRestoreRequest(BaseModel):
    expected_revision: dict[str, Any]
    confirm_restore: bool = False


class MetadataWritePrepareRequest(BaseModel):
    expected_revision: dict[str, Any]
    editor_document: MetadataEditorPayloadRequest


class MetadataWriteApplyRequest(BaseModel):
    confirm_apply: bool = False


class MetadataWriteAbandonRequest(BaseModel):
    confirm_abandon: bool = False


class MetadataWriteItemResponse(BaseModel):
    id: int
    order: int
    kind: str
    state: str
    source_id: int | None = None
    source_name: str | None = None
    source_uri: str
    target_uri: str
    action: str
    label: str
    sheets: list[str] = Field(default_factory=list)
    provider: str | None = None
    write_safety: WriteSafety | None = None
    error: dict[str, Any] | None = None


class MetadataWriteOperationResponse(BaseModel):
    operation_key: str
    environment_id: int
    state: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    expires_at: datetime
    error: dict[str, Any] | None = None
    summary: dict[str, int]
    items: list[MetadataWriteItemResponse]

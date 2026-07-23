from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

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


class MetadataEditorWorkspaceResponse(BaseModel):
    schema_version: Literal["metadata-editor-workspace.v1"]
    environment_id: int
    metadata_catalog_version: str
    document: MetadataEditorDocumentResponse
    draft: MetadataEditorDocumentResponse | None = None


class MetadataEditorValidationRequest(BaseModel):
    source: dict[str, Any] | None = None
    sheets: dict[str, Any]
    issues: list[dict[str, Any]] = []


class MetadataEditorValidationResponse(BaseModel):
    status: str
    summary: dict[str, Any]
    issues: list[dict[str, Any]]


class MetadataEditorSaveRequest(BaseModel):
    expected_revision: dict[str, Any]
    editor_document: MetadataEditorValidationRequest
    confirm_overwrite: bool = False


class MetadataBackupResponse(BaseModel):
    id: int
    project_id: int
    environment_id: int
    source_id: int
    source_uri: str
    backup_path: str
    source_revision: dict[str, Any] | None = None
    saved_revision: dict[str, Any] | None = None
    created_at: datetime


class MetadataBackupRestoreRequest(BaseModel):
    expected_revision: dict[str, Any]
    confirm_restore: bool = False

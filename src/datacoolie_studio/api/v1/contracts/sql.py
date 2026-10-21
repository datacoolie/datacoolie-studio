from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class SqlReferenceRequest(BaseModel):
    source_id: int
    query: str


class SqlReferenceResponse(BaseModel):
    schema_version: Literal["sql-reference.v1"]
    source_id: int
    source_uri: str
    declared_query: str
    kind: Literal["inline", "file"]
    content: str
    file_uri: str | None = None
    base_uri: str | None = None
    base_kind: str | None = None
    relative_path: str | None = None
    revision: dict[str, Any] | None = None
    config_revision: dict[str, Any] | None = None

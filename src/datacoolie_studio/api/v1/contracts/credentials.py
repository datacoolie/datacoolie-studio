from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


CredentialProvider = Literal["s3", "minio", "adls", "onelake", "gcs", "dbfs"]


class CredentialProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    provider: CredentialProvider
    auth_type: str = Field(min_length=1, max_length=50)
    config: dict[str, Any] = Field(default_factory=dict)
    secret: dict[str, Any] | None = None


class CredentialProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict[str, Any] | None = None
    secret: dict[str, Any] | None = None


class CredentialProfileInfo(BaseModel):
    id: str
    name: str
    provider: CredentialProvider
    auth_type: str
    secret_state: Literal["present", "missing", "unavailable"]
    masked_summary: dict[str, Any]
    version: int
    reference_count: int
    created_at: datetime
    updated_at: datetime


class CredentialProfileDetail(CredentialProfileInfo):
    config: dict[str, Any]


class CredentialCapabilities(BaseModel):
    providers: dict[str, list[str]]
    secret_store_available: bool
    secret_store_backend: str
    remediation: str | None = None

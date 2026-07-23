from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from datacoolie_studio.api.v1.contracts.shared import ReferenceResolutionResponse, ReferenceType

class LineageSummaryResponse(BaseModel):
    assets: int
    references: int
    dataflows: int
    dependencies: int
    stitched_assets: int
    declared_assets: int
    automatic_references: int
    manual_references: int
    unresolved_references: int
    automatic_dependencies: int
    manual_dependencies: int
    unresolved_dependencies: int
    diagnostics: int


class LineageAssetResponse(BaseModel):
    id: str
    entity_type: Literal["asset"]
    asset_type: Literal["table", "path", "sql_query", "python_function", "api", "unresolved"]
    declaration_status: Literal["declared", "discovered_only"]
    display_name: str
    label: str
    display_label: str | None = None
    endpoint_locator: str | None = None
    endpoint_kind: str | None = None
    identity_type: str | None = None
    connection_name: str | None = None
    connection_type: str | None = None
    format: str | None = None
    catalog: str | None = None
    database: str | None = None
    schema_name: str | None = None
    table: str | None = None
    path: str | None = None
    python_function: str | None = None
    metadata_source_ids: list[int] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    connection_names: list[str] = Field(default_factory=list)
    mapping_target: dict[str, str] | None = None


class LineageReferenceResponse(BaseModel):
    id: str
    entity_type: Literal["reference"]
    reference_type: ReferenceType
    display_name: str
    normalized_value: str
    resolution: ReferenceResolutionResponse
    resolved_asset_id: str | None = None
    resolved_asset_ids: list[str] = Field(default_factory=list)
    candidate_asset_ids: list[str] = Field(default_factory=list)
    occurrence_count: int = 0
    consumer_asset_ids: list[str] = Field(default_factory=list)
    provenances: list[Literal["sql", "python", "python_sql"]] = Field(default_factory=list)
    dependency_count: int = 0
    manual_mapping: dict[str, Any] | None = None

    @field_validator("reference_type", mode="before")
    @classmethod
    def normalize_reference_type(cls, value: Any) -> Any:
        if str(value or "").strip().lower() == "dynamic_expression":
            return "unknown"
        return value


class SourceLocationResponse(BaseModel):
    module: str | None = None
    path: str | None = None
    function_path: str | None = None
    line: int | None = None
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None
    coordinate_space: Literal["query_source", "function_source"] | None = None


class LineageDataflowResponse(BaseModel):
    id: str
    dataflow_id: str
    name: str
    source_asset_id: str
    destination_asset_id: str
    stage: str | None = None
    load_type: str | None = None
    metadata_source_id: int
    metadata_source_uri: str


class LineageDependencyResponse(BaseModel):
    id: str
    target_asset_id: str
    consumer_asset_id: str
    kind: Literal["reads", "uses"]
    provenance: Literal["sql", "python", "python_sql"]
    resolution: ReferenceResolutionResponse
    resolution_method: str
    reference_id: str
    reference_occurrence_id: str
    resolved_asset_id: str | None = None


class LineageResponse(BaseModel):
    schema_version: Literal["lineage.v4"]
    summary: LineageSummaryResponse
    assets: list[LineageAssetResponse]
    references: list[LineageReferenceResponse]
    dataflows: list[LineageDataflowResponse]
    dependencies: list[LineageDependencyResponse]

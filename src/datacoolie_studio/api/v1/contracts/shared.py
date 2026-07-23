from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

TargetIdentifierKind = Literal["logical_table", "physical_path", "api_endpoint"]
ReferenceType = Literal["table_reference", "path_reference", "api_endpoint_reference", "unknown"]
ResolutionState = Literal["automatic", "manual", "unresolved"]
ResolutionReason = Literal["no_match", "multiple_matches", "conflicting_targets", "incomplete", "target_missing"]


class ReferenceResolutionResponse(BaseModel):
    state: ResolutionState
    reason: ResolutionReason | None = None

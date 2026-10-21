"""Read-model value objects shared by Assets collaborators."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class AssetsCatalog:
    payload: dict[str, Any]
    input_fingerprint: str


@dataclass(frozen=True)

class AssetAttention:
    severity: str
    code: str
    message: str
    source_type: str
    subject_type: str = "asset"
    dataflow_id: str | None = None
    metadata_source_id: int | None = None
    reference_id: str | None = None
    reference_occurrence_id: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "source_type": self.source_type,
            "subject_type": self.subject_type,
            "dataflow_id": self.dataflow_id,
            "metadata_source_id": self.metadata_source_id,
            "reference_id": self.reference_id,
            "reference_occurrence_id": self.reference_occurrence_id,
            "details": self.details or {},
        }

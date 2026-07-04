from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SourceLocation:
    module: str | None = None
    path: str | None = None
    line: int | None = None
    column: int | None = None


@dataclass(slots=True)
class InputEvidence:
    kind: str
    value: str
    provenance: str
    confidence: str = "exact"
    catalog: str | None = None
    database: str | None = None
    schema_name: str | None = None
    table: str | None = None
    sql: str | None = None
    location: SourceLocation | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AnalysisResult:
    inputs: list[InputEvidence] = field(default_factory=list)
    temp_views: dict[str, InputEvidence] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputs": [item.to_dict() for item in self.inputs],
            "temp_views": {name: item.to_dict() for name, item in self.temp_views.items()},
            "diagnostics": self.diagnostics,
        }

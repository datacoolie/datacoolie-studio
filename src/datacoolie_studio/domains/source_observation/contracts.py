from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ObservationOutcome = Literal["changed", "unchanged", "error", "skipped"]


@dataclass(frozen=True)
class ObservationResult:
    source_id: int
    source_kind: str
    outcome: ObservationOutcome
    pending_changes: bool | None
    observed_revision: dict[str, object] | None
    error: dict[str, object] | None
    inventory_metrics: dict[str, object] | None
    started_at: datetime
    completed_at: datetime

    @property
    def duration_ms(self) -> int:
        return max(
            0,
            round((self.completed_at - self.started_at).total_seconds() * 1000),
        )

    @property
    def changed(self) -> bool:
        return self.outcome == "changed"

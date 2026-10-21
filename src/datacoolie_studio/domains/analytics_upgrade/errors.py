"""Typed errors used to classify analytics-upgrade failures."""
from __future__ import annotations

class AnalyticsUpgradeSourceError(RuntimeError):
    def __init__(self, source_id: int, *, code: str, message: str) -> None:
        super().__init__(f"Log source {source_id}: {message}")
        self.source_id = source_id
        self.code = code

class AnalyticsUpgradeScopeChangedError(RuntimeError):
    code = "source_scope_changed"

    def __init__(self) -> None:
        super().__init__("Enabled Log source scope changed during analytics upgrade")

__all__ = ["AnalyticsUpgradeScopeChangedError", "AnalyticsUpgradeSourceError"]

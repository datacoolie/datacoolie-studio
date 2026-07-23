from __future__ import annotations


class AnalyticsRebuildRequired(RuntimeError):
    code = "analytics_rebuild_required"

    def __init__(
        self,
        message: str,
        *,
        source_ids: list[int] | None = None,
        missing_source_ids: list[int] | None = None,
        reason: str = "not_ready",
    ) -> None:
        super().__init__(message)
        self.source_ids = source_ids or []
        self.missing_source_ids = missing_source_ids or []
        self.reason = reason

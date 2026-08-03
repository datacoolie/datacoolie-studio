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

    def detail(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": str(self),
            "reason": self.reason,
            "source_ids": self.source_ids,
            "missing_source_ids": self.missing_source_ids,
        }


class AnalyticsSchemaIncompatibleError(RuntimeError):
    code = "schema_incompatible"


class AnalyticsFileChangedDuringPublishError(RuntimeError):
    code = "file_changed_during_sync"

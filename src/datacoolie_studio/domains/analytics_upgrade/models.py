"""Analytics-upgrade assessment value objects."""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class CacheAssessment:
    action: str
    source_version: int | None
    reason_code: str
    missing_source_ids: tuple[int, ...] = ()
    extra_source_ids: tuple[int, ...] = ()
    migration_kind: str | None = None
    migration_reason: str | None = None
    previous_fingerprint: str | None = None
    target_fingerprint: str | None = None

    @property
    def operation_kind(self) -> str:
        if self.action == "unsupported":
            return "unsupported"
        if self.action == "schema_migration" or self.migration_kind in {
            "schema_migration",
            "schema17_native_rewrite",
            "additive_ddl",
            "stored_column_rewrite",
        }:
            return "schema_migration"
        if self.action == "source_repair":
            return "source_repair"
        if self.action == "initialize":
            return "initialize"
        if self.reason_code == "schema_mismatch":
            return "schema_upgrade"
        if self.reason_code == "source_scope_changed":
            return "scope_reconcile"
        return "cache_rebuild"

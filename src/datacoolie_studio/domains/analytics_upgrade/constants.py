"""Shared analytics-upgrade lifecycle constants."""
UPGRADE_ID = 1
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 3600
UPGRADE_POLL_SECONDS = 30

NON_PARKING_SOURCE_CODES = {
    "source_sync_busy",
    "provider_dependency_missing",
    "provider_not_enabled",
    "storage_provider_not_installed",
    "invalid_json",
    "file_read_failed",
    "file_changed_during_sync",
    "schema_incompatible",
    "publish_failed",
    "source_replay_failed",
}

from __future__ import annotations

from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.storage.uri import (
    StorageProviderNotEnabled,
    require_local_path,
)


def source_revision(source: EnvironmentSource) -> dict[str, Any]:
    """Return a shallow source revision without walking the log tree."""

    try:
        path = require_local_path(source.uri)
    except StorageProviderNotEnabled as exc:
        return {
            "provider": exc.provider,
            "uri": source.uri,
            "path": source.uri,
            "exists": False,
            "source_kind": source.source_kind,
            "object_type": "provider_not_enabled",
        }
    exists = path.exists()
    state = path.stat() if exists else None
    return {
        "provider": "local",
        "uri": source.uri,
        "path": str(path),
        "exists": exists,
        "source_kind": source.source_kind,
        "object_type": "directory" if exists and path.is_dir() else "file" if exists else "missing",
        "file_count": None,
        "total_size": state.st_size if state and path.is_file() else None,
        "max_mtime_ns": state.st_mtime_ns if state else None,
    }

from __future__ import annotations

import logging
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.storage.capabilities import (
    metadata_write_back_supported,
)


logger = logging.getLogger(__name__)


def unexpected_validation_result(
    source: EnvironmentSource,
    *,
    operation: str,
    message: str,
    exc: Exception,
    provider: str | None = None,
) -> dict[str, Any]:
    """Build a safe validation result and emit a non-sensitive diagnostic.

    Validation is a user-facing probe, so an unexpected provider/adapter error
    must not turn into a traceback containing a local path, signed URI, or
    credential material. The error type and source identity are enough for
    operational triage; the original exception remains intentionally out of
    the response and log message.
    """

    resolved_provider = provider or str(
        getattr(source, "storage_provider", None) or "unknown"
    )
    _log_unexpected_validation(
        operation=operation,
        source_id=getattr(source, "id", None),
        source_kind=getattr(source, "source_kind", None),
        provider=resolved_provider,
        exc=exc,
    )
    return {
        "source_id": source.id,
        "source_kind": source.source_kind,
        "status": "error",
        "message": message,
        "detected_provider": resolved_provider,
        "detected_format": None,
        "metadata_write_back_supported": metadata_write_back_supported(resolved_provider),
        "record_counts": {},
        "records_scanned": 0,
        "errors": [
            {
                "code": "source_validation_internal_error",
                "message": message,
            }
        ],
    }


def unexpected_storage_validation_result(
    *,
    operation: str,
    provider: str | None,
    message: str,
    exc: Exception,
) -> dict[str, Any]:
    resolved_provider = provider or "unknown"
    _log_unexpected_validation(
        operation=operation,
        source_id=None,
        source_kind="storage_probe",
        provider=resolved_provider,
        exc=exc,
    )
    return {
        "status": "error",
        "provider": resolved_provider,
        "canonical_uri": None,
        "message": message,
        "error": {
            "code": "storage_validation_internal_error",
            "message": message,
        },
    }


def _log_unexpected_validation(
    *,
    operation: str,
    source_id: int | None,
    source_kind: str | None,
    provider: str,
    exc: Exception,
) -> None:
    logger.warning(
        "Unexpected source validation failure operation=%s source_id=%s "
        "source_kind=%s provider=%s error_type=%s",
        operation,
        source_id,
        source_kind,
        provider,
        type(exc).__name__,
    )

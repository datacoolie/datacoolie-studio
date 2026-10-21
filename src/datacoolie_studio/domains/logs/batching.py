from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from threading import Condition
from typing import Any, TypeVar

from datacoolie_studio.core.config import (
    analytics_upgrade_batch_max_bytes,
    analytics_upgrade_batch_max_files,
    analytics_upgrade_max_inflight_bytes,
)
from datacoolie_studio.domains.logs.discovery import DiscoveredLogFile


T = TypeVar("T")

LOG_BATCH_MAX_FILES = analytics_upgrade_batch_max_files()
LOG_BATCH_MAX_BYTES = analytics_upgrade_batch_max_bytes()
LOG_MAX_INFLIGHT_BYTES = analytics_upgrade_max_inflight_bytes()


class ByteBudget:
    """Keep concurrently materialized log batches within one byte budget."""

    def __init__(self, limit_bytes: int) -> None:
        self._limit_bytes = max(1, int(limit_bytes))
        self._condition = Condition()
        self._inflight_bytes = 0

    @contextmanager
    def acquire(self, requested_bytes: int):
        requested = max(1, int(requested_bytes))
        with self._condition:
            while (
                self._inflight_bytes
                and self._inflight_bytes + requested > self._limit_bytes
            ):
                self._condition.wait()
            self._inflight_bytes += requested
        try:
            yield
        finally:
            with self._condition:
                self._inflight_bytes = max(0, self._inflight_bytes - requested)
                self._condition.notify_all()


log_byte_budget = ByteBudget(LOG_MAX_INFLIGHT_BYTES)


def sized_batches(
    items: Sequence[T],
    *,
    size_of: Callable[[T], int],
) -> list[list[T]]:
    batches: list[list[T]] = []
    current: list[T] = []
    current_bytes = 0
    for item in items:
        item_bytes = max(0, int(size_of(item)))
        if current and (
            len(current) >= LOG_BATCH_MAX_FILES
            or current_bytes + item_bytes > LOG_BATCH_MAX_BYTES
        ):
            batches.append(current)
            current = []
            current_bytes = 0
        current.append(item)
        current_bytes += item_bytes
    if current:
        batches.append(current)
    return batches


def file_batches(
    file_states: Sequence[Mapping[str, Any]],
) -> list[list[Mapping[str, Any]]]:
    return sized_batches(file_states, size_of=_file_state_bytes)


def candidate_batches(
    candidates: Sequence[DiscoveredLogFile],
) -> list[list[DiscoveredLogFile]]:
    return sized_batches(candidates, size_of=lambda item: int(item.revision.size or 0))


def materialization_fingerprint() -> str:
    """Version the admission/parser and exhaustive DuckDB mapping used by replay."""
    from datacoolie_studio.domains.analytics import schema
    from datacoolie_studio.domains.analytics.log_contract import (
        LOG_SCHEMA_VERSION,
        SUPPORTED_LOG_SCHEMA_VERSIONS,
    )
    from datacoolie_studio.domains.analytics.schema_compiler import (
        SCHEMA_DOCUMENT,
        V7_SCHEMA_DOCUMENT,
    )
    payload = {
        "analytics_schema": schema.ANALYTICS_SCHEMA_VERSION,
        # Retain the v6 value for older checkpoint readers while including the
        # complete admission set in the contract fingerprint. A v7 semantic
        # change must invalidate old batches and result-cache inputs.
        "log_schema": LOG_SCHEMA_VERSION,
        "log_schema_versions": SUPPORTED_LOG_SCHEMA_VERSIONS,
        "admission_schema": {
            "v6": SCHEMA_DOCUMENT,
            "v7": V7_SCHEMA_DOCUMENT,
        },
        "codec": "native-v1",
        "dataflow_columns": schema.DATAFLOW_COLUMN_TYPES,
        "job_columns": schema.JOB_COLUMN_TYPES,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def batch_identity(batch: Sequence[Mapping[str, Any]]) -> str:
    payload = [
        {
            "file_uri": str(file_state["file_uri"]),
            "file_kind": str(file_state["file_kind"]),
            "revision_json": str(file_state["revision_json"]),
        }
        for file_state in batch
    ]
    encoded = json.dumps({"contract": materialization_fingerprint(), "files": payload}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def resume_batches(
    checkpoint: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(checkpoint, Mapping):
        return {}
    batches = checkpoint.get("batches")
    if not isinstance(batches, Mapping):
        return {}
    return {
        str(identity): value
        for identity, value in batches.items()
        if isinstance(value, Mapping) and value.get("status") == "completed"
        and value.get("materialization_fingerprint") == materialization_fingerprint()
    }


def checkpoint_file_map(
    checkpoint: Mapping[str, Any] | None,
) -> dict[str, tuple[str, str]]:
    file_map: dict[str, tuple[str, str]] = {}
    for batch in resume_batches(checkpoint).values():
        files = batch.get("files")
        if not isinstance(files, list):
            continue
        for file_state in files:
            if not isinstance(file_state, Mapping):
                continue
            file_uri = str(file_state.get("file_uri") or "")
            if file_uri:
                file_map[file_uri] = (
                    str(file_state.get("file_kind") or ""),
                    str(file_state.get("revision_json") or ""),
                )
    return file_map


def batch_bytes(batch: Sequence[Mapping[str, Any]]) -> int:
    return max(1, sum(_file_state_bytes(file_state) for file_state in batch))


def _file_state_bytes(file_state: Mapping[str, Any]) -> int:
    try:
        return max(
            0,
            int(json.loads(str(file_state["revision_json"])).get("size") or 0),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return 0

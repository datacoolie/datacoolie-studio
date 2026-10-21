from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LogFileDescriptor:
    """Immutable source identity plus the transient DuckDB read location."""

    canonical_uri: str
    file_kind: str
    revision_json: str
    read_uri: str
    partition_date: str | None = None

    def __post_init__(self) -> None:
        if not self.canonical_uri:
            raise ValueError("Log file canonical_uri cannot be empty")
        if not self.read_uri:
            raise ValueError("Log file read_uri cannot be empty")
        if self.file_kind not in {"dataflow_json", "job_json"}:
            raise ValueError(f"Unsupported analytics log file kind: {self.file_kind!r}")


DataflowFile = LogFileDescriptor
JobFile = LogFileDescriptor


@dataclass(frozen=True, slots=True)
class LogPublishBatch:
    """One bounded set of staged log files ready for DuckDB publication."""

    dataflow_files: tuple[DataflowFile, ...] = ()
    job_files: tuple[JobFile, ...] = ()
    source_files: tuple[Mapping[str, Any], ...] = ()


__all__ = [
    "DataflowFile",
    "JobFile",
    "LogFileDescriptor",
    "LogPublishBatch",
]

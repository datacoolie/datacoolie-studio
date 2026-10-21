"""Shared DataCoolie log category and record-type contract."""

from __future__ import annotations


LOG_CATEGORY_EXECUTION = "execution_logs"
LOG_CATEGORY_SYSTEM = "system_logs"

LOG_TYPE_JOB_RUN = "job_run_log"
LOG_TYPE_DATAFLOW_RUN = "dataflow_run_log"
LOG_TYPE_SYSTEM = "system_log"

LOG_STREAM_TYPES: dict[str, str] = {
    LOG_TYPE_JOB_RUN: LOG_TYPE_JOB_RUN,
    LOG_TYPE_DATAFLOW_RUN: LOG_TYPE_DATAFLOW_RUN,
    LOG_CATEGORY_SYSTEM: LOG_TYPE_SYSTEM,
}

__all__ = [
    "LOG_CATEGORY_EXECUTION",
    "LOG_CATEGORY_SYSTEM",
    "LOG_STREAM_TYPES",
    "LOG_TYPE_DATAFLOW_RUN",
    "LOG_TYPE_JOB_RUN",
    "LOG_TYPE_SYSTEM",
]

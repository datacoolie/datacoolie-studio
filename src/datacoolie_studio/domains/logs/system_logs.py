from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest
from datacoolie_studio.domains.logs.reader import read_system_log_file
from datacoolie_studio.domains.sources import service as source_validation


def system_log_records(
    session: Session,
    paths: list[EnvironmentSource],
    *,
    job_id: str,
    dataflow_id: str | None = None,
    include_dataflow_logs: bool = False,
    level: str | None = None,
    q: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    enabled_ids = _source_ids(paths)
    if not enabled_ids or not job_id:
        return {"records": [], "total": 0, "files": [], "errors": []}
    files = list(
        session.scalars(
            select(LogFileManifest)
            .where(
                LogFileManifest.source_id.in_(enabled_ids),
                LogFileManifest.file_kind == "system_jsonl",
                LogFileManifest.job_id == job_id,
            )
            .order_by(LogFileManifest.log_timestamp.desc(), LogFileManifest.id.desc())
        )
    )
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    total = 0
    remaining_offset = offset
    for file in files:
        file_rows, file_total, file_errors = read_system_log_file(
            file.file_uri,
            job_id=job_id,
            dataflow_id=dataflow_id,
            include_dataflow_logs=include_dataflow_logs,
            level=level,
            q=q,
            limit=limit - len(records),
            offset=remaining_offset,
        )
        total += file_total
        errors.extend(file_errors)
        if remaining_offset:
            remaining_offset = max(0, remaining_offset - file_total)
        records.extend(file_rows)
        if len(records) >= limit:
            break
    return {
        "records": records,
        "total": total,
        "files": [
            {
                "source_id": file.source_id,
                "file_uri": file.file_uri,
                "row_count": file.row_count,
                "log_timestamp": file.log_timestamp,
                "run_date": file.run_date,
            }
            for file in files
        ],
        "errors": errors,
    }


def _source_ids(paths: list[EnvironmentSource]) -> list[int]:
    return sorted(
        path.id
        for path in paths
        if path.enabled and not source_validation.is_validated_empty_log_source(path)
    )

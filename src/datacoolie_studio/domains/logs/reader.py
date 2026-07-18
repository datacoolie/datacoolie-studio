from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from datacoolie_studio.core.time import utc_datetime_sort_key


DATAFLOW_COLUMNS = [
    "job_id",
    "dataflow_id",
    "dataflow_name",
    "stage",
    "source_connection_type",
    "source_format",
    "source_table",
    "source_path",
    "destination_connection_type",
    "destination_format",
    "destination_table",
    "destination_path",
    "destination_load_type",
    "dataflow_run_id",
    "operation_type",
    "start_time",
    "end_time",
    "duration_seconds",
    "status",
    "error_message",
    "retry_attempts",
    "source_duration_seconds",
    "source_rows_read",
    "transform_duration_seconds",
    "destination_duration_seconds",
    "destination_rows_written",
    "destination_rows_inserted",
    "destination_rows_updated",
    "destination_rows_deleted",
    "destination_files_added",
    "destination_files_removed",
    "destination_bytes_added",
    "destination_bytes_removed",
    "destination_bytes_saved",
]

SYSTEM_LOG_FILE_RE = re.compile(r"system_log_(?P<stamp>\d{8}_\d{6})_(?P<job_id>.+)\.jsonl$")


def discover_dataflow_parquet_files(root_uri: str) -> list[str]:
    root = Path(root_uri).expanduser()
    if not root.exists():
        return []
    candidates = []
    if (root / "dataflow_run_log").exists():
        candidates.append(root / "dataflow_run_log")
    candidates.append(root)
    files: list[Path] = []
    for candidate in candidates:
        files.extend(candidate.rglob("*.parquet"))
    unique = sorted({file.resolve() for file in files})
    return [file.as_posix() for file in unique]


def read_dataflow_logs(root_uris: list[str], limit: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    files: list[str] = []
    errors: list[dict[str, str]] = []
    for root in root_uris:
        discovered = discover_dataflow_parquet_files(root)
        if not discovered:
            errors.append({"uri": root, "message": "No dataflow_run_log parquet files found"})
        files.extend(discovered)
    if not files:
        return [], errors
    sql = f"SELECT * FROM read_parquet({_duckdb_list(files)}, union_by_name=true) ORDER BY end_time DESC NULLS LAST"
    if limit:
        sql += f" LIMIT {int(limit)}"
    conn = duckdb.connect(database=":memory:")
    try:
        result = conn.execute(sql)
        names = [desc[0] for desc in result.description]
        rows = [_json_ready(dict(zip(names, row))) for row in result.fetchall()]
        return rows, errors
    finally:
        conn.close()


def discover_job_jsonl_files(root_uri: str) -> list[str]:
    root = Path(root_uri).expanduser()
    if not root.exists():
        return []
    candidates = []
    if (root / "job_run_log").exists():
        candidates.append(root / "job_run_log")
    candidates.append(root)
    files: list[Path] = []
    for candidate in candidates:
        files.extend(candidate.rglob("*.jsonl"))
    unique = sorted({file.resolve() for file in files if "job_run_log" in file.as_posix()})
    return [file.as_posix() for file in unique]


def discover_system_jsonl_files(root_uri: str | None) -> list[str]:
    if not root_uri:
        return []
    root = Path(root_uri).expanduser()
    if not root.exists():
        return []
    candidates = []
    if (root / "system_logs").exists():
        candidates.append(root / "system_logs")
    candidates.append(root)
    files: list[Path] = []
    for candidate in candidates:
        files.extend(candidate.rglob("system_log_*.jsonl"))
    unique = sorted({file.resolve() for file in files})
    return [file.as_posix() for file in unique]


def parse_system_log_file_metadata(file_uri: str) -> dict[str, Any]:
    path = Path(file_uri)
    match = SYSTEM_LOG_FILE_RE.match(path.name)
    if not match:
        return {"job_id": None, "log_timestamp": None, "run_date": None}
    timestamp = None
    try:
        timestamp = datetime.strptime(match.group("stamp"), "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        timestamp = None
    return {
        "job_id": match.group("job_id"),
        "log_timestamp": timestamp,
        "run_date": timestamp.date() if timestamp else None,
    }


def read_system_log_file(
    file_uri: str,
    *,
    job_id: str | None = None,
    dataflow_id: str | None = None,
    include_dataflow_logs: bool = False,
    level: str | None = None,
    q: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int, list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    path = Path(file_uri)
    normalized_level = (level or "").strip().lower()
    normalized_query = (q or "").strip().lower()
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = _json_ready(json.loads(line))
                except json.JSONDecodeError as exc:
                    errors.append({"uri": file_uri, "message": f"Invalid JSONL at line {line_number}: {exc}"})
                    continue
                if job_id:
                    row.setdefault("job_id", job_id)
                if dataflow_id and str(row.get("dataflow_id") or "").lower() != dataflow_id.lower():
                    continue
                if not dataflow_id and not include_dataflow_logs and str(row.get("dataflow_id") or "").strip():
                    continue
                if normalized_level and str(row.get("level") or "").lower() != normalized_level:
                    continue
                if normalized_query and normalized_query not in json.dumps(row, ensure_ascii=False, default=str).lower():
                    continue
                rows.append(row)
    except OSError as exc:
        errors.append({"uri": file_uri, "message": str(exc)})
    total = len(rows)
    return rows[offset : offset + limit], total, errors


def read_job_logs(root_uris: list[str], limit: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    files: list[str] = []
    errors: list[dict[str, str]] = []
    for root in root_uris:
        discovered = discover_job_jsonl_files(root)
        if not discovered:
            errors.append({"uri": root, "message": "No job_run_log JSONL files found"})
        files.extend(discovered)
    if not files:
        return [], errors

    rows: list[dict[str, Any]] = []
    for file_name in files:
        path = Path(file_name)
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        rows.append(_json_ready(json.loads(line)))
                    except json.JSONDecodeError as exc:
                        errors.append({"uri": file_name, "message": f"Invalid JSONL at line {line_number}: {exc}"})
        except OSError as exc:
            errors.append({"uri": file_name, "message": str(exc)})

    rows.sort(key=lambda row: _sort_time(row.get("end_time") or row.get("start_time")), reverse=True)
    if limit:
        rows = rows[: int(limit)]
    return rows, errors


def _duckdb_list(paths: list[str]) -> str:
    return "[" + ", ".join("'" + path.replace("'", "''") + "'" for path in paths) + "]"


def _json_ready(row: dict[str, Any]) -> dict[str, Any]:
    ready = {}
    for key, value in row.items():
        if isinstance(value, (datetime, date)):
            ready[key] = value.isoformat()
        else:
            ready[key] = value
    return ready


def _sort_time(value: object) -> datetime:
    return utc_datetime_sort_key(value)

"""Readers for the DataCoolie v6 JSON Lines log contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from datacoolie_studio.core.time import utc_datetime_sort_key
from datacoolie_studio.domains.analytics.log_contract import admit_log_record
from datacoolie_studio.domains.analytics.errors import AnalyticsSchemaIncompatibleError
from datacoolie_studio.domains.logs.contracts import (
    LOG_TYPE_DATAFLOW_RUN,
    LOG_TYPE_JOB_RUN,
    LOG_TYPE_SYSTEM,
)
from datacoolie_studio.domains.logs.system_log_filename import parse_system_log_filename
from datacoolie_studio.domains.logs.system_log_selection import record_matches_filename


DATAFLOW_COLUMNS = [
    "log_schema_version",
    "_type",
    "job_id",
    "job_num",
    "job_index",
    "dataflow_id",
    "workspace_id",
    "dataflow_name",
    "stage",
    "source_connection_type",
    "source_format",
    "source_table",
    "source_path",
    "source_query",
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

# A line is read as bytes so an original download never round-trips through a
# JSON serializer or a text newline conversion.  Export opts into this cap;
# preview keeps its established reader behavior unless a caller supplies one.
MAX_SYSTEM_LOG_LINE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class SystemLogLine:
    """One admitted physical line, or one line-level admission diagnostic."""

    line_number: int
    raw: bytes = b""
    row: dict[str, Any] | None = None
    diagnostic: dict[str, Any] | None = None
    fatal: bool = False


def iter_system_log_lines(
    file_uri: str,
    *,
    max_line_bytes: int | None = None,
) -> Iterator[SystemLogLine]:
    """Yield validated system-log lines without changing their source bytes.

    Unsupported historical versions are diagnostics that do not stop preview
    iteration.  Invalid JSON, a malformed current-schema record, an unexpected
    record type, an overlong line, or a read failure is fatal for the file.  A
    caller that needs a file-atomic view can discard already yielded records;
    an export caller can fail the whole request on any diagnostic.
    """

    cap = None if max_line_bytes is None else max(1, int(max_line_bytes))
    try:
        with Path(file_uri).open("rb") as handle:
            line_number = 0
            while True:
                # Preview keeps the historical unbounded reader.  Export passes
                # an explicit cap and reads one byte over it before admission.
                raw = handle.readline() if cap is None else handle.readline(cap + 1)
                if not raw:
                    break
                line_number += 1
                if cap is not None and len(raw) > cap:
                    yield SystemLogLine(
                        line_number=line_number,
                        diagnostic={
                            "uri": file_uri,
                            "line": str(line_number),
                            "message": (
                                f"System log record at line {line_number} exceeds "
                                f"the {cap} byte limit"
                            ),
                            "code": "line_too_large",
                        },
                        fatal=True,
                    )
                    return

                body = raw.rstrip(b"\r\n")
                if not body.strip():
                    continue
                try:
                    value = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
                    yield SystemLogLine(
                        line_number=line_number,
                        diagnostic={
                            "uri": file_uri,
                            "line": str(line_number),
                            "message": f"Invalid JSON record at line {line_number}: {exc}",
                            "code": "invalid_json",
                        },
                        fatal=True,
                    )
                    return
                if not isinstance(value, dict):
                    yield SystemLogLine(
                        line_number=line_number,
                        diagnostic={
                            "uri": file_uri,
                            "line": str(line_number),
                            "message": f"JSON record at line {line_number} is not an object",
                            "code": "schema_incompatible",
                        },
                        fatal=True,
                    )
                    return
                try:
                    admitted = admit_log_record(
                        value,
                        expected_type=LOG_TYPE_SYSTEM,
                        uri=file_uri,
                        line=line_number,
                    )
                except AnalyticsSchemaIncompatibleError as exc:
                    yield SystemLogLine(
                        line_number=line_number,
                        diagnostic={
                            "uri": file_uri,
                            "line": str(line_number),
                            "message": str(exc),
                            "code": exc.code,
                        },
                        fatal=True,
                    )
                    return
                if not admitted:
                    yield SystemLogLine(
                        line_number=line_number,
                        diagnostic={
                            "uri": file_uri,
                            "line": str(line_number),
                            "message": f"Unsupported schema at line {line_number}",
                            "code": "unsupported_log_schema",
                        }
                    )
                    continue
                yield SystemLogLine(
                    line_number=line_number,
                    raw=raw,
                    row=_json_ready(value),
                )
    except OSError as exc:
        yield SystemLogLine(
            line_number=0,
            diagnostic={
                "uri": file_uri,
                "message": str(exc),
                "code": "storage_read_failed",
            },
            fatal=True,
        )


def discover_dataflow_json_files(root_uri: str) -> list[str]:
    return _discover_stream_files(root_uri, "dataflow_run_log")


def discover_job_json_files(root_uri: str) -> list[str]:
    return _discover_stream_files(root_uri, "job_run_log")




def read_dataflow_logs(
    root_uris: list[str], limit: int | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows, errors = _read_stream_roots(
        root_uris,
        discover_dataflow_json_files,
        expected_type=LOG_TYPE_DATAFLOW_RUN,
        missing_message="No dataflow_run_log JSON files found",
    )
    rows.sort(key=lambda row: _sort_time(row.get("end_time") or row.get("start_time")), reverse=True)
    return (rows[: int(limit)] if limit else rows), errors


def read_job_logs(
    root_uris: list[str], limit: int | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows, errors = _read_stream_roots(
        root_uris,
        discover_job_json_files,
        expected_type=LOG_TYPE_JOB_RUN,
        missing_message="No job_run_log JSON files found",
    )
    rows.sort(key=lambda row: _sort_time(row.get("end_time") or row.get("start_time")), reverse=True)
    return (rows[: int(limit)] if limit else rows), errors


def parse_system_log_file_metadata(file_uri: str) -> dict[str, Any]:
    """Derive filename metadata without opening the source object."""

    parsed = parse_system_log_filename(file_uri)
    timestamp = parsed.log_timestamp
    return {
        "job_id": None,
        "filename_job_id": parsed.job_id,
        "filename_job_id_alt": parsed.job_id_alt,
        "log_session_id": None,
        "log_timestamp": timestamp,
        "event_min_timestamp": None,
        "event_max_timestamp": None,
        "run_date": timestamp.date() if timestamp else None,
    }


@dataclass
class SystemLogScanStats:
    """Admission count for this read, independent of display filters."""

    admitted_records: int = 0


def read_system_log_file(
    file_uri: str,
    *,
    job_id: str | None = None,
    log_session_id: str | None = None,
    log_session_scope: str = "all",
    dataflow_id: str | None = None,
    dataflow_run_id: str | None = None,
    event_name: str | None = None,
    include_dataflow_logs: bool = False,
    level: str | None = None,
    q: str | None = None,
    limit: int | None = 500,
    offset: int = 0,
    scan_stats: SystemLogScanStats | None = None,
    filename_job_ids: tuple[str, ...] = (),
) -> tuple[list[dict[str, Any]], int, list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    fatal = False
    admitted_records = 0
    filename_mismatch_reported = False
    for item in iter_system_log_lines(file_uri):
        if item.diagnostic is not None:
            errors.append({
                str(key): str(value)
                for key, value in item.diagnostic.items()
                if key != "line" or value is not None
            })
            if item.fatal:
                fatal = True
                break
            continue
        if item.row is None:
            continue
        admitted_records += 1
        if not filename_mismatch_reported and not record_matches_filename(
            item.row, filename_job_ids
        ):
            errors.append({
                "uri": file_uri,
                "line": str(item.line_number),
                "message": "System log record job identity differs from its filename",
                "code": "system_log_filename_identity_mismatch",
            })
            filename_mismatch_reported = True
        if not system_log_record_matches(
            item.row,
            job_id=job_id,
            log_session_id=log_session_id,
            log_session_scope=log_session_scope,
            dataflow_id=dataflow_id,
            dataflow_run_id=dataflow_run_id,
            event_name=event_name,
            include_dataflow_logs=include_dataflow_logs,
            level=level,
            q=q,
        ):
            continue
        row = dict(item.row)
        row["raw_record_json"] = item.raw.rstrip(b"\r\n").decode("utf-8")
        row["_record_line"] = item.line_number
        rows.append(row)
    if fatal:
        # A malformed current record invalidates the physical file as a unit;
        # callers must never present a prefix as a complete preview.
        if scan_stats is not None:
            scan_stats.admitted_records = 0
        return [], 0, errors
    if scan_stats is not None:
        scan_stats.admitted_records = admitted_records
    total = len(rows)
    if limit is None:
        return rows[offset:], total, errors
    return rows[offset : offset + limit], total, errors


def system_log_record_matches(
    row: dict[str, Any],
    *,
    job_id: str | None = None,
    log_session_id: str | None = None,
    log_session_scope: str = "all",
    dataflow_id: str | None = None,
    dataflow_run_id: str | None = None,
    event_name: str | None = None,
    include_dataflow_logs: bool = False,
    level: str | None = None,
    q: str | None = None,
) -> bool:
    """Apply the preview identity/display filters to one admitted row."""

    # Filename candidates select files; exact identity comes from each record
    # and must never be synthesized by the reader.
    if job_id and str(row.get("job_id") or "") != job_id:
        return False
    if log_session_scope == "null" and row.get("log_session_id") is not None:
        return False
    if log_session_scope == "exact" and row.get("log_session_id") != log_session_id:
        return False

    exact_dataflow_id = _identity_filter(dataflow_id)
    exact_dataflow_run_id = _identity_filter(dataflow_run_id)
    exact_event_name = _identity_filter(event_name)
    row_dataflow_id = _identity_value(row.get("dataflow_id"))
    if exact_dataflow_id is not None and row_dataflow_id != exact_dataflow_id:
        return False
    row_dataflow_run_id = _identity_value(row.get("dataflow_run_id"))
    if exact_dataflow_run_id is not None and row_dataflow_run_id != exact_dataflow_run_id:
        return False
    if exact_event_name is not None and _identity_value(row.get("event_name")) != exact_event_name:
        return False
    if (
        exact_dataflow_id is None
        and exact_dataflow_run_id is None
        and exact_event_name is None
        and not include_dataflow_logs
        and row_dataflow_id.strip()
    ):
        return False
    normalized_level = (level or "").strip().lower()
    if normalized_level and str(row.get("level") or "").lower() != normalized_level:
        return False
    normalized_query = (q or "").strip().lower()
    if normalized_query and normalized_query not in json.dumps(
        row, ensure_ascii=False, default=str
    ).lower():
        return False
    return True


def _discover_stream_files(root_uri: str, stream_name: str) -> list[str]:
    root = Path(root_uri).expanduser()
    if not root.exists():
        return []
    candidates = [root]
    if root.name != stream_name and (root / stream_name).is_dir():
        candidates.insert(0, root / stream_name)
    if root.name not in {"execution_logs", stream_name}:
        execution_root = root / "execution_logs" / stream_name
        if execution_root.is_dir():
            candidates.insert(0, execution_root)
    files = [path for candidate in candidates for path in candidate.rglob("*.json")]
    return _unique_files(path for path in files if stream_name in _parts(path))


def _read_stream_roots(
    roots: Iterable[str],
    discover: Any,
    *,
    expected_type: str,
    missing_message: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for root in roots:
        files = discover(root)
        if not files:
            errors.append({"uri": root, "message": missing_message})
            continue
        for file_uri in files:
            file_rows, file_errors = read_json_lines_file(
                file_uri,
                expected_type=expected_type,
            )
            rows.extend(file_rows)
            errors.extend(file_errors)
    return rows, errors


def read_json_lines_file(
    file_uri: str,
    *,
    expected_type: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    try:
        with Path(file_uri).open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    return [], [{"uri": file_uri, "message": f"Invalid JSONL at line {line_number}: {exc}", "code": "invalid_json"}]
                if not isinstance(value, dict):
                    return [], [{"uri": file_uri, "message": f"JSONL at line {line_number} is not an object", "code": "schema_incompatible"}]
                if expected_type:
                    try:
                        if not admit_log_record(value, expected_type=expected_type, uri=file_uri, line=line_number):
                            errors.append({"uri": file_uri, "message": f"Unsupported schema at line {line_number}", "code": "unsupported_log_schema"})
                            continue
                    except AnalyticsSchemaIncompatibleError as exc:
                        return [], [{"uri": file_uri, "message": str(exc), "code": exc.code}]
                if expected_type and value.get("_type") != expected_type:
                    errors.append({
                        "uri": file_uri,
                        "message": f"Unexpected log record type at line {line_number}: {value.get('_type')!r}",
                    })
                    continue
                rows.append(_json_ready(value))
    except OSError as exc:
        errors.append({"uri": file_uri, "message": str(exc)})
    return rows, errors


def _unique_files(files: Iterable[Path]) -> list[str]:
    return [
        path.as_posix()
        for path in sorted({path.resolve() for path in files})
        if path.is_file()
    ]


def _parts(path: Path) -> set[str]:
    return {part.casefold() for part in path.parts}


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _identity_filter(value: object) -> str | None:
    """Keep identity filters lexical while treating only absent/empty values as unset."""

    if value is None:
        return None
    text = str(value)
    return text or None


def _identity_value(value: object) -> str:
    return "" if value is None else str(value)


def _sort_time(value: object) -> datetime:
    return utc_datetime_sort_key(value)

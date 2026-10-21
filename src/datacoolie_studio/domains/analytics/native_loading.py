"""Strict admission and native file loading with physical-line provenance."""

from __future__ import annotations

import json
import math
import re
import tempfile
from array import array
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import duckdb

from datacoolie_studio.domains.analytics.contracts import LogFileDescriptor
from datacoolie_studio.domains.analytics.errors import (
    AnalyticsFileChangedDuringPublishError,
    AnalyticsInvalidJsonError,
    AnalyticsSchemaIncompatibleError,
)
from datacoolie_studio.domains.analytics.log_contract import admit_log_record
from datacoolie_studio.domains.analytics.record_codec import (
    NUMERIC_RANGE_MAPPINGS,
    root_column_types,
)
from datacoolie_studio.domains.analytics.sql import quote_identifier

# Match DuckDB's explicit maximum JSON object size. A bounded scan must never
# allocate an arbitrary single source line before it can report an oversized row.
MAX_RECORD_BYTES = 16 * 1024 * 1024
_MALFORMED_JSON_LINE_MAP_VERSIONS = {"1.5.3", "1.5.5"}

def _reject_constant(value):
    raise ValueError(f"Non-finite JSON token {value}")


def _revision(path: Path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _native_range_failure(node, value):
    if value is None:
        return None
    if node.category == "integer":
        bits = 64 if node.duckdb_type == "BIGINT" else 128
        if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
            return (), f"value outside {node.duckdb_type} range"
    elif node.category == "number":
        try:
            finite = math.isfinite(float(value))
        except OverflowError:
            finite = False
        if not finite:
            return (), "value outside finite DOUBLE range"
    elif node.category == "struct":
        for child in node.children:
            failure = _native_range_failure(child, value.get(child.name))
            if failure:
                path, error = failure
                return (child.name, *path), error
    elif node.category == "list":
        for index, child in enumerate(value):
            failure = _native_range_failure(node.item, child)
            if failure:
                path, error = failure
                return (index, *path), error
    return None


def _check_native_range(node, value, *, uri: str, line: int, field: str):
    failure = _native_range_failure(node, value)
    if failure:
        path, error = failure
        suffix = "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in path)
        raise AnalyticsSchemaIncompatibleError(
            f"{uri}:{line}: native storage at {field}{suffix}: {error}"
        )


@dataclass
class AdmittedFile:
    item: LogFileDescriptor
    read_path: Path
    lines: array
    summary: dict
    initial: tuple[int, int]

    def assert_stable(self, phase: str) -> None:
        if _revision(Path(self.item.read_uri)) != self.initial:
            raise AnalyticsFileChangedDuringPublishError(
                f"{self.item.canonical_uri}: file changed during {phase}"
            )


@contextmanager
def admit_file(item, *, expected_type: str):
    path = Path(item.read_uri)
    initial = _revision(path)
    summary = {"admitted": 0, "excluded": 0, "versions": {}, "locations": []}
    lines = array("q")
    normalization = False
    with ExitStack() as owned:
        # The scan copies original admitted bytes only when needed; same-version
        # ordinary files go directly to DuckDB, with no second materialized copy.
        with path.open("rb") as handle:
            line_number = 0
            while raw := handle.readline(MAX_RECORD_BYTES + 1):
                line_number += 1
                if len(raw) > MAX_RECORD_BYTES:
                    raise AnalyticsInvalidJsonError(
                        f"{item.canonical_uri}:{line_number}: record exceeds {MAX_RECORD_BYTES} bytes"
                    )
                if line_number == 1 and raw.startswith(b"\xef\xbb\xbf"):
                    normalization = True
                    raw = raw[3:]
                if not raw.strip():
                    continue
                try:
                    record = json.loads(
                        raw, parse_float=Decimal, parse_constant=_reject_constant
                    )
                except (ValueError, UnicodeError) as exc:
                    raise AnalyticsInvalidJsonError(
                        f"{item.canonical_uri}:{line_number}: {exc}"
                    ) from exc
                version = (
                    str(record.get("log_schema_version", "absent"))
                    if isinstance(record, dict)
                    else "non-object"
                )
                summary["versions"][version] = summary["versions"].get(version, 0) + 1
                if admit_log_record(
                    record,
                    expected_type=expected_type,
                    uri=item.canonical_uri,
                    line=line_number,
                ):
                    for key, node in NUMERIC_RANGE_MAPPINGS[
                        {"dataflow_run_log": "dataflow", "job_run_log": "job"}[
                            expected_type
                        ]
                    ].items():
                        _check_native_range(
                            node,
                            record.get(key),
                            uri=item.canonical_uri,
                            line=line_number,
                            field=f"/{key}",
                        )
                    summary["admitted"] += 1
                    lines.append(line_number)
                else:
                    normalization = True
                    summary["excluded"] += 1
                    if len(summary["locations"]) < 16:
                        summary["locations"].append(
                            {"line": line_number, "version": version}
                        )
        if _revision(path) != initial:
            raise AnalyticsFileChangedDuringPublishError(
                f"{item.canonical_uri}: file changed during admission"
            )
        read_path = path
        if normalization and lines:
            temporary = owned.enter_context(tempfile.TemporaryDirectory(prefix="dc-admitted-"))
            admitted = Path(temporary) / "admitted.ndjson"
            # Second bounded byte scan follows the validated line map. Excluded
            # records never enter the typed native reader, even when uncastable.
            with path.open("rb") as handle, admitted.open("wb") as output:
                wanted = iter(lines)
                next_line = next(wanted, None)
                line_number = 0
                while raw := handle.readline(MAX_RECORD_BYTES + 1):
                    line_number += 1
                    if len(raw) > MAX_RECORD_BYTES:
                        raise AnalyticsFileChangedDuringPublishError(
                            f"{item.canonical_uri}: file changed during normalization"
                        )
                    if line_number == next_line:
                        if line_number == 1:
                            raw = raw.removeprefix(b"\xef\xbb\xbf")
                        output.write(raw.rstrip(b"\r\n") + b"\n")
                        next_line = next(wanted, None)
            if _revision(path) != initial:
                raise AnalyticsFileChangedDuringPublishError(
                    f"{item.canonical_uri}: file changed during normalization"
                )
            read_path = admitted
        yield AdmittedFile(item, read_path, lines, summary, initial)



def native_failure_message(exc, admitted_files):
    """Translate normalized-reader record positions back to source physical lines."""
    message = str(exc)
    for admitted in admitted_files:
        path = str(admitted.read_path)
        if path not in message:
            continue
        # These supported DuckDB 1.5.x readers report the current record as
        # completed (reported record index +1), including source blank or
        # excluded lines. Translate only that qualified error shape; other
        # conversion errors retain their native details.
        if (
            duckdb.__version__ in _MALFORMED_JSON_LINE_MAP_VERSIONS
            and "Malformed JSON in file" in message
        ):
            def original_line(match):
                ordinal = int(match.group("line")) - 1
                if 1 <= ordinal <= len(admitted.lines):
                    return f"{match.group('prefix')}line {admitted.lines[ordinal - 1]}"
                return match.group(0)
            location = re.compile(
                rf'(?P<prefix>Malformed JSON in file "{re.escape(path)}", '
                r'at byte \d+ in )line (?P<line>\d+)'
            )
            message = location.sub(original_line, message, count=1)
        message = message.replace(path, admitted.item.canonical_uri)
    return message


def load_file(
    conn,
    item,
    *,
    relation: str,
    expected_type: str,
    filename_column: str = "filename",
    mapping: dict[str, str] | None = None,
) -> dict:
    """Single-file adapter; batch staging reuses the same admission owner."""
    with admit_file(item, expected_type=expected_type) as admitted:
        if admitted.lines:
            mapping = mapping if mapping is not None else root_column_types(expected_type)
            aliases = ", ".join(quote_identifier(f"__dc_payload_{i}") for i in range(len(mapping)))
            projection = ", ".join(
                f'{quote_identifier(f"__dc_payload_{i}")} AS {quote_identifier(name)}'
                for i, name in enumerate(mapping)
            )
            try:
                conn.execute(
                    f"INSERT INTO {relation} BY NAME SELECT {projection}, "
                    f"list_extract(?::BIGINT[], __dc_ordinal) AS _record_line, ? AS {quote_identifier(filename_column)} "
                    "FROM read_json(?, columns=?, auto_detect=false, format='newline_delimited', "
                    "hive_partitioning=false, ignore_errors=false, maximum_object_size=16777216) "
                    f"WITH ORDINALITY AS r({aliases}, __dc_ordinal)",
                    [list(admitted.lines), item.read_uri, str(admitted.read_path), mapping],
                )
            except duckdb.Error as exc:
                raise AnalyticsSchemaIncompatibleError(
                    f"{item.canonical_uri}: native load failed: {native_failure_message(exc, [admitted])}"
                ) from exc
            admitted.assert_stable("native load")
            count = conn.execute(
                f"SELECT count(*) FROM {relation} WHERE {quote_identifier(filename_column)} = ?", [item.read_uri]
            ).fetchone()[0]
            if count != len(admitted.lines):
                raise AnalyticsSchemaIncompatibleError(
                    f"{item.canonical_uri}: native row counts do not reconcile"
                )
        return admitted.summary

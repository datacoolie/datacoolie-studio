from __future__ import annotations

import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest

from datacoolie_studio.domains.analytics import native_loading, staging
from datacoolie_studio.domains.analytics.contracts import LogFileDescriptor
from datacoolie_studio.domains.analytics.errors import (
    AnalyticsFileChangedDuringPublishError,
    AnalyticsInvalidJsonError,
    AnalyticsSchemaIncompatibleError,
)
from datacoolie_studio.domains.analytics.log_contract import admit_log_record
from datacoolie_studio.domains.analytics.record_codec import root_column_types
from datacoolie_studio.domains.analytics.schema_compiler import MappingNode
from datacoolie_studio.domains.analytics.sql import quote_identifier
from tests.support.v6_fixture import deepcopy_v6_rows


def _descriptor(path: Path, file_kind: str, *, canonical_uri: str | None = None) -> LogFileDescriptor:
    stat = path.stat()
    return LogFileDescriptor(
        canonical_uri=canonical_uri or f"canonical://{path.name}",
        file_kind=file_kind,
        revision_json=json.dumps({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}),
        read_uri=str(path),
    )


def _create_stage_table(connection: duckdb.DuckDBPyConnection, kind: str, name: str = "native_stage") -> str:
    columns = {
        **root_column_types(kind),
        "_record_line": "BIGINT",
        "filename": "VARCHAR",
    }
    connection.execute(
        f"CREATE TEMP TABLE {quote_identifier(name)} ("
        + ", ".join(f"{quote_identifier(column)} {dtype}" for column, dtype in columns.items())
        + ")"
    )
    return name


def _load_one(
    path: Path,
    *,
    expected_type: str = "dataflow_run_log",
    file_kind: str = "dataflow_json",
    canonical_uri: str = "canonical://fixture",
) -> tuple[duckdb.DuckDBPyConnection, dict[str, Any]]:
    connection = duckdb.connect()
    relation = _create_stage_table(connection, expected_type, "native_stage")
    summary = native_loading.load_file(
        connection,
        _descriptor(path, file_kind, canonical_uri=canonical_uri),
        relation=relation,
        expected_type=expected_type,
    )
    return connection, summary


def _json_line(row: dict[str, Any]) -> bytes:
    return json.dumps(row, separators=(",", ":")).encode("utf-8")


def test_physical_lines_survive_bom_blank_crlf_exclusion_and_missing_final_newline(
    tmp_path: Path,
) -> None:
    first = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    first["dataflow_run_id"] = "physical-line-first"
    excluded = copy.deepcopy(first)
    excluded["dataflow_run_id"] = "unsupported-v5"
    excluded["log_schema_version"] = 5
    second = copy.deepcopy(first)
    second["dataflow_run_id"] = "physical-line-second"
    payload = b"\xef\xbb\xbf" + _json_line(first) + b"\r\n\r\n" + _json_line(excluded)
    payload += b"\r\n" + _json_line(second)
    path = tmp_path / "physical.jsonl"
    path.write_bytes(payload)

    connection, summary = _load_one(path)
    try:
        assert summary == {
            "admitted": 2,
            "excluded": 1,
            "versions": {"6": 2, "5": 1},
            "locations": [{"line": 3, "version": "5"}],
        }
        rows = connection.execute(
            "SELECT dataflow_run_id, _record_line FROM native_stage ORDER BY _record_line"
        ).fetchall()
        assert rows == [("physical-line-first", 1), ("physical-line-second", 4)]
    finally:
        connection.close()


def test_direct_native_read_accepts_a_valid_record_without_final_newline(tmp_path: Path) -> None:
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    row["dataflow_run_id"] = "no-final-newline"
    path = tmp_path / "no-final-newline.jsonl"
    path.write_bytes(_json_line(row))

    connection, summary = _load_one(path)
    try:
        assert summary["admitted"] == 1
        assert connection.execute(
            "SELECT dataflow_run_id, _record_line FROM native_stage"
        ).fetchone() == ("no-final-newline", 1)
    finally:
        connection.close()


def test_multiple_files_keep_independent_physical_line_maps_and_source_identity(
    tmp_path: Path,
) -> None:
    paths: list[Path] = []
    for index in range(2):
        rows = deepcopy_v6_rows("dataflow_snapshot.json")[:2]
        for row_number, row in enumerate(rows):
            row["dataflow_run_id"] = f"file-{index}-row-{row_number}"
        path = tmp_path / f"source-{index}.jsonl"
        path.write_bytes(b"\n" + _json_line(rows[0]) + b"\r\n\r\n" + _json_line(rows[1]))
        paths.append(path)

    connection = duckdb.connect()
    try:
        items = tuple(
            _descriptor(path, "dataflow_json", canonical_uri=f"canonical://source-{index}")
            for index, path in enumerate(paths)
        )
        relations, tables, views = staging.stage_dataflow_relations(
            connection,
            items,
            stage_index=41,
        )
        assert set(relations) == {0, 1}
        assert len(views) == 2
        raw_rows = connection.execute(
            f"SELECT filename, dataflow_run_id, _record_line FROM {quote_identifier(tables[0])} "
            "ORDER BY filename, _record_line"
        ).fetchall()
        assert raw_rows == [
            (str(paths[0]), "file-0-row-0", 2),
            (str(paths[0]), "file-0-row-1", 4),
            (str(paths[1]), "file-1-row-0", 2),
            (str(paths[1]), "file-1-row-1", 4),
        ]
        for index, view in enumerate(views):
            assert connection.execute(
                f"SELECT dataflow_run_id, _record_line FROM {quote_identifier(view)} "
                "ORDER BY _record_line"
            ).fetchall() == [
                (f"file-{index}-row-0", 2),
                (f"file-{index}-row-1", 4),
            ]
    finally:
        connection.close()


def test_unsupported_version_is_excluded_before_unsafe_native_cast(tmp_path: Path) -> None:
    valid = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    valid["dataflow_run_id"] = "admitted-v6"
    old = copy.deepcopy(valid)
    old["log_schema_version"] = 5
    old["dataflow_run_id"] = "excluded-v5"
    old["metrics"]["rows_read"] = {"not": "an integer"}
    old["duration_seconds"] = {"not": "a double"}
    path = tmp_path / "mixed-version.jsonl"
    path.write_bytes(_json_line(old) + b"\n" + _json_line(valid) + b"\n")

    connection, summary = _load_one(path)
    try:
        assert summary["admitted"] == 1
        assert summary["excluded"] == 1
        assert summary["versions"] == {"5": 1, "6": 1}
        assert connection.execute(
            "SELECT dataflow_run_id, _record_line FROM native_stage"
        ).fetchall() == [("admitted-v6", 2)]
    finally:
        connection.close()


def test_optional_structs_lists_and_nested_members_preserve_nulls_and_order(
    tmp_path: Path,
) -> None:
    base = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    base["read_request"] = None
    base.pop("workspace_id", None)
    attempts = copy.deepcopy(base["attempts"])
    attempts[0]["attempt_number"] = 9
    attempts[0].pop("message", None)
    attempts[0]["source"]["source_action"] = None
    attempts.append(copy.deepcopy(attempts[0]))
    attempts[1]["attempt_number"] = 3
    base["attempts"] = attempts

    omitted = copy.deepcopy(base)
    omitted["dataflow_run_id"] = "optional-omitted"
    omitted.pop("diagnostic_issues", None)
    empty_list = copy.deepcopy(base)
    empty_list["dataflow_run_id"] = "optional-empty"
    empty_list["diagnostic_issues"] = []
    path = tmp_path / "optional-shapes.jsonl"
    path.write_bytes(b"\n".join(_json_line(row) for row in (omitted, empty_list)))

    connection, summary = _load_one(path)
    try:
        assert summary["admitted"] == 2
        rows = connection.execute(
            "SELECT dataflow_run_id, workspace_id, read_request, diagnostic_issues, attempts "
            "FROM native_stage ORDER BY dataflow_run_id"
        ).fetchall()
        assert [row[0] for row in rows] == [
            "optional-empty",
            "optional-omitted",
        ]
        assert all(row[1] is None and row[2] is None for row in rows)
        assert rows[0][3] == []
        assert rows[1][3] is None
        for row in rows:
            assert [attempt["attempt_number"] for attempt in row[4]] == [9, 3]
            assert row[4][0]["message"] is None
            assert row[4][0]["source"]["source_action"] is None
    finally:
        connection.close()


def test_read_json_ordinality_stays_global_with_four_threads_for_large_bare_mapping(
    tmp_path: Path,
    monkeypatch,
) -> None:
    row_count = 130_000
    path = tmp_path / "large-bare.jsonl"
    with path.open("wb") as output:
        for value in range(row_count):
            output.write(f'{{"value":{value}}}\n'.encode("ascii"))

    mapping = {"value": MappingNode("value", "BIGINT", "integer", True)}
    monkeypatch.setattr(native_loading, "NUMERIC_RANGE_MAPPINGS", {"dataflow": mapping, "job": mapping})
    monkeypatch.setattr(native_loading, "root_column_types", lambda _kind: {"value": "BIGINT"})
    monkeypatch.setattr(
        native_loading,
        "admit_log_record",
        lambda _record, *, expected_type, uri, line: True,
    )
    connection = duckdb.connect()
    try:
        connection.execute("SET threads = 4")
        connection.execute(
            "CREATE TEMP TABLE native_stage (value BIGINT, _record_line BIGINT, filename VARCHAR)"
        )
        summary = native_loading.load_file(
            connection,
            _descriptor(path, "dataflow_json", canonical_uri="canonical://large-bare"),
            relation="native_stage",
            expected_type="dataflow_run_log",
        )
        assert connection.execute("SELECT current_setting('threads')").fetchone() == (4,)
        assert summary["admitted"] == row_count
        assert connection.execute("SELECT count(*) FROM native_stage").fetchone() == (row_count,)
        assert connection.execute(
            "SELECT count(*) FROM native_stage WHERE _record_line <> value + 1"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT min(_record_line), max(_record_line) FROM native_stage"
        ).fetchone() == (1, row_count)
    finally:
        connection.close()


def test_rust_validator_cache_is_reusable_for_concurrent_decimal_admission() -> None:
    rows = []
    for index in range(256):
        row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
        row["dataflow_run_id"] = f"concurrent-validator-{index}"
        row["metrics"]["rows_read"] = Decimal("1e30")
        rows.append(row)

    def admit(index_and_row: tuple[int, dict[str, Any]]) -> bool:
        index, row = index_and_row
        return admit_log_record(
            row,
            expected_type="dataflow_run_log",
            uri="canonical://concurrent-validator",
            line=index + 1,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(admit, enumerate(rows)))
    assert results == [True] * len(rows)


@pytest.mark.parametrize(
    ("name", "mutate", "path_fragment", "message_fragment"),
    [
        (
            "unknown-root",
            lambda row: row.update(unmodeled_extra={"region": "apac"}),
            "/",
            "unmodeled_extra",
        ),
        (
            "wrong-type",
            lambda row: row.update(datacoolie_version=7),
            "/datacoolie_version",
            "not of type",
        ),
        (
            "enum",
            lambda row: row.update(status="not-a-status"),
            "/status",
            "not one",
        ),
        (
            "format",
            lambda row: row["read_request"]["read_range"]["start"].update(
                __datetime__="not-a-date"
            ),
            "/read_request/read_range/start",
            "not valid",
        ),
        (
            "required",
            lambda row: row.pop("metrics"),
            "/",
            "required property",
        ),
    ],
)
def test_current_admission_errors_identify_canonical_uri_line_and_path(
    name: str,
    mutate,
    path_fragment: str,
    message_fragment: str,
) -> None:
    del name
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    mutate(row)
    with pytest.raises(AnalyticsSchemaIncompatibleError) as raised:
        admit_log_record(
            row,
            expected_type="dataflow_run_log",
            uri="canonical://logs/dataflow.jsonl",
            line=37,
        )
    message = str(raised.value)
    assert message.startswith("canonical://logs/dataflow.jsonl:37:")
    assert f"at {path_fragment}:" in message
    assert message_fragment in message


@pytest.mark.parametrize("token", ["1", "1.0", "1e0"])
def test_integral_json_tokens_are_equivalent_for_native_integer_fields(
    tmp_path: Path,
    token: str,
) -> None:
    row = deepcopy_v6_rows("job_snapshot.json")[0]
    payload = json.dumps(row, separators=(",", ":"))
    payload = re.sub(r'("job_num":)[^,}]+', rf"\g<1>{token}", payload, count=1)
    path = tmp_path / f"job-{token.replace('.', '_')}.jsonl"
    path.write_text(payload + "\n", encoding="utf-8")

    connection, summary = _load_one(
        path,
        expected_type="job_run_log",
        file_kind="job_json",
        canonical_uri=f"canonical://job-{token}",
    )
    try:
        assert summary["admitted"] == 1
        assert connection.execute("SELECT job_num FROM native_stage").fetchone() == (1,)
    finally:
        connection.close()


def test_native_integer_accepts_decimal_exponent_with_documented_rounding(
    tmp_path: Path,
) -> None:
    row = deepcopy_v6_rows("job_snapshot.json")[0]
    payload = json.dumps(row, separators=(",", ":"))
    payload = re.sub(r'("job_num":)[^,}]+', r"\g<1>1e30", payload, count=1)
    path = tmp_path / "job-decimal-exponent.jsonl"
    path.write_text(payload + "\n", encoding="utf-8")

    connection, summary = _load_one(
        path,
        expected_type="job_run_log",
        file_kind="job_json",
        canonical_uri="canonical://job-decimal-exponent",
    )
    try:
        assert summary["admitted"] == 1
        stored = connection.execute("SELECT job_num FROM native_stage").fetchone()[0]
        assert isinstance(stored, int)
        assert 10**29 < stored < 10**31
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("field", "raw_value", "path_fragment"),
    [
        ("job_num", str(2**127), "/job_num"),
        ("duration_seconds", "1e400", "/duration_seconds"),
    ],
)
def test_native_numeric_limits_fail_with_location(
    tmp_path: Path,
    field: str,
    raw_value: str,
    path_fragment: str,
) -> None:
    row = deepcopy_v6_rows("job_snapshot.json")[0]
    payload = json.dumps(row, separators=(",", ":"))
    payload = re.sub(rf'("{field}":)[^,}}]+', rf"\g<1>{raw_value}", payload, count=1)
    path = tmp_path / f"overflow-{field}.jsonl"
    path.write_text(payload + "\n", encoding="utf-8")

    with pytest.raises(AnalyticsSchemaIncompatibleError) as raised:
        _load_one(
            path,
            expected_type="job_run_log",
            file_kind="job_json",
            canonical_uri=f"canonical://overflow/{field}",
        )
    message = str(raised.value)
    assert f"canonical://overflow/{field}:1:" in message
    assert f"at {path_fragment}:" in message


def test_oversized_record_is_rejected_before_unbounded_json_parse(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(native_loading, "MAX_RECORD_BYTES", 128)
    path = tmp_path / "oversized.jsonl"
    path.write_bytes(b"{" + b"x" * 128 + b"}\n")

    with pytest.raises(AnalyticsInvalidJsonError, match=r"canonical://oversized:1: record exceeds 128"):
        _load_one(path, canonical_uri="canonical://oversized")


def test_file_mutation_after_admission_aborts_before_native_insert(tmp_path: Path, monkeypatch) -> None:
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    path = tmp_path / "mutating.jsonl"
    path.write_bytes(_json_line(row) + b"\n")
    original_admit = native_loading.admit_log_record
    mutated = False

    def admit_and_mutate(record, *, expected_type: str, uri: str, line: int) -> bool:
        nonlocal mutated
        admitted = original_admit(record, expected_type=expected_type, uri=uri, line=line)
        if admitted and not mutated:
            with path.open("ab") as handle:
                handle.write(b"\n")
            mutated = True
        return admitted

    monkeypatch.setattr(native_loading, "admit_log_record", admit_and_mutate)
    with pytest.raises(AnalyticsFileChangedDuringPublishError, match="canonical://mutating"):
        _load_one(path, canonical_uri="canonical://mutating")


def test_file_mutation_observed_after_native_reader_is_fenced_by_caller_transaction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    path = tmp_path / "mutating-after-validation.jsonl"
    path.write_bytes(_json_line(row) + b"\n")
    connection = duckdb.connect()
    _create_stage_table(connection, "dataflow_run_log")
    initial = native_loading._revision(path)
    revision_calls = 0

    def mutate_after_validation(candidate: Path) -> tuple[int, int]:
        nonlocal revision_calls
        revision_calls += 1
        if revision_calls == 2:
            with candidate.open("ab") as handle:
                handle.write(b"\n")
            return initial
        if revision_calls == 3:
            return (initial[0] + 1, initial[1])
        return initial

    monkeypatch.setattr(native_loading, "_revision", mutate_after_validation)
    try:
        connection.execute("BEGIN")
        with pytest.raises(AnalyticsFileChangedDuringPublishError, match="native load"):
            native_loading.load_file(
                connection,
                _descriptor(path, "dataflow_json", canonical_uri="canonical://mutating-after-validation"),
                relation="native_stage",
                expected_type="dataflow_run_log",
            )
        assert revision_calls == 3
        assert connection.execute("SELECT count(*) FROM native_stage").fetchone() == (1,)
        connection.execute("ROLLBACK")
        assert connection.execute("SELECT count(*) FROM native_stage").fetchone() == (0,)
    finally:
        if revision_calls and connection.execute("SELECT count(*) FROM native_stage").fetchone() != (0,):
            connection.execute("ROLLBACK")
        connection.close()


def test_staging_drops_transient_relations_when_admission_fails(tmp_path: Path) -> None:
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    row["unmodeled_extra"] = "reject me"
    path = tmp_path / "invalid.jsonl"
    path.write_bytes(_json_line(row) + b"\n")
    connection = duckdb.connect()
    try:
        with pytest.raises(AnalyticsSchemaIncompatibleError):
            staging.stage_dataflow_relations(
                connection,
                (_descriptor(path, "dataflow_json", canonical_uri="canonical://invalid"),),
                stage_index=93,
            )
        remaining = connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name LIKE '__dc_dataflow_batch_93%'"
        ).fetchall()
        assert remaining == []
    finally:
        connection.close()



@pytest.mark.parametrize("threads", [1, 4])
def test_native_batch_full_schema_physical_provenance_and_lazy_normalization(tmp_path, monkeypatch, threads):
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    ordinary = tmp_path / "ordinary's log.ndjson"
    normalized = tmp_path / "normalized.ndjson"
    empty = tmp_path / "empty.ndjson"
    excluded = {**row, "log_schema_version": 5}
    ordinary.write_bytes(b"\n" + _json_line({**row, "dataflow_run_id": "first"}) + b"\r\n\r\n" + _json_line({**row, "dataflow_run_id": "second"}))
    normalized.write_bytes(b"\xef\xbb\xbf" + _json_line(excluded) + b"\n\n" + _json_line({**row, "dataflow_run_id": "normalized"}) + b"\n")
    empty.write_bytes(_json_line(excluded) + b"\n")
    items = [_descriptor(p, "dataflow_json") for p in (empty, normalized, ordinary)]
    created = []
    original = native_loading.tempfile.TemporaryDirectory
    def tracked(*args, **kwargs):
        result = original(*args, **kwargs)
        created.append(Path(result.name))
        return result
    monkeypatch.setattr(native_loading.tempfile, "TemporaryDirectory", tracked)
    conn = duckdb.connect()
    try:
        conn.execute(f"SET threads={threads}")
        stage = staging.stage_native_batch(conn, items, kind="dataflow", stage_index=71)
        rows = conn.execute(f"SELECT s.dataflow_run_id,s._record_line,m.canonical_uri FROM {stage.relation} s JOIN {stage.metadata} m ON s.{quote_identifier(stage.file_key_column)}=m.file_key ORDER BY s.dataflow_run_id").fetchall()
        assert rows == [("first", 2, items[2].canonical_uri), ("normalized", 3, items[1].canonical_uri), ("second", 4, items[2].canonical_uri)]
        assert stage.counts == (0, 1, 2)
        assert [x["excluded"] for x in stage.summaries] == [1, 1, 0]
        assert len(created) == 1
        assert all(not p.exists() for p in created)
    finally:
        conn.close()


def test_native_batch_fallback_preserves_distinct_descriptors_for_same_read_path(tmp_path, monkeypatch):
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    path = tmp_path / "same.ndjson"
    path.write_bytes(_json_line(row) + b"\n")
    items = [_descriptor(path, "dataflow_json", canonical_uri=f"canonical://copy-{i}") for i in range(2)]
    conn = duckdb.connect()
    try:
        stage = staging.stage_native_batch(conn, items, kind="dataflow", stage_index=72)
        assert stage.counts == (1, 1)
        assert conn.execute(f"SELECT {quote_identifier(stage.file_key_column)},_record_line FROM {stage.relation} ORDER BY 1").fetchall() == [(0,1),(1,1)]
    finally:
        conn.close()


@pytest.mark.parametrize("threads", [1, 4])
def test_native_batch_large_full_schema_preserves_every_record(tmp_path, threads):
    template = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    template["attempts"] = []
    template["diagnostic_issues"] = []
    template["read_request"] = None
    paths = []
    for file_index in range(3):
        path = tmp_path / f"large-{file_index}.ndjson"
        paths.append(path)
        with path.open("wb") as output:
            for i in range(45000):
                output.write(_json_line({**template, "dataflow_run_id": f"{file_index}:{i}"}) + b"\n")
    items = [_descriptor(p, "dataflow_json") for p in reversed(paths)]
    conn = duckdb.connect()
    try:
        conn.execute(f"SET threads={threads}")
        conn.execute("SET memory_limit='512MiB'")
        stage = staging.stage_native_batch(conn, items, kind="dataflow", stage_index=73)
        assert stage.counts == (45000,45000,45000)
        # Check record identity -> file and original line for ALL 135000 rows.
        mismatch = conn.execute(f"SELECT count(*) FROM {stage.relation} WHERE split_part(dataflow_run_id, ':', 2)::BIGINT+1 != _record_line OR split_part(dataflow_run_id, ':', 1)::BIGINT != 2-{quote_identifier(stage.file_key_column)}").fetchone()[0]
        assert mismatch == 0
    finally:
        conn.close()


@pytest.mark.parametrize("variant", ["slashes", "version", "order"])
def test_native_batch_fallback_for_alias_or_unqualified_reader(tmp_path, monkeypatch, variant):
    path = tmp_path / "alias.ndjson"
    path.write_bytes(_json_line(deepcopy_v6_rows("dataflow_snapshot.json")[0]) + b"\n")
    items = [_descriptor(path, "dataflow_json", canonical_uri="canonical://a")]
    if variant == "slashes":
        items.append(LogFileDescriptor("canonical://b", "dataflow_json", "{}", path.as_posix()))
    elif variant == "version":
        monkeypatch.setattr(staging.duckdb, "__version__", "unqualified")
    conn = duckdb.connect()
    try:
        if variant == "order":
            conn.execute("SET preserve_insertion_order=false")
        stage = staging.stage_native_batch(conn, items, kind="dataflow", stage_index=74)
        assert stage.counts == tuple(1 for item in items)
    finally:
        conn.close()


def test_direct_insert_rejects_target_identity_type_narrowing(tmp_path):
    from datacoolie_studio.domains.analytics import schema, schema_tables, store
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    row["dataflow_run_id"] = "123"
    path = tmp_path / "numeric-string.ndjson"
    path.write_bytes(_json_line(row) + b"\n")
    conn = duckdb.connect()
    try:
        schema_tables.ensure_tables(conn)
        conn.execute(f"ALTER TABLE {schema.DATAFLOW_TABLE} ALTER dataflow_run_id TYPE BIGINT")
        with pytest.raises(AnalyticsSchemaIncompatibleError, match="requires.*rebuild"):
            store.insert_dataflow_file(conn, 1, str(path), "dataflow_json", "{}")
        assert conn.execute(f"SELECT count(*) FROM {schema.DATAFLOW_TABLE}").fetchone() == (0,)
    finally:
        conn.close()


def test_native_batch_internal_aliases_are_case_insensitive_and_preserve_payload(tmp_path, monkeypatch):
    from datacoolie_studio.domains.analytics import record_codec
    extra = {"__DC_FILE_KEY": "HUGEINT", "__DC_READER_FILENAME": "VARCHAR", "filename": "VARCHAR", "ordinality": "HUGEINT", "__dc_payload_0": "VARCHAR"}
    mapping = {**root_column_types("dataflow_run_log"), **extra}
    row = {**deepcopy_v6_rows("dataflow_snapshot.json")[0], "__DC_FILE_KEY": 19, "__DC_READER_FILENAME": "producer", "filename": "source-value", "ordinality": 42, "__dc_payload_0": "payload"}
    path = tmp_path / "alias-payload.ndjson"
    path.write_bytes(_json_line(row) + b"\n")
    original_admit = native_loading.admit_log_record
    def admit(record, **kwargs):
        return original_admit({k:v for k,v in record.items() if k not in extra}, **kwargs)
    monkeypatch.setattr(native_loading, "admit_log_record", admit)
    monkeypatch.setattr(record_codec, "root_column_types", lambda kind: mapping)
    # Exercise the fallback used by the locked DuckDB 1.5.5 runtime;
    # custom/internal columns must survive it too.
    monkeypatch.setattr(staging.duckdb, "__version__", "1.5.5")
    conn = duckdb.connect()
    try:
        stage = staging.stage_native_batch(conn, [_descriptor(path,"dataflow_json")], kind="dataflow", stage_index=75)
        assert stage.file_key_column.casefold() != "__dc_file_key"
        assert conn.execute(f'SELECT "__DC_FILE_KEY", "__DC_READER_FILENAME", filename, ordinality, __dc_payload_0, _record_line FROM {stage.relation}').fetchone() == (19,"producer","source-value",42,"payload",1)
    finally:
        conn.close()


def test_native_batch_conversion_failure_reports_original_line_after_normalization(tmp_path):
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    valid = _json_line(row)
    invalid = _json_line({**row, "dataflow_run_id": "\ud800"})
    path = tmp_path / "surrogate.ndjson"
    path.write_bytes(b"\n" + valid + b"\n" + _json_line({**row, "log_schema_version":5}) + b"\n\n" + invalid + b"\n")
    conn = duckdb.connect()
    try:
        with pytest.raises(AnalyticsSchemaIncompatibleError, match="canonical://original.*line 5"):
            staging.stage_native_batch(conn, [_descriptor(path,"dataflow_json",canonical_uri="canonical://original")], kind="dataflow",stage_index=76)
        assert conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_name LIKE '__dc_dataflow_batch_76%'").fetchone() == (0,)
    finally:
        conn.close()


@pytest.mark.parametrize("layout", ["direct-blank", "bom", "excluded"])
def test_native_batch_malformed_json_maps_original_line_for_direct_and_normalized_reads(
    tmp_path,
    layout,
):
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    valid = _json_line(row)
    invalid = _json_line({**row, "dataflow_run_id": "\ud800"})
    if layout == "direct-blank":
        payload = b"\n" + valid + b"\n\n" + invalid + b"\n"
        expected_line = 4
    elif layout == "bom":
        payload = b"\xef\xbb\xbf" + valid + b"\n" + invalid + b"\n"
        expected_line = 2
    else:
        excluded = _json_line({**row, "log_schema_version": 5})
        payload = valid + b"\n" + excluded + b"\n" + invalid + b"\n"
        expected_line = 3
    path = tmp_path / f"{layout}.ndjson"
    path.write_bytes(payload)
    conn = duckdb.connect()
    try:
        with pytest.raises(
            AnalyticsSchemaIncompatibleError,
            match=rf"canonical://{layout}.*line {expected_line}",
        ):
            staging.stage_native_batch(
                conn,
                [_descriptor(path, "dataflow_json", canonical_uri=f"canonical://{layout}")],
                kind="dataflow",
                stage_index={"direct-blank": 79, "bom": 80, "excluded": 81}[layout],
            )
    finally:
        conn.close()


def test_native_failure_message_preserves_unknown_runtime_location(tmp_path, monkeypatch):
    from array import array
    from types import SimpleNamespace

    path = tmp_path / "unknown-runtime.ndjson"
    path.write_bytes(b"placeholder")
    item = SimpleNamespace(
        read_uri=str(path),
        canonical_uri="canonical://unknown-runtime",
    )
    admitted = SimpleNamespace(
        item=item,
        read_path=path,
        lines=array("q", [4]),
    )
    message = (
        f'Malformed JSON in file "{path}", at byte 7 in line 3: '
        "context mentions line 99"
    )
    monkeypatch.setattr(native_loading.duckdb, "__version__", "unknown")

    rendered = native_loading.native_failure_message(RuntimeError(message), [admitted])

    assert "canonical://unknown-runtime" in rendered
    assert "line 3" in rendered
    assert "line 99" in rendered
    assert str(path) not in rendered


def test_batch_ingested_timestamp_order_preserves_file_latest_winner_when_clock_ticks_equal(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from datacoolie_studio.domains.analytics import schema_tables, store
    fixed = datetime(2026,10,10,tzinfo=timezone.utc)
    monkeypatch.setattr(store, "utc_now", lambda: fixed)
    row = deepcopy_v6_rows("dataflow_snapshot.json")[0]
    paths=[]
    for i in range(3):
        path = tmp_path / f"clock-{i}.ndjson"
        path.write_bytes(_json_line({**row,"dataflow_run_id":f"run-{i}"})+b"\n")
        paths.append(path)
    items=[_descriptor(p,"dataflow_json") for p in paths]
    conn=duckdb.connect()
    try:
        schema_tables.ensure_tables(conn)
        stage=staging.stage_native_batch(conn,items,kind="dataflow",stage_index=77)
        store.insert_native_batch(conn,1,stage,kind="dataflow")
        values=conn.execute("SELECT _ingested_at FROM etl_dataflow_runs ORDER BY _file_uri").fetchall()
        assert values[0][0] < values[1][0] < values[2][0]
        assert conn.execute("SELECT dataflow_run_id FROM etl_dataflow_runs ORDER BY _ingested_at DESC LIMIT 1").fetchone() == ("run-2",)
    finally:
        conn.close()


def test_native_batch_physical_map_mismatch_fails_and_cleans_stage(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from array import array
    path=tmp_path/"count-mismatch.ndjson"
    path.write_bytes(_json_line(deepcopy_v6_rows("dataflow_snapshot.json")[0])+b"\n")
    original=native_loading.admit_file
    @contextmanager
    def mismatch(item, **kwargs):
        with original(item, **kwargs) as entry:
            entry.lines = array("q", [1,2])
            entry.summary["admitted"]=2
            yield entry
    monkeypatch.setattr(native_loading,"admit_file",mismatch)
    conn=duckdb.connect()
    try:
        with pytest.raises(AnalyticsSchemaIncompatibleError,match="counts do not reconcile"):
            staging.stage_native_batch(conn,[_descriptor(path,"dataflow_json")],kind="dataflow",stage_index=78)
        assert conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_name LIKE '__dc_dataflow_batch_78%'").fetchone() == (0,)
    finally:
        conn.close()

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics import schema_tables as analytics_schema_tables
from datacoolie_studio.domains.analytics import store as analytics_store
from datacoolie_studio.domains.read_models.contracts import ResultCacheKey
from datacoolie_studio.domains.read_models.database import reset_result_cache_engine
from datacoolie_studio.domains.read_models.sqlite_store import (
    SqliteResultCacheStore,
    clear_memory_cache,
)
from datacoolie_studio.domains.monitoring import evidence_hydration as hydration
from tests.support.v6_records import native_record


class _Result:
    description = [
        ("_source_id",),
        ("_file_uri",),
        ("_record_line",),
        ("_file_kind",),
        ("dataflow_id",),
        ("duration_seconds",),
        ("attempts",),
    ]

    def __init__(self, row: tuple[object, ...]):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row]


class _Connection:
    def __init__(self, row: tuple[object, ...]):
        self.row = row
        self.calls: list[tuple[str, object]] = []

    def execute(self, sql: str, parameters=None):
        self.calls.append((sql, parameters))
        return _Result(self.row)


def _context(connection: _Connection):
    return connection, [7], "generation-1"


def _source_connection() -> _Connection:
    return _Connection(
        (
            7,
            "logs/dataflow.jsonl",
            4,
            "dataflow_run_log",
            "base-run",
            1.25,
            [],
        )
    )


def _source_row(row, _kind):
    return {
        "dataflow_id": row["dataflow_id"],
        "duration_seconds": row["duration_seconds"],
        "attempts": row["attempts"],
    }


def test_cache_payload_removes_evidence_and_native_root_containers():
    payload = {
        "records": [
            {
                "_source_id": 7,
                "_file_uri": "logs/dataflow.jsonl",
                "_record_line": 4,
                "dataflow_id": "run-1",
                "raw_record_json": '{"metrics":{"rows_read":9007199254740993}}',
                "metrics": {"rows_read": 9007199254740993},
                "attempts": [{"attempt_number": 1}],
            }
        ],
        "summary": {"raw_record_json": "should-never-persist"},
    }

    cached = hydration.cache_payload(
        payload,
        generation="generation-1",
        input_fingerprint="fingerprint-1",
    )

    record = cached["records"][0]
    assert record["raw_record_json"] is None
    assert "metrics" not in record
    assert "attempts" not in record
    assert cached["summary"]["raw_record_json"] is None
    assert cached[hydration.CACHE_GENERATION_FIELD] == "generation-1"
    assert cached[hydration.CACHE_INPUT_FINGERPRINT_FIELD] == "fingerprint-1"


def test_generation_mismatch_rejects_before_source_lookup(monkeypatch):
    connection = _source_connection()
    payload = hydration.cache_payload(
        {"records": [{"raw_record_json": None, "dataflow_id": "run-1"}]},
        generation="old-generation",
        input_fingerprint="fingerprint-1",
    )

    def fail_codec(*_args, **_kwargs):
        raise AssertionError("codec must not run for a stale cache")

    monkeypatch.setattr(hydration, "reconstruct_record", fail_codec)
    with pytest.raises(hydration.EvidenceGenerationMismatch):
        hydration.hydrate_payload(
            payload,
            _context(connection),
            expected_generation="generation-1",
            expected_input_fingerprint="fingerprint-1",
        )
    assert connection.calls == []


def test_hydrate_uses_base_row_when_view_projection_has_overridden_values(monkeypatch):
    connection = _source_connection()
    monkeypatch.setattr(
        hydration.analytics_schema,
        "table_columns",
        lambda _connection, _table: [
            "_source_id",
            "_file_uri",
            "_record_line",
            "_file_kind",
            "dataflow_id",
            "duration_seconds",
            "attempts",
        ],
    )

    monkeypatch.setattr(hydration, "reconstruct_record", _source_row)
    monkeypatch.setattr(hydration, "record_json", lambda value: json.dumps(value, separators=(",", ":")))
    result = hydration.hydrate_row(
        {
            "_source_id": 7,
            "_file_uri": "logs/dataflow.jsonl",
            "_record_line": 4,
            "_file_kind": "dataflow_run_log",
            "dataflow_id": "view-run",
            "duration_seconds": 99.0,
            "raw_record_json": None,
        },
        _context(connection),
        kind="dataflow_run_log",
    )

    assert result["raw_record_json"] == '{"dataflow_id":"base-run","duration_seconds":1.25,"attempts":[]}'
    assert len(connection.calls) == 1
    sql, parameters = connection.calls[0]
    assert "FROM etl_dataflow_runs" in sql
    assert parameters == [7, "logs/dataflow.jsonl", 4, "dataflow_run_log"]


def test_attempt_evidence_selects_attempt_number_losslessly(monkeypatch):
    connection = _source_connection()
    monkeypatch.setattr(
        hydration.analytics_schema,
        "table_columns",
        lambda _connection, _table: [
            "_source_id",
            "_file_uri",
            "_record_line",
            "_file_kind",
            "dataflow_id",
            "duration_seconds",
            "attempts",
        ],
    )
    source = {
        "dataflow_id": "base-run",
        "duration_seconds": "1.25",
        "attempts": [
            {"attempt_number": 1, "duration_seconds": "0.5"},
            {"attempt_number": 2, "duration_seconds": "1.25"},
        ],
    }
    monkeypatch.setattr(hydration, "reconstruct_record", lambda _row, _kind: source)
    monkeypatch.setattr(hydration, "record_json", lambda value: json.dumps(value, separators=(",", ":")))

    result = hydration.hydrate_row(
        {
            "_source_id": 7,
            "_file_uri": "logs/dataflow.jsonl",
            "_record_line": 4,
            "_file_kind": "dataflow_run_log",
            "dataflow_id": "view-run",
            "attempt_number": "2",
            "attempt_record_json": None,
        },
        _context(connection),
        kind="dataflow_run_log",
        evidence_fields=("attempt_record_json",),
    )

    assert result["attempt_record_json"] == '{"attempt_number":2,"duration_seconds":"1.25"}'


def test_hydration_has_an_explicit_row_bound():
    rows = [{"raw_record_json": None}, {"raw_record_json": None}]
    with pytest.raises(hydration.EvidenceHydrationLimit):
        hydration.hydrate_rows(rows, (None, [], "generation-1"), max_rows=1)


def test_warm_sqlite_cache_hydrates_native_typed_evidence_without_raw_storage(
    tmp_path, monkeypatch
):
    import duckdb

    cache_url = f"sqlite:///{(tmp_path / 'result-cache.sqlite3').as_posix()}"
    monkeypatch.setenv("DATACOOLIE_STUDIO_RESULT_CACHE_URL", cache_url)
    clear_memory_cache()
    reset_result_cache_engine()

    record = native_record(
        {
            "_type": "dataflow_run_log",
            "dataflow_id": "flow-1",
            "dataflow_run_id": "run-1",
            "job_id": "job-1",
            "status": "succeeded",
        }
    )
    source_duration = Decimal("12345678901234567890.125")
    record["duration_seconds"] = source_duration
    analytics = duckdb.connect(":memory:")
    try:
        analytics_schema_tables.ensure_tables(analytics)
        analytics_schema_tables.ensure_typed_table(
            analytics,
            analytics_schema.DATAFLOW_TABLE,
            analytics_schema.DATAFLOW_COLUMN_TYPES,
        )
        # The URI intentionally has no corresponding source file.  A warm
        # cache hydration must use this typed base row and never reread a raw
        # provider object.
        analytics_store.insert_typed_rows(
            analytics,
            analytics_schema.DATAFLOW_TABLE,
            7,
            [("provider://missing/run.jsonl", "dataflow_jsonl", "{}", record)],
            analytics_schema.DATAFLOW_COLUMN_TYPES,
        )

        response = {
            "records": [
                {
                    "_source_id": 7,
                    "_file_uri": "provider://missing/run.jsonl",
                    "_record_line": 1,
                    "_file_kind": "dataflow_jsonl",
                    "dataflow_id": "flow-1",
                    "raw_record_json": None,
                }
            ]
        }
        key = ResultCacheKey(
            environment_id=1,
            namespace="monitoring.page.dataflows",
            parameters_fingerprint="parameters-1",
            input_fingerprint="fingerprint-1",
            producer_version="monitoring-page-sql-v16",
        )
        cached_payload = hydration.cache_payload(
            response,
            generation="generation-1",
            input_fingerprint=key.input_fingerprint,
        )
        store = SqliteResultCacheStore()
        stored = store.put(key, cached_payload)
        assert stored.stored is True

        # Evict the process-local copy so this is a persistent SQLite warm hit.
        clear_memory_cache()
        warm = store.get(key)
        assert warm is not None
        assert warm.payload["records"][0]["raw_record_json"] is None
        assert "duration_seconds" not in warm.payload["records"][0]
        assert "duration_seconds" not in json.dumps(warm.payload)

        hydrated = hydration.hydrate_payload(
            warm.payload,
            (analytics, [7], "generation-1"),
            expected_generation="generation-1",
            expected_input_fingerprint=key.input_fingerprint,
        )
        evidence = hydrated["records"][0]["raw_record_json"]
        # duration_seconds is a native DOUBLE in the evidence table.  The
        # reconstructed value may round, but it must remain a finite number
        # and the row must still be hydrated from the typed cache.
        assert evidence not in json.dumps(warm.payload)
        decoded = json.loads(evidence, parse_float=Decimal)
        native_duration = Decimal(str(float(source_duration)))
        assert decoded["duration_seconds"] == native_duration
        assert decoded["duration_seconds"] != source_duration

        with pytest.raises(hydration.EvidenceGenerationMismatch):
            hydration.hydrate_payload(
                warm.payload,
                (analytics, [7], "generation-2"),
                expected_generation="generation-2",
                expected_input_fingerprint=key.input_fingerprint,
            )
    finally:
        analytics.close()
        clear_memory_cache()
        reset_result_cache_engine()

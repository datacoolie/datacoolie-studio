from __future__ import annotations

from tests.support.v6_records import insert_native_rows

from datetime import datetime, timezone
from pathlib import Path
import json
import pytest

from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.domains.analytics import access as analytics_access
from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics import cache_state as analytics_cache_state
from datacoolie_studio.domains.analytics import publication as analytics_publication
from datacoolie_studio.domains.analytics import schema_tables as analytics_schema_tables
from datacoolie_studio.domains.analytics import store as analytics_store
from datacoolie_studio.domains.monitoring import page_cache
from datacoolie_studio.domains.monitoring.page_contracts import (
    DATAFLOW_REGISTRY_PRODUCER_VERSION,
    FRESHNESS_REGISTRY_PRODUCER_VERSION,
    JOBS_PAGE_PRODUCER_VERSION,
    PRODUCER_VERSION,
)
from datacoolie_studio.domains.read_models.contracts import ResultCacheKey
from datacoolie_studio.domains.logs import ingestion as log_ingestion
from datacoolie_studio.domains.monitoring import log_repository

from tests.support.v6_records import native_record

from tests.domains.monitoring.monitoring_test_support import (
    PathRecord,
    _normalize_duckdb_type,
)


def _v6_typed_row(file_uri: str, kind: str, row: dict) -> tuple[str, str, str, dict]:
    record = native_record({
        "log_schema_version": 6,
        "_type": "dataflow_run_log" if kind == "dataflow" else "job_run_log",
        "datacoolie_version": "0.2.0",
        "log_session_id": "session-1",
        **row,
    })
    return file_uri, f"{kind}_jsonl", json.dumps(record), record

def test_legacy_duckdb_cache_routes_to_upgrade_owner(tmp_path: Path, monkeypatch):
    import duckdb

    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)
    with duckdb.connect(str(analytics_path)) as connection:
        connection.execute("CREATE TABLE etl_dataflow_run_cache (source_id INTEGER, file_uri VARCHAR, row_json VARCHAR)")
        connection.execute("CREATE TABLE etl_job_run_cache (source_id INTEGER, file_uri VARCHAR, row_json VARCHAR)")

    assert analytics_access.cache_is_ready(analytics_path) is False

    record = native_record({
        "_type": "job_run_log",
        "job_id": "job-1",
        "status": "succeeded",
        "engine_name": "polars",
        "end_time": "2026-06-13T00:01:00+00:00",
        "observed_at": "2026-06-13T00:01:00+00:00",
    })
    result = analytics_store.publish_rows(
        1,
        [],
        [("job.jsonl", "job_jsonl", json.dumps(record), record)],
        [],
        ["job.jsonl"],
        database_path=analytics_path,
    )
    assert result["published"] is False
    assert result["errors"][0]["code"] == "analytics_upgrade_required"

    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        tables = {row[0] for row in connection.execute("show tables").fetchall()}
        assert {"etl_dataflow_run_cache", "etl_job_run_cache"} <= tables
        assert "etl_dataflow_runs" not in tables
        assert "etl_job_runs" not in tables


def test_freshness_page_cache_version_fences_old_semantics_and_etag(monkeypatch):
    monkeypatch.setattr(page_cache, "monitoring_input_fingerprint", lambda *_: "same-input")
    monkeypatch.setattr(page_cache, "monitoring_admission", lambda *_: {"admitted": 1, "excluded": 0})
    current = page_cache.monitoring_page_cache_key(
        object(),
        environment_id=1,
        paths=[],
        page="freshness",
        filters={"range": "30d"},
        timezone_label="UTC",
        now=datetime(2026, 10, 12, tzinfo=timezone.utc),
    )
    previous = ResultCacheKey(
        environment_id=current.environment_id,
        namespace=current.namespace,
        parameters_fingerprint=current.parameters_fingerprint,
        input_fingerprint=current.input_fingerprint,
        producer_version="monitoring-page-sql-v24",
    )

    assert current.producer_version == PRODUCER_VERSION
    assert current.identity != previous.identity
    assert page_cache.monitoring_page_etag(current) != page_cache.monitoring_page_etag(previous)


def test_jobs_page_cache_version_fences_incomplete_workload_operation_lists(monkeypatch):
    monkeypatch.setattr(page_cache, "monitoring_input_fingerprint", lambda *_: "same-input")
    monkeypatch.setattr(page_cache, "monitoring_admission", lambda *_: {"admitted": 1, "excluded": 0})
    current = page_cache.monitoring_page_cache_key(
        object(),
        environment_id=1,
        paths=[],
        page="jobs",
        filters={"range": "30d"},
        timezone_label="UTC",
        now=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    previous = ResultCacheKey(
        environment_id=current.environment_id,
        namespace=current.namespace,
        parameters_fingerprint=current.parameters_fingerprint,
        input_fingerprint=current.input_fingerprint,
        producer_version=PRODUCER_VERSION,
    )

    assert current.producer_version == JOBS_PAGE_PRODUCER_VERSION
    assert current.identity != previous.identity
    assert page_cache.monitoring_page_etag(current) != page_cache.monitoring_page_etag(previous)


@pytest.mark.parametrize('page,previous_version', [
    ('overview', 'monitoring-page-sql-v28.maintenance-policy-v3'),
    ('dataflows', 'monitoring-page-sql-v28'),
    ('performance', 'monitoring-page-sql-v29.performance-kpis-v1'),
])
def test_contribution_execution_scope_metrics_fence_cached_phase_only_payloads(monkeypatch, page, previous_version):
    from dataclasses import replace

    monkeypatch.setattr(page_cache, 'monitoring_input_fingerprint', lambda *_: 'same-input')
    monkeypatch.setattr(page_cache, 'monitoring_admission', lambda *_: {'admitted': 1, 'excluded': 0})
    current = page_cache.monitoring_page_cache_key(
        object(), environment_id=1, paths=[], page=page,
        filters={'range': '30d'}, timezone_label='UTC',
        now=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    previous = replace(current, producer_version=previous_version)
    assert current.identity != previous.identity
    assert page_cache.monitoring_page_etag(current) != page_cache.monitoring_page_etag(previous)


def test_freshness_registry_evidence_key_preserves_literal_investigation_id(monkeypatch):
    monkeypatch.setattr(page_cache, "monitoring_input_fingerprint", lambda *_: "same-input")
    monkeypatch.setattr(page_cache, "monitoring_admission", lambda *_: {"admitted": 1, "excluded": 0})
    common = {
        "environment_id": 1,
        "paths": [],
        "page": "freshness",
        "timezone_label": "UTC",
        "view": "registry",
        "limit": 100,
        "offset": 0,
        "sort_by": "latest_freshness_at",
        "sort_dir": "desc",
        "analytics_generation": "generation-1",
    }
    padded = page_cache.monitoring_evidence_cache_key(
        object(), filters={"range": "all", "investigateValue": " df-42 "}, **common,
    )
    plain = page_cache.monitoring_evidence_cache_key(
        object(), filters={"range": "all", "investigateValue": "df-42"}, **common,
    )

    assert padded.identity != plain.identity
    assert padded.producer_version == FRESHNESS_REGISTRY_PRODUCER_VERSION


def test_dataflow_registry_key_separates_entity_detail_and_generation(monkeypatch):
    monkeypatch.setattr(page_cache, "monitoring_input_fingerprint", lambda *_: "same-input")
    monkeypatch.setattr(page_cache, "monitoring_admission", lambda *_: {"admitted": 1, "excluded": 0})
    common = {
        "environment_id": 1,
        "paths": [],
        "filters": {"range": "30d", "investigateValue": " df-42 "},
        "timezone_label": "UTC",
        "limit": 100,
        "offset": 0,
        "sort_by": "latest_run_at",
        "sort_dir": "desc",
        "analytics_generation": "generation-1",
    }
    overview = page_cache.monitoring_dataflow_registry_cache_key(
        object(), target_dataflow_id=None, **common,
    )
    detail = page_cache.monitoring_dataflow_registry_cache_key(
        object(), target_dataflow_id=" df-42 ", **common,
    )
    next_generation = page_cache.monitoring_dataflow_registry_cache_key(
        object(), target_dataflow_id=" df-42 ", **{**common, "analytics_generation": "generation-2"},
    )

    assert overview.identity != detail.identity
    freshness = page_cache.monitoring_dataflow_registry_cache_key(
        object(), facet="freshness", **common,
    )
    performance = page_cache.monitoring_dataflow_registry_cache_key(
        object(), facet="performance", **common,
    )
    assert freshness.identity != performance.identity
    assert freshness.identity != overview.identity
    assert detail.identity != next_generation.identity
    assert detail.producer_version == DATAFLOW_REGISTRY_PRODUCER_VERSION

def test_failed_analytics_publish_preserves_previous_generation(tmp_path: Path, monkeypatch):
    import duckdb

    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)
    first = analytics_store.publish_rows(
        7,
        [],
        [
            (
                "job.jsonl",
                "job_jsonl",
                "{}",
                {"job_id": "job-1", "status": "succeeded", "end_time": "2026-07-01T00:00:00Z"},
            )
        ],
        [],
        ["job.jsonl"],
        database_path=analytics_path,
    )
    assert first["published"] is True

    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        before = connection.execute(
            "select m.generation, s.generation, (select count(*) from etl_job_runs) "
            "from etl_analytics_meta m cross join etl_cache_sources s "
            "where m.singleton_id = 1 and s.source_id = 7"
        ).fetchone()

    def fail_insert(*_args, **_kwargs):
        raise RuntimeError("invalid parquet")

    monkeypatch.setattr(analytics_store, "insert_dataflow_file", fail_insert)
    failed = analytics_store.publish_rows(
        7,
        [("bad.parquet", "dataflow_parquet", "{}")],
        [],
        [],
        ["bad.parquet"],
        database_path=analytics_path,
    )
    assert failed["published"] is False
    assert failed["errors"]

    with duckdb.connect(str(analytics_path), read_only=True) as connection:
        after = connection.execute(
            "select m.generation, s.generation, (select count(*) from etl_job_runs) "
            "from etl_analytics_meta m cross join etl_cache_sources s "
            "where m.singleton_id = 1 and s.source_id = 7"
        ).fetchone()
    assert after == before

@pytest.mark.parametrize(
    "timezone_name,utc_offset_seconds", [("Asia/Saigon", None), (None, 7 * 3600)]
)
def test_cached_monitoring_summary_aggregates_overview_window_in_duckdb(
    tmp_path: Path, monkeypatch, timezone_name, utc_offset_seconds
):
    import duckdb

    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)

    with duckdb.connect(str(analytics_path)) as connection:
        analytics_schema_tables.ensure_tables(connection)
        analytics_schema_tables.ensure_typed_table(
            connection,
            analytics_schema.DATAFLOW_TABLE,
            analytics_schema.DATAFLOW_COLUMN_TYPES,
        )
        analytics_schema_tables.ensure_typed_table(
            connection,
            analytics_schema.JOB_TABLE,
            analytics_schema.JOB_COLUMN_TYPES,
        )
        insert_native_rows(
            connection,
            analytics_schema.DATAFLOW_TABLE,
            7,
            [
                _v6_typed_row("outside.parquet", "dataflow", {"job_id": "job-outside", "dataflow_id": "outside", "dataflow_run_id": "outside-run", "status": "failed", "end_time": "2026-06-10T02:59:59Z"}),
                _v6_typed_row("success.parquet", "dataflow", {"job_id": "job-success", "dataflow_id": "success", "dataflow_run_id": "success-run", "status": "succeeded", "end_time": "2026-07-01T22:00:00Z"}),
                _v6_typed_row("failed.parquet", "dataflow", {"job_id": "job-failed", "dataflow_id": "failed", "dataflow_run_id": "failed-run", "status": "failed", "end_time": "2026-07-02T01:00:00Z"}),
            ],
            analytics_schema.DATAFLOW_COLUMN_TYPES,
        )
        insert_native_rows(
            connection,
            analytics_schema.JOB_TABLE,
            7,
            [
                _v6_typed_row("last7.jsonl", "job", {"job_id": "job-last7", "status": "failed", "engine_name": "polars", "end_time": "2026-07-02T17:00:00Z"}),
                _v6_typed_row("before-last7.jsonl", "job", {"job_id": "job-before-last7", "status": "failed", "engine_name": "polars", "end_time": "2026-07-02T16:59:59Z"}),
                _v6_typed_row("last30.jsonl", "job", {"job_id": "job-last30", "status": "failed", "engine_name": "spark", "end_time": "2026-06-11T20:00:00Z"}),
                _v6_typed_row("future.jsonl", "job", {"job_id": "job-future", "status": "failed", "engine_name": "", "end_time": "2026-07-11T00:00:00Z"}),
                _v6_typed_row("success.jsonl", "job", {"job_id": "job-success", "status": "succeeded", "engine_name": "polars", "end_time": "2026-07-10T00:00:00Z"}),
                _v6_typed_row("fallback.jsonl", "job", {"job_id": "job-fallback", "status": "succeeded", "engine_name": "spark", "end_time": "not-a-timestamp", "start_time": "2026-07-09T00:00:00Z", "__run_date": "2026-07-09"}),
                _v6_typed_row("last30-boundary.jsonl", "job", {"job_id": "job-last30-boundary", "status": "failed", "engine_name": "historical", "end_time": "2026-06-09T17:00:00Z"}),
                _v6_typed_row("historical.jsonl", "job", {"job_id": "job-historical", "status": "failed", "engine_name": "historical", "end_time": "2026-04-01T00:00:00Z"}),
                _v6_typed_row("last365-boundary.jsonl", "job", {"job_id": "job-last365-boundary", "status": "failed", "engine_name": "historical", "end_time": "2025-07-09T17:00:00Z"}),
                _v6_typed_row("before-last365.jsonl", "job", {"job_id": "job-before-last365", "status": "failed", "engine_name": "historical", "end_time": "2025-07-09T16:59:59Z"}),
                _v6_typed_row("historical-success.jsonl", "job", {"job_id": "job-historical-success", "status": "succeeded", "engine_name": "historical", "end_time": "2026-04-01T00:00:00Z"}),
            ],
            analytics_schema.JOB_COLUMN_TYPES,
        )
        insert_native_rows(
            connection,
            analytics_schema.JOB_TABLE,
            8,
            [_v6_typed_row("other-source.jsonl", "job", {"job_id": "job-other-source", "status": "failed", "end_time": "2026-07-09T00:00:00Z"})],
            analytics_schema.JOB_COLUMN_TYPES,
        )
        analytics_cache_state.mark_cache_source(
            connection,
            7,
            refreshed_at=datetime.now(timezone.utc),
        )
        analytics_publication.publish_generation(
            connection,
            dataflow_column_types=analytics_schema.DATAFLOW_COLUMN_TYPES,
            job_column_types=analytics_schema.JOB_COLUMN_TYPES,
            published_at=datetime.now(timezone.utc),
        )

    source = PathRecord(str(tmp_path))
    source.id = 7
    cached = log_repository.cached_monitoring_summary(
        object(),
        [source],
        cutoff=datetime(2026, 6, 10, 3, 0, tzinfo=timezone.utc),
        timezone_name=timezone_name,
        utc_offset_seconds=utc_offset_seconds,
        local_today=datetime(2026, 7, 10, tzinfo=timezone.utc).date(),
    )

    assert cached is not None
    summary, errors = cached
    assert errors == []
    assert summary["dataflow_records"] == 2
    assert summary["dataflow_succeeded"] == 1
    assert summary["dataflow_failed"] == 1
    assert summary["job_records"] == 6
    assert summary["total_failures"] == 4
    assert summary["active_engines"] == 2
    assert summary["failed_last7"] == 1
    assert summary["failed_last30"] == 4
    assert summary["failed_last365"] == 6
    assert parse_utc_datetime(summary["latest_log_at"]).isoformat() == "2026-07-11T00:00:00+00:00"
    assert str(summary["date_min"]) == "2026-06-11"
    assert str(summary["date_max"]) == "2026-07-11"

def test_latest_dataflow_query_has_no_global_row_cap(tmp_path: Path, monkeypatch):
    import duckdb

    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)
    recent_rows = [
        _v6_typed_row(f"recent-{index}.parquet", "dataflow", {
                "dataflow_id": "recent",
                "dataflow_name": "recent flow",
                "dataflow_run_id": f"run-{index:04d}",
                "job_id": "job-recent",
                "status": "succeeded",
                "end_time": "2026-07-17T12:00:00Z",
                "observed_at": f"2026-07-17T{index // 3600 + 12:02d}:{(index // 60) % 60:02d}:{index % 60:02d}+00:00",
            })
        for index in range(1001)
    ]
    with duckdb.connect(str(analytics_path)) as connection:
        analytics_schema_tables.ensure_tables(connection)
        analytics_schema_tables.ensure_typed_table(
            connection,
            analytics_schema.DATAFLOW_TABLE,
            analytics_schema.DATAFLOW_COLUMN_TYPES,
        )
        insert_native_rows(
            connection,
            analytics_schema.DATAFLOW_TABLE,
            7,
            [
                *recent_rows,
                _v6_typed_row("old.parquet", "dataflow", {
                        "dataflow_id": "old",
                        "dataflow_name": "old flow",
                        "dataflow_run_id": "old-run",
                        "job_id": "job-old",
                        "status": "failed",
                        "end_time": "2025-01-01T00:00:00Z",
                        "observed_at": "2025-01-01T00:00:00+00:00",
                    }),
            ],
            analytics_schema.DATAFLOW_COLUMN_TYPES,
        )
        analytics_cache_state.mark_cache_source(
            connection,
            7,
            refreshed_at=datetime.now(timezone.utc),
        )
        analytics_publication.publish_generation(
            connection,
            dataflow_column_types=analytics_schema.DATAFLOW_COLUMN_TYPES,
            job_column_types=analytics_schema.JOB_COLUMN_TYPES,
            published_at=datetime.now(timezone.utc),
        )

    source = PathRecord(str(tmp_path))
    source.id = 7
    result = log_repository.query_cached_latest_dataflow_runs(object(), [source])

    assert result is not None
    rows, ambiguous_names, errors = result
    assert errors == []
    assert ambiguous_names == []
    assert {row["dataflow_id"] for row in rows} == {"recent", "old"}
    assert next(row for row in rows if row["dataflow_id"] == "recent")["dataflow_run_id"] == "run-1000"

def test_legacy_dataflow_cache_table_is_recreated_in_one_schema_pass(tmp_path: Path):
    import duckdb

    analytics_path = tmp_path / "analytics.duckdb"
    with duckdb.connect(str(analytics_path)) as connection:
        connection.execute(
            f"CREATE TABLE {analytics_schema.DATAFLOW_TABLE} (_raw_json VARCHAR)"
        )
        analytics_schema_tables.ensure_tables(connection)
        columns = analytics_schema.table_columns(connection, analytics_schema.DATAFLOW_TABLE)

    assert "_raw_json" not in columns
    assert "_source_id" in columns

def test_typed_dataflow_cache_ignores_legacy_transform_policy(tmp_path: Path):
    import duckdb

    analytics_path = tmp_path / "analytics.duckdb"
    with duckdb.connect(str(analytics_path)) as connection:
        analytics_schema_tables.ensure_tables(connection)
        insert_native_rows(
            connection,
            analytics_schema.DATAFLOW_TABLE,
            7,
            [
                (
                    "legacy.json",
                    "legacy_dataflow_json",
                    "{}",
                    {
                        "dataflow_id": "customers",
                        "transform_configure": '{"missing_column_policy": "ignore"}',
                        "transform_missing_column_policy": "ignore",
                    },
                )
            ],
            analytics_schema.DATAFLOW_COLUMN_TYPES,
        )
        columns = set(
            analytics_schema.table_columns(connection, analytics_schema.DATAFLOW_TABLE)
        )
        configure = connection.execute(
            "SELECT transform_configure FROM etl_dataflow_runs"
        ).fetchone()[0]

    assert configure == '{"missing_column_policy": "ignore"}'
    assert "transform_missing_column_policy" not in columns

def test_duckdb_cache_preserves_job_timestamp_string_and_drops_raw_json(tmp_path: Path, monkeypatch):
    import duckdb

    analytics_path = tmp_path / "analytics.duckdb"
    monkeypatch.setattr(log_ingestion, "analytics_database_path", lambda: analytics_path)
    monkeypatch.setattr(analytics_access, "analytics_database_path", lambda: analytics_path)
    raw_start = "2026-06-18T15:23:08.534826+07:00"
    job_row = {
        "job_id": "job-1",
        "status": "succeeded",
        "engine_name": "PolarsEngine",
        "metadata_provider_name": "file",
        "start_time": raw_start,
        "end_time": raw_start,
    }

    with duckdb.connect(str(analytics_path)) as connection:
        analytics_schema_tables.ensure_tables(connection)
        insert_native_rows(
            connection,
            analytics_schema.JOB_TABLE,
            1,
            [("job.jsonl", "job_jsonl", "{}", job_row)],
            analytics_schema.JOB_COLUMN_TYPES,
        )

        columns = {str(row[1]): str(row[2]).upper() for row in connection.execute("PRAGMA table_info('etl_job_runs')").fetchall()}
        cached_start = connection.execute("SELECT start_time FROM etl_job_runs").fetchone()[0]

    assert "_raw_json" not in columns
    assert "operation_type" not in columns
    assert columns["start_time"] == "VARCHAR"
    assert cached_start == raw_start

def test_duckdb_cache_uses_json_schema_for_dataflow_timestamps(tmp_path: Path):
    import duckdb

    file_path = tmp_path / "dataflow.json"
    file_path.write_text(
        json.dumps(
            {
                "log_schema_version": 3,
                "_type": "dataflow_run_log",
                "dataflow_run_id": "run-1",
                "job_id": "job-1",
                "start_time": "2026-06-18T15:23:08.534826+07:00",
                "end_time": "2026-06-18T15:24:08.534826+07:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with duckdb.connect() as connection:
        analytics_schema_tables.ensure_tables(connection)
        analytics_store.insert_dataflow_file(
            connection, 1, str(file_path), "dataflow_json", "{}"
        )

        escaped = str(file_path).replace("'", "''")
        source_types = {
            str(row[0]): _normalize_duckdb_type(str(row[1]))
            for row in connection.execute(
                f"DESCRIBE SELECT * FROM read_json_auto('{escaped}', format='newline_delimited', union_by_name=true)"
            ).fetchall()
        }
        cached_types = {
            str(row[1]): _normalize_duckdb_type(str(row[2]))
            for row in connection.execute("PRAGMA table_info('etl_dataflow_runs')").fetchall()
        }

    assert "_raw_json" not in cached_types
    assert source_types["start_time"] in {"TIMESTAMP", "TIMESTAMP WITH TIME ZONE"}
    assert source_types["end_time"] in {"TIMESTAMP", "TIMESTAMP WITH TIME ZONE"}
    assert cached_types["start_time"] == "VARCHAR"
    assert cached_types["end_time"] == "VARCHAR"

def test_duckdb_expected_column_order_keeps_source_columns_before_studio_columns():
    actual_columns = [
        "_source_id",
        "job_id",
        "new_raw_log_field",
        "operation_types",
        "_file_uri",
        "_ingested_at",
    ]
    source_column_types = {
        "job_id": "VARCHAR",
        "operation_types": "VARCHAR",
    }

    ordered = analytics_schema.expected_column_order(
        actual_columns,
        source_column_types,
    )

    assert ordered == [
        "job_id",
        "operation_types",
        "new_raw_log_field",
        "_source_id",
        "_file_uri",
        "_ingested_at",
    ]

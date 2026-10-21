from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from datacoolie_studio.domains.analytics import schema, schema_migration
from datacoolie_studio.domains.analytics.sql import quote_identifier


def _create_schema17(path: Path, *, overflow: bool = False) -> None:
    conn = duckdb.connect(str(path))
    try:
        technical = {
            **schema.STUDIO_CACHE_COLUMNS,
            "_record_line": "BIGINT",
            "_present_paths": "VARCHAR[]",
        }
        for table_name in (schema.DATAFLOW_TABLE, schema.JOB_TABLE):
            legacy = {**schema_migration._legacy_columns(table_name), **technical}
            ddl = ", ".join(
                f"{quote_identifier(name)} {dtype}" for name, dtype in legacy.items()
            )
            conn.execute(f"CREATE TABLE {table_name} ({ddl})")
        conn.execute(
            f"CREATE TABLE {schema.CACHE_SOURCES_TABLE} "
            "(source_id BIGINT, refreshed_at TIMESTAMPTZ, generation BIGINT)"
        )
        conn.execute(
            f"CREATE TABLE {schema.ANALYTICS_META_TABLE} "
            "(singleton_id INTEGER, schema_version INTEGER, generation BIGINT, "
            "build_state VARCHAR, published_at TIMESTAMPTZ)"
        )
        conn.execute(
            f"INSERT INTO {schema.ANALYTICS_META_TABLE} VALUES "
            "(1, 17, 4, 'ready', CURRENT_TIMESTAMP)"
        )
        conn.execute(
            f"INSERT INTO {schema.CACHE_SOURCES_TABLE} VALUES "
            "(101, CURRENT_TIMESTAMP, 2), (202, CURRENT_TIMESTAMP, 3)"
        )
        integer_value = 2**127 if overflow else 7
        conn.execute(
            f"""
            INSERT INTO {schema.JOB_TABLE} (
              _source_id, _file_uri, _file_kind, _ingested_at,
              _record_line, _type, job_id, status,
              total_dataflows, total_succeeded, total_failed, total_skipped,
              total_rows_read, operation_types, _present_paths
            ) VALUES (101, 'offline://job', 'job_json', CURRENT_TIMESTAMP,
              1, 'job_run_log', 'job-101', 'succeeded',
              {integer_value}, 1, 0, 0, 12, '[]', ['/status'])
            """
        )
        conn.execute(
            f"""
            INSERT INTO {schema.DATAFLOW_TABLE} (
              _source_id, _file_uri, _file_kind, _ingested_at,
              _record_line, _type, job_id, dataflow_id,
              dataflow_run_id, status, attempts, metrics, _present_paths
            ) VALUES (202, 'offline://flow', 'dataflow_json', CURRENT_TIMESTAMP,
              1, 'dataflow_run_log', 'job-101', 'flow-1', 'run-1',
              'succeeded', NULL, NULL, ['/status'])
            """
        )
    finally:
        conn.close()


def _schema_with_records(definitions: dict) -> dict:
    definitions = {
        name: {
            **definition,
            "properties": {
                "_type": {
                    "const": (
                        "dataflow_run_log"
                        if name == "dataflowRecord"
                        else "job_run_log"
                    )
                },
                **definition.get("properties", {}),
            },
        }
        for name, definition in definitions.items()
    }
    return {
        "oneOf": [
            {"$ref": "#/$defs/dataflowRecord"},
            {"$ref": "#/$defs/jobRecord"},
        ],
        "$defs": definitions,
    }


def test_schema17_candidate_rewrite_preserves_coverage_and_removes_presence(tmp_path: Path):
    live = tmp_path / "analytics.duckdb"
    candidate = tmp_path / "analytics.candidate.duckdb"
    _create_schema17(live)

    snapshot = schema_migration.migrate_database_copy(live, candidate)

    assert snapshot.source_ids == (101, 202)
    with duckdb.connect(str(candidate), read_only=True) as conn:
        assert conn.execute(f"SELECT count(*) FROM {schema.JOB_TABLE}").fetchone()[0] == 1
        assert conn.execute(f"SELECT count(*) FROM {schema.DATAFLOW_TABLE}").fetchone()[0] == 1
        assert conn.execute(
            f"SELECT job_id, total_dataflows FROM {schema.JOB_TABLE}"
        ).fetchone() == ("job-101", 7)
        assert "_present_paths" not in schema.table_columns(conn, schema.JOB_TABLE)
        assert "_present_paths" not in schema.table_columns(conn, schema.DATAFLOW_TABLE)


def test_schema17_candidate_preserves_null_parents_and_list_order(tmp_path: Path):
    live = tmp_path / "analytics.duckdb"
    candidate = tmp_path / "analytics.candidate.duckdb"
    _create_schema17(live)
    with duckdb.connect(str(live)) as conn:
        conn.execute(
            f"UPDATE {schema.DATAFLOW_TABLE} SET diagnostic_issues = "
            "[NULL, {'field': 'second', 'reason': 'omitted'}, "
            "{'field': 'third', 'reason': 'invalid'}]"
        )

    schema_migration.migrate_database_copy(live, candidate)

    with duckdb.connect(str(candidate), read_only=True) as conn:
        issues, attempts, metrics = conn.execute(
            f"SELECT diagnostic_issues, attempts, metrics FROM {schema.DATAFLOW_TABLE}"
        ).fetchone()
        assert issues == [None, {"field": "second", "reason": "omitted"}, {"field": "third", "reason": "invalid"}]
        assert attempts is None
        assert metrics is None


def test_schema17_contract_does_not_follow_future_mapping_additions(monkeypatch):
    original = schema_migration._legacy_columns(schema.DATAFLOW_TABLE)
    future = {**schema.DATAFLOW_COLUMN_TYPES, "future_root": "VARCHAR"}
    monkeypatch.setattr(schema, "DATAFLOW_COLUMN_TYPES", future)

    historical = schema_migration._legacy_columns(schema.DATAFLOW_TABLE)

    assert historical == original
    assert "future_root" not in historical


def test_schema17_overflow_aborts_candidate_without_mutating_live(tmp_path: Path):
    live = tmp_path / "analytics.duckdb"
    candidate = tmp_path / "analytics.candidate.duckdb"
    _create_schema17(live, overflow=True)

    with pytest.raises(schema_migration.SchemaMigrationError, match="outside HUGEINT"):
        schema_migration.migrate_database_copy(live, candidate)

    with duckdb.connect(str(live), read_only=True) as conn:
        assert int(conn.execute(f"SELECT total_dataflows FROM {schema.JOB_TABLE}").fetchone()[0]) == 2**127
        assert "_present_paths" in schema.table_columns(conn, schema.JOB_TABLE)


def test_schema17_nested_integer_overflow_aborts_candidate(tmp_path: Path):
    live = tmp_path / "analytics.duckdb"
    candidate = tmp_path / "analytics.candidate.duckdb"
    _create_schema17(live)
    with duckdb.connect(str(live)) as conn:
        conn.execute(
            f"UPDATE {schema.DATAFLOW_TABLE} SET metrics = struct_pack("
            "rows_read := CAST('170141183460469231731687303715884105728' AS BIGNUM), "
            "rows_written := CAST(1 AS BIGNUM), rows_inserted := CAST(1 AS BIGNUM), "
            "rows_updated := CAST(1 AS BIGNUM), rows_deleted := CAST(1 AS BIGNUM), "
            "files_added := CAST(1 AS BIGNUM), files_removed := CAST(1 AS BIGNUM), "
            "bytes_added := CAST(1 AS BIGNUM), bytes_removed := CAST(1 AS BIGNUM), "
            "bytes_saved := CAST(1 AS BIGNUM))"
        )

    with pytest.raises(schema_migration.SchemaMigrationError) as error:
        schema_migration.migrate_database_copy(live, candidate)
    assert error.value.reason_code == "numeric_overflow"

    with duckdb.connect(str(live), read_only=True) as conn:
        assert conn.execute(
            f"SELECT metrics.rows_read FROM {schema.DATAFLOW_TABLE}"
        ).fetchone()[0] == str(2**127)


def test_mapping_diff_rejects_required_addition_and_accepts_optional_root():
    old = {
        "schema": _schema_with_records(
            {"dataflowRecord": {"required": []}, "jobRecord": {"required": []}}
        ),
        "columns": {"dataflow_run_log": {"status": "VARCHAR"}, "job_run_log": {"status": "VARCHAR"}},
    }
    optional = {
        "schema": _schema_with_records(
            {"dataflowRecord": {"required": []}, "jobRecord": {"required": []}}
        ),
        "columns": {
            "dataflow_run_log": {"status": "VARCHAR", "new_field": "VARCHAR"},
            "job_run_log": {"status": "VARCHAR"},
        },
    }
    required = {
        "schema": _schema_with_records(
            {
                "dataflowRecord": {"required": ["new_field"]},
                "jobRecord": {"required": []},
            }
        ),
        "columns": {
            "dataflow_run_log": {"status": "VARCHAR", "new_field": "VARCHAR"},
            "job_run_log": {"status": "VARCHAR"},
        },
    }

    assert schema_migration.diff_mapping_documents(old, optional).kind == "additive_ddl"
    assert schema_migration.diff_mapping_documents(old, required).kind == "unsupported"


def test_mapping_diff_resolves_required_fields_when_definition_names_change():
    def document(flow_definition: dict, job_definition: dict) -> dict:
        return {
            "schema": {
                "oneOf": [
                    {"$ref": "#/$defs/flowV1"},
                    {"$ref": "#/$defs/jobV1"},
                ],
                "$defs": {
                    "flowV1": {
                        "type": "object",
                        **flow_definition,
                        "properties": {
                            "_type": {"const": "dataflow_run_log"},
                            **flow_definition.get("properties", {}),
                        },
                    },
                    "jobV1": {
                        "type": "object",
                        **job_definition,
                        "properties": {
                            "_type": {"const": "job_run_log"},
                            **job_definition.get("properties", {}),
                        },
                    },
                },
            },
            "columns": {
                "dataflow_run_log": {
                    "status": "VARCHAR",
                    **({"new_field": "VARCHAR"} if "new_field" in flow_definition.get("properties", {}) else {}),
                },
                "job_run_log": {"status": "VARCHAR"},
            },
        }

    old = document(
        {"required": [], "properties": {"status": {"type": "string"}}},
        {"required": [], "properties": {"status": {"type": "string"}}},
    )
    required = document(
        {
            "required": ["new_field"],
            "properties": {
                "status": {"type": "string"},
                "new_field": {"type": "string"},
            },
        },
        {"required": [], "properties": {"status": {"type": "string"}}},
    )

    assert schema_migration.diff_mapping_documents(old, required).kind == "unsupported"


def test_mapping_diff_accepts_optional_list_member_and_rejects_tightening():
    old = {
        "schema": _schema_with_records(
            {
                "dataflowRecord": {
                    "type": "object",
                    "required": [],
                    "properties": {
                        "attempts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["number"],
                                "properties": {"number": {"type": "integer"}},
                            },
                        }
                    },
                },
                "jobRecord": {"type": "object", "required": [], "properties": {}},
            }
        ),
        "columns": {
            "dataflow_run_log": {"attempts": "STRUCT(\"number\" HUGEINT)[]"},
            "job_run_log": {},
        },
    }
    optional = {
        "schema": _schema_with_records(
            {
                "dataflowRecord": {
                    **old["schema"]["$defs"]["dataflowRecord"],
                    "properties": {
                        "attempts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["number"],
                                "properties": {
                                    "number": {"type": "integer"},
                                    "label": {"type": "string"},
                                },
                            },
                        }
                    },
                },
                "jobRecord": old["schema"]["$defs"]["jobRecord"],
            }
        ),
        "columns": {
            "dataflow_run_log": {
                "attempts": "STRUCT(\"number\" HUGEINT, \"label\" VARCHAR)[]"
            },
            "job_run_log": {},
        },
    }
    required = {
        **optional,
        "schema": {
            **optional["schema"],
            "$defs": {
                **optional["schema"]["$defs"],
                "dataflowRecord": {
                    **optional["schema"]["$defs"]["dataflowRecord"],
                    "properties": {
                        **optional["schema"]["$defs"]["dataflowRecord"]["properties"],
                        "attempts": {
                            **optional["schema"]["$defs"]["dataflowRecord"]["properties"]["attempts"],
                            "items": {
                                **optional["schema"]["$defs"]["dataflowRecord"]["properties"]["attempts"]["items"],
                                "required": ["number", "label"],
                            },
                        },
                    },
                },
            },
        },
    }

    assert schema_migration.diff_mapping_documents(old, optional).kind == "stored_column_rewrite"
    assert schema_migration.diff_mapping_documents(old, required).kind == "unsupported"


def test_assessment_uses_cached_scope_for_schema17(tmp_path: Path):
    from datacoolie_studio.domains.analytics_upgrade.assessment import assess_cache

    path = tmp_path / "analytics.duckdb"
    _create_schema17(path)

    result = assess_cache(path, [])

    assert result.action == "schema_migration"
    assert result.operation_kind == "schema_migration"
    assert result.migration_kind == "schema17_native_rewrite"

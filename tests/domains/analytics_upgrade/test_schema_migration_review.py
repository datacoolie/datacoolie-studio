from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from datacoolie_studio.db.session import init_db
from datacoolie_studio.domains.analytics import schema, schema_compiler
from datacoolie_studio.domains.analytics.schema_compiler import (
    compile_mapping,
    resolve_ref,
)
from datacoolie_studio.domains.analytics_upgrade import assessment, orchestration
from datacoolie_studio.domains.analytics import schema_migration
from tests.domains.analytics_upgrade.test_schema_migration_native import _create_schema17


@pytest.mark.parametrize(
    "ddl",
    [
        "ALTER TABLE etl_analytics_meta DROP COLUMN generation",
        "ALTER TABLE etl_analytics_meta DROP COLUMN published_at",
        "ALTER TABLE etl_cache_sources DROP COLUMN generation",
        "ALTER TABLE etl_cache_sources DROP COLUMN refreshed_at",
        "UPDATE etl_dataflow_runs SET _file_uri = ''",
        "UPDATE etl_dataflow_runs SET _file_kind = ''",
        "UPDATE etl_dataflow_runs SET _record_line = 0",
    ],
)
def test_schema17_guard_rejects_incomplete_metadata_and_coverage(
    tmp_path: Path,
    ddl: str,
) -> None:
    path = tmp_path / "incomplete-schema17.duckdb"
    _create_schema17(path)
    with duckdb.connect(str(path)) as connection:
        connection.execute(ddl)

    with duckdb.connect(str(path), read_only=True) as connection:
        assert schema_migration.schema17_migration_reason(connection) in {
            "schema17_incomplete",
            "schema17_incompatible",
        }


def test_schema17_guard_rejects_duplicate_source_coverage(tmp_path: Path) -> None:
    path = tmp_path / "duplicate-source-coverage.duckdb"
    _create_schema17(path)
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            "INSERT INTO etl_cache_sources VALUES "
            "(101, CURRENT_TIMESTAMP, 2)"
        )

    with duckdb.connect(str(path), read_only=True) as connection:
        assert schema_migration.schema17_migration_reason(connection) == (
            "schema17_incomplete"
        )


def _optional_target_document() -> tuple[dict[str, Any], dict[str, Any]]:
    previous = schema_compiler.mapping_document()
    target_schema = copy.deepcopy(previous["schema"])
    target_schema["$defs"]["dataflowRecord"]["properties"]["review_tag"] = {
        "type": ["string", "null"],
    }
    target_schema["$defs"]["metrics"]["properties"]["optional_metric"] = {
        "type": ["integer", "null"],
    }
    target_schema["$defs"]["attempt"]["properties"]["optional_label"] = {
        "type": ["string", "null"],
    }
    target = {
        "codec": "native-v1",
        "schema": target_schema,
        "columns": {
            record_type: {
                name: node.duckdb_type
                for name, node in compile_mapping(target_schema, record_type).items()
            }
            for record_type in ("dataflow_run_log", "job_run_log")
        },
    }
    return previous, target


@pytest.mark.parametrize("already_native", [False, True])
def test_optional_root_struct_and_list_migration_uses_cached_rows_only(
    studio_paths,
    monkeypatch,
    already_native,
) -> None:
    init_db()
    live = studio_paths.analytics_path
    _create_schema17(live)

    if already_native:
        first = orchestration.run_upgrade_once(database_path_factory=lambda: live)
        assert first["state"] == "succeeded"
    with duckdb.connect(str(live), read_only=True) as connection:
        before = {
            "coverage": connection.execute(
                "SELECT source_id, generation FROM etl_cache_sources ORDER BY source_id"
            ).fetchall(),
            "job_ids": connection.execute(
                "SELECT job_id FROM etl_job_runs ORDER BY _source_id, _record_line"
            ).fetchall(),
            "flow_ids": connection.execute(
                "SELECT dataflow_run_id FROM etl_dataflow_runs ORDER BY _source_id, _record_line"
            ).fetchall(),
            "rows": connection.execute(
                "SELECT count(*) FROM etl_job_runs"
            ).fetchone()[0]
            + connection.execute(
                "SELECT count(*) FROM etl_dataflow_runs"
            ).fetchone()[0],
        }

    _previous, target = _optional_target_document()
    target_fingerprint = hashlib.sha256(
        json.dumps(target, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    target_dataflow_types = {
        **schema.DATAFLOW_COLUMN_TYPES,
        "review_tag": target["columns"]["dataflow_run_log"]["review_tag"],
        "metrics": target["columns"]["dataflow_run_log"]["metrics"],
        "attempts": target["columns"]["dataflow_run_log"]["attempts"],
    }
    monkeypatch.setattr(schema, "DATAFLOW_COLUMN_TYPES", target_dataflow_types)
    monkeypatch.setattr(assessment, "_target_contract_document", lambda: target)
    monkeypatch.setattr(
        assessment,
        "_target_contract_fingerprint",
        lambda: target_fingerprint,
    )
    monkeypatch.setattr(schema_compiler, "mapping_document", lambda: target)
    monkeypatch.setattr(
        schema_compiler,
        "contract_fingerprint",
        lambda: target_fingerprint,
    )

    def source_replay_must_not_run(*args, **kwargs):
        del args, kwargs
        raise AssertionError("optional SQL migration attempted source replay")

    result = orchestration.run_upgrade_once(
        database_path_factory=lambda: live,
        build_sources_parallel_fn=source_replay_must_not_run,
    )
    assert result["state"] == "succeeded"
    assert result["operation_kind"] == "schema_migration"
    assert result["reason_code"] == (
        "optional_fields_added" if already_native else "schema17_native_rewrite"
    )
    assert result["source_ids"] == [101, 202]
    assert result["completed_source_ids"] == []

    with duckdb.connect(str(live), read_only=True) as connection:
        after = {
            "coverage": connection.execute(
                "SELECT source_id, generation FROM etl_cache_sources ORDER BY source_id"
            ).fetchall(),
            "job_ids": connection.execute(
                "SELECT job_id FROM etl_job_runs ORDER BY _source_id, _record_line"
            ).fetchall(),
            "flow_ids": connection.execute(
                "SELECT dataflow_run_id FROM etl_dataflow_runs ORDER BY _source_id, _record_line"
            ).fetchall(),
            "rows": connection.execute(
                "SELECT count(*) FROM etl_job_runs"
            ).fetchone()[0]
            + connection.execute(
                "SELECT count(*) FROM etl_dataflow_runs"
            ).fetchone()[0],
        }
        assert after == before
        assert connection.execute(
            "SELECT count(*), count(review_tag) FROM etl_dataflow_runs"
        ).fetchone() == (len(before["flow_ids"]), 0)
        assert connection.execute(
            "SELECT count(*), count(metrics.optional_metric) FROM etl_dataflow_runs"
        ).fetchone() == (len(before["flow_ids"]), 0)
        assert connection.execute(
            "SELECT count(*) FROM etl_dataflow_runs WHERE attempts IS NULL"
        ).fetchone() == (len(before["flow_ids"]),)

    # A later scheduler pass may have no currently eligible source (for
    # example, both cached sources are paused/offline). Existing coverage is
    # still current and must not trigger a source replay or scope rebuild.
    follow_up = assessment.assess_cache(live, [])
    assert follow_up.action == "current"
    assert follow_up.extra_source_ids == (101, 202)


@pytest.mark.parametrize(
    "change",
    [
        lambda target: target["schema"]["$defs"]["dataflowRecord"]["properties"][
            "retry_attempts"
        ].update(minimum=1),
        lambda target: target["schema"]["$defs"]["dataflowRecord"]["properties"][
            "retry_attempts"
        ].update(type="number"),
    ],
)
def test_mapping_diff_blocks_tightened_or_changed_existing_constraints(change) -> None:
    previous = schema_compiler.mapping_document()
    target = copy.deepcopy(previous)
    change(target)
    if target["schema"]["$defs"]["dataflowRecord"]["properties"]["retry_attempts"].get(
        "type"
    ) == "number":
        target["columns"]["dataflow_run_log"]["retry_attempts"] = "DOUBLE"

    diff = schema_migration.diff_mapping_documents(previous, target)
    assert diff.kind == "unsupported"
    assert diff.reason_code == "breaking_schema_change"


def test_mapping_diff_does_not_lose_required_addition_when_defs_are_renamed() -> None:
    previous = schema_compiler.mapping_document()
    target_schema = copy.deepcopy(previous["schema"])
    names = list(target_schema["$defs"])
    replacements = {
        name: f"renamed_definition_{index}" for index, name in enumerate(names)
    }
    target_schema["$defs"] = {
        replacements[name]: value
        for name, value in target_schema["$defs"].items()
    }

    def rewrite(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "$ref" and isinstance(child, str):
                    value[key] = "#/$defs/" + replacements[
                        child.removeprefix("#/$defs/")
                    ]
                else:
                    rewrite(child)
        elif isinstance(value, list):
            for child in value:
                rewrite(child)

    rewrite(target_schema)
    tightened_schema = copy.deepcopy(target_schema)
    tightened_dataflow = next(
        resolve_ref(tightened_schema, branch["$ref"])
        for branch in tightened_schema["oneOf"]
        if resolve_ref(tightened_schema, branch["$ref"])
        .get("properties", {})
        .get("_type", {})
        .get("const")
        == "dataflow_run_log"
    )
    tightened_dataflow["properties"]["retry_attempts"]["minimum"] = 1
    tightened_target = {
        "codec": "native-v1",
        "schema": tightened_schema,
        "columns": {
            record_type: {
                name: node.duckdb_type
                for name, node in compile_mapping(tightened_schema, record_type).items()
            }
            for record_type in ("dataflow_run_log", "job_run_log")
        },
    }
    tightened_diff = schema_migration.diff_mapping_documents(
        previous, tightened_target
    )
    assert tightened_diff.kind == "unsupported"
    assert tightened_diff.reason_code == "breaking_schema_change"

    dataflow = next(
        resolve_ref(target_schema, branch["$ref"])
        for branch in target_schema["oneOf"]
        if resolve_ref(target_schema, branch["$ref"])
        .get("properties", {})
        .get("_type", {})
        .get("const")
        == "dataflow_run_log"
    )
    dataflow["properties"]["required_added"] = {"type": ["string", "null"]}
    dataflow["required"] = [*dataflow.get("required", []), "required_added"]
    target = {
        "codec": "native-v1",
        "schema": target_schema,
        "columns": {
            record_type: {
                name: node.duckdb_type
                for name, node in compile_mapping(target_schema, record_type).items()
            }
            for record_type in ("dataflow_run_log", "job_run_log")
        },
    }

    diff = schema_migration.diff_mapping_documents(previous, target)
    assert diff.kind == "unsupported"
    assert diff.reason_code == "breaking_schema_change"
    assert diff.required_additions == ("dataflow_run_log.required_added",)


@pytest.mark.parametrize(
    ("value", "should_fail"),
    [("'null'::JSON", False), ("'1e400'::JSON", True)],
)
def test_schema17_json_numeric_null_is_preserved_and_nonfinite_is_blocked(
    tmp_path: Path,
    value: str,
    should_fail: bool,
) -> None:
    live = tmp_path / "json-number-schema17.duckdb"
    candidate = tmp_path / "json-number-candidate.duckdb"
    _create_schema17(live)
    with duckdb.connect(str(live)) as connection:
        connection.execute(
            f"UPDATE {schema.DATAFLOW_TABLE} SET duration_seconds = {value}"
        )

    if should_fail:
        with pytest.raises(schema_migration.SchemaMigrationError) as raised:
            schema_migration.migrate_database_copy(live, candidate)
        assert raised.value.reason_code == "numeric_overflow"
    else:
        schema_migration.migrate_database_copy(live, candidate)
        with duckdb.connect(str(candidate), read_only=True) as connection:
            assert connection.execute(
                f"SELECT duration_seconds FROM {schema.DATAFLOW_TABLE}"
            ).fetchone() == (None,)

from __future__ import annotations

from tests.support.v6_records import native_record

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from datacoolie_studio.domains.logs.reader import (
    discover_dataflow_json_files,
    discover_job_json_files,
    read_dataflow_logs,
    read_job_logs,
    read_system_log_file,
)
from datacoolie_studio.domains.sources.service import validate_log_source


class _ValidationSession:
    def commit(self) -> None:
        pass

    def refresh(self, _source) -> None:
        pass


def test_reads_v6_execution_tree_and_excludes_unsupported_versions(tmp_path: Path):
    dataflow_dir = tmp_path / "execution_logs" / "dataflow_run_log" / "__run_date=2026-09-12"
    job_dir = tmp_path / "execution_logs" / "job_run_log" / "__run_date=2026-09-12"
    dataflow_dir.mkdir(parents=True)
    job_dir.mkdir(parents=True)
    (dataflow_dir / "dataflow_batch.json").write_text(
        "\n".join(
            [
                json.dumps(native_record({
                    "log_schema_version": 3,
                    "_type": "dataflow_run_log",
                    "dataflow_id": "orders",
                    "status": "succeeded",
                })),
                json.dumps({
                    "log_schema_version": 2,
                    "_type": "dataflow_run_log",
                    "dataflow_id": "old",
                    "status": "succeeded",
                }),
            ]
        ) + "\n",
        encoding="utf-8",
    )
    (job_dir / "job_snapshot.json").write_text(
        json.dumps(native_record({
            "log_schema_version": 3,
            "_type": "job_run_log",
            "job_id": "job-1",
            "run_attributes": '{"factory_run_id":"run-1"}',
            "status": "succeeded",
        })) + "\n",
        encoding="utf-8",
    )

    assert discover_dataflow_json_files(str(tmp_path))
    assert discover_job_json_files(str(tmp_path))
    dataflows, dataflow_errors = read_dataflow_logs([str(tmp_path)])
    jobs, job_errors = read_job_logs([str(tmp_path)])

    assert [row["dataflow_id"] for row in dataflows] == ["orders"]
    assert dataflow_errors[0]["code"] == "unsupported_log_schema"
    assert jobs[0]["run_attributes"] == '{"factory_run_id":"run-1"}'
    assert not job_errors


def test_system_log_filter_uses_record_job_id_not_filename(tmp_path: Path):
    log_file = tmp_path / "system_logs" / "__run_date=2026-09-12" / "opaque-name.json"
    log_file.parent.mkdir(parents=True)
    log_file.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                native_record({"log_schema_version": 3, "_type": "system_log", "job_id": "job-1", "level": "INFO", "msg": "one"}),
                native_record({"log_schema_version": 3, "_type": "system_log", "job_id": "job-2", "level": "INFO", "msg": "two"}),
            ]
        ) + "\n",
        encoding="utf-8",
    )

    rows, total, errors = read_system_log_file(str(log_file), job_id="job-1")

    assert not errors
    assert total == 1
    assert rows[0]["msg"] == "one"


def test_system_log_filters_run_and_event_context_independently(tmp_path: Path):
    log_file = tmp_path / "system_logs" / "__run_date=2026-09-12" / "system.json"
    log_file.parent.mkdir(parents=True)
    rows = [
        native_record({
            "log_schema_version": 3,
            "_type": "system_log",
            "job_id": "job-1",
            "dataflow_id": "orders",
            "dataflow_run_id": "run-1",
            "event_name": "dataflow.started",
            "msg": "started",
        }),
        native_record({
            "log_schema_version": 99,
            "_type": "system_log",
            "job_id": "job-1",
            "dataflow_run_id": "run-2",
            "event_name": "dataflow.finished",
            "msg": "finished",
        }),
    ]
    log_file.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    run_rows, run_total, run_errors = read_system_log_file(
        str(log_file), job_id="job-1", dataflow_run_id="run-2"
    )
    event_rows, event_total, event_errors = read_system_log_file(
        str(log_file), job_id="job-1", event_name="dataflow.started"
    )
    case_mismatch_rows, case_mismatch_total, case_mismatch_errors = read_system_log_file(
        str(log_file), job_id="job-1", event_name="DATAFLOW.STARTED"
    )

    assert run_total == 1
    assert run_rows[0]["msg"] == "finished"
    assert event_total == 1
    assert event_rows[0]["msg"] == "started"
    assert case_mismatch_rows == []
    assert case_mismatch_total == 0
    assert not run_errors
    assert not event_errors
    assert not case_mismatch_errors


@pytest.mark.parametrize(
    ("field", "filter_value", "message"),
    [
        ("dataflow_id", "DF-A", "exact"),
        ("dataflow_id", "df-a", "lowercase"),
        ("dataflow_id", "DF-A ", "space"),
        ("dataflow_id", "DF.A", "punctuation"),
        ("dataflow_run_id", "Run-A", "exact"),
        ("dataflow_run_id", "run-a", "lowercase"),
        ("dataflow_run_id", "Run-A ", "space"),
        ("dataflow_run_id", "Run.A", "punctuation"),
        ("event_name", "Event.A", "exact"),
        ("event_name", "event.a", "lowercase"),
        ("event_name", "Event.A ", "space"),
        ("event_name", "Event.A#", "punctuation"),
        ("dataflow_id", "123", "numeric scalar"),
    ],
)
def test_system_log_identity_filters_keep_lexical_values(
    tmp_path: Path,
    field: str,
    filter_value: str,
    message: str,
):
    log_file = tmp_path / "system_logs" / "system.json"
    log_file.parent.mkdir(parents=True)
    rows = [
        native_record({
            "log_schema_version": 4,
            "_type": "system_log",
            "job_id": "job-1",
            "dataflow_id": "DF-A",
            "dataflow_run_id": "Run-A",
            "event_name": "Event.A",
            "msg": "exact",
        }),
        native_record({
            "log_schema_version": 4,
            "_type": "system_log",
            "job_id": "job-1",
            "dataflow_id": "df-a",
            "dataflow_run_id": "run-a",
            "event_name": "event.a",
            "msg": "lowercase",
        }),
        native_record({
            "log_schema_version": 4,
            "_type": "system_log",
            "job_id": "job-1",
            "dataflow_id": "DF-A ",
            "dataflow_run_id": "Run-A ",
            "event_name": "Event.A ",
            "msg": "space",
        }),
        native_record({
            "log_schema_version": 4,
            "_type": "system_log",
            "job_id": "job-1",
            "dataflow_id": "DF.A",
            "dataflow_run_id": "Run.A",
            "event_name": "Event.A#",
            "msg": "punctuation",
        }),
        native_record({
            "log_schema_version": 4,
            "_type": "system_log",
            "job_id": "job-1",
            "dataflow_id": "123",
            "dataflow_run_id": "456",
            "event_name": "789",
            "msg": "numeric scalar",
        }),
    ]
    log_file.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    result_rows, total, errors = read_system_log_file(
        str(log_file),
        job_id="job-1",
        include_dataflow_logs=False,
        **{field: filter_value},
    )

    assert not errors
    assert total == 1
    assert [row["msg"] for row in result_rows] == [message]


def test_empty_identity_filters_preserve_job_only_scope(tmp_path: Path):
    log_file = tmp_path / "system_logs" / "system.json"
    log_file.parent.mkdir(parents=True)
    log_file.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                native_record({"_type": "system_log", "job_id": "job-1", "msg": "job"}),
                native_record({
                    "_type": "system_log",
                    "job_id": "job-1",
                    "dataflow_id": "DF-A",
                    "msg": "child",
                }),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows, total, errors = read_system_log_file(
        str(log_file),
        job_id="job-1",
        dataflow_id="",
        dataflow_run_id="",
        event_name="",
    )

    assert not errors
    assert total == 1
    assert [row["msg"] for row in rows] == ["job"]


@pytest.mark.parametrize("schema_version", [None, 2, 99])
def test_log_validation_reports_unsupported_schema_versions(
    tmp_path: Path,
    schema_version: int | None,
):
    dataflow_dir = tmp_path / "execution_logs" / "dataflow_run_log" / "__run_date=2026-09-12"
    dataflow_dir.mkdir(parents=True)
    record = {"_type": "dataflow_run_log", "job_id": "job-1"}
    if schema_version is not None:
        record["log_schema_version"] = schema_version
    (dataflow_dir / "snapshot.json").write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )
    source = SimpleNamespace(
        id=7,
        environment_id=1,
        source_kind="logs",
        uri=str(tmp_path),
        source_config_json=None,
        storage_provider="local",
        storage_auth_mode="none",
        credential_profile_id=None,
        storage_config_json=None,
    )

    result = validate_log_source(_ValidationSession(), source)

    assert result["status"] == "warning"
    assert result["message"] == (
        "Log source is readable but contains no supported v6 log records"
    )
    assert result["record_counts"]["unsupported_samples"] == 1
    assert result["errors"][0]["code"] == "unsupported_log_schema"


def test_log_validation_accepts_a_valid_v6_sample(tmp_path: Path):
    dataflow_dir = tmp_path / "execution_logs" / "dataflow_run_log" / "__run_date=2026-09-12"
    dataflow_dir.mkdir(parents=True)
    fixture = Path(__file__).parents[2] / "fixtures/datacoolie/logging/v6/dataflow_snapshot.json"
    (dataflow_dir / "snapshot.json").write_text(
        fixture.read_text(encoding="utf-8").splitlines()[0] + "\n",
        encoding="utf-8",
    )
    source = SimpleNamespace(
        id=7,
        environment_id=1,
        source_kind="logs",
        uri=str(tmp_path),
        source_config_json=None,
        storage_provider="local",
        storage_auth_mode="none",
        credential_profile_id=None,
        storage_config_json=None,
    )

    result = validate_log_source(_ValidationSession(), source)

    assert result["status"] == "ok"
    assert result["errors"] == []

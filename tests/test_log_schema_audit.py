from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "audit_or_repair_log_schema.py"
)
SPEC = importlib.util.spec_from_file_location("log_schema_audit", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def _write_legacy_file(path: Path, value: float = 2.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "group_number": pa.array([value, None], type=pa.float64()),
                "execution_order": pa.array([1.0, None], type=pa.float64()),
            }
        ),
        path,
    )


def test_audit_scans_analytics_streams_and_finds_all_integer_drift(tmp_path):
    root = tmp_path / "logs"
    legacy = root / "etl_logs" / "analyst" / "dataflow_run_log" / "legacy.parquet"
    _write_legacy_file(legacy)
    debug_file = root / "etl_logs" / "debug_json" / "job_run_log" / "bad.jsonl"
    debug_file.parent.mkdir(parents=True)
    debug_file.write_text("not-json\n", encoding="utf-8")
    job_file = root / "etl_logs" / "analyst" / "job_run_log" / "job.jsonl"
    job_file.parent.mkdir(parents=True)
    job_file.write_text(
        json.dumps({"_type": "job_run_log", "job_id": "job-1"}) + "\n",
        encoding="utf-8",
    )

    report = AUDIT.audit_log_root(root)

    assert report["scan_root"] == str(root / "etl_logs" / "analyst")
    assert report["parquet_files"] == 1
    assert report["job_jsonl_files"] == 1
    assert report["job_records"] == 1
    assert report["incompatible_count"] == 0
    assert len(report["repair_plan"]) == 1
    assert report["repair_plan"][0]["columns"] == [
        "execution_order",
        "group_number",
    ]


def test_apply_repairs_is_reversible_and_idempotent(tmp_path):
    root = tmp_path / "logs"
    legacy = root / "etl_logs" / "analyst" / "dataflow_run_log" / "legacy.parquet"
    _write_legacy_file(legacy)
    report = AUDIT.audit_log_root(root)
    backup = tmp_path / "backup"

    applied = AUDIT.apply_repairs(report, backup_dir=backup)
    after = AUDIT.audit_log_root(root)

    assert len(applied) == 1
    assert Path(applied[0]["backup"]).exists()
    assert after["repair_plan"] == []
    assert after["incompatible_count"] == 0
    schema = pq.ParquetFile(legacy).schema_arrow
    assert schema.field("group_number").type == pa.int64()
    assert schema.field("execution_order").type == pa.int64()


def test_apply_repairs_rolls_back_when_a_later_file_fails(tmp_path, monkeypatch):
    root = tmp_path / "logs"
    first = root / "etl_logs" / "analyst" / "dataflow_run_log" / "first.parquet"
    second = root / "etl_logs" / "analyst" / "dataflow_run_log" / "second.parquet"
    _write_legacy_file(first)
    _write_legacy_file(second)
    report = AUDIT.audit_log_root(root)
    real_repair = AUDIT._repair_parquet_file
    calls = 0

    def fail_on_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated repair failure")
        return real_repair(*args, **kwargs)

    monkeypatch.setattr(AUDIT, "_repair_parquet_file", fail_on_second)
    with pytest.raises(RuntimeError, match="simulated repair failure"):
        AUDIT.apply_repairs(report, backup_dir=tmp_path / "backup")

    for path in (first, second):
        schema = pq.ParquetFile(path).schema_arrow
        assert schema.field("group_number").type == pa.float64()
        assert schema.field("execution_order").type == pa.float64()


def test_audit_rejects_fractional_integer_drift(tmp_path):
    root = tmp_path / "logs"
    legacy = root / "etl_logs" / "analyst" / "dataflow_run_log" / "bad.parquet"
    _write_legacy_file(legacy, value=2.5)

    report = AUDIT.audit_log_root(root)

    assert report["incompatible_count"] == 1
    assert report["repair_plan"][0]["columns"] == ["execution_order"]
    with pytest.raises(ValueError, match="incompatible findings"):
        AUDIT.apply_repairs(report, backup_dir=tmp_path / "backup")


def test_audit_counts_malformed_analyst_job_lines_as_incompatible(tmp_path):
    root = tmp_path / "logs"
    job_file = root / "etl_logs" / "analyst" / "job_run_log" / "job.jsonl"
    job_file.parent.mkdir(parents=True)
    job_file.write_text("not-json\n", encoding="utf-8")

    report = AUDIT.audit_log_root(root)

    assert report["malformed_job_lines"] == 1
    assert report["incompatible_count"] == 1

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from tests.support.paths import REPOSITORY_ROOT


SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "audit_or_repair_log_schema.py"


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


def _run_cli(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(root), *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_dry_run_is_deterministic_and_does_not_mutate(tmp_path):
    root = tmp_path / "logs"
    legacy = root / "etl_logs" / "analyst" / "dataflow_run_log" / "legacy.parquet"
    _write_legacy_file(legacy)

    first = _run_cli(root)
    output = tmp_path / "report.json"
    second = _run_cli(root, "--output", str(output))

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert json.loads(first.stdout) == json.loads(second.stdout)
    assert output.read_text(encoding="utf-8") == second.stdout
    assert pq.ParquetFile(legacy).schema_arrow.field("group_number").type == pa.float64()


def test_cli_incompatible_input_returns_two_without_mutation(tmp_path):
    root = tmp_path / "logs"
    job_file = root / "etl_logs" / "analyst" / "job_run_log" / "job.jsonl"
    job_file.parent.mkdir(parents=True)
    job_file.write_text("not-json\n", encoding="utf-8")

    result = _run_cli(root)

    assert result.returncode == 2
    assert json.loads(result.stdout)["incompatible_count"] == 1
    assert result.stderr == ""


def test_cli_apply_requires_external_backup_and_reports_post_apply_state(tmp_path):
    root = tmp_path / "logs"
    legacy = root / "etl_logs" / "analyst" / "dataflow_run_log" / "legacy.parquet"
    _write_legacy_file(legacy)
    backup = tmp_path / "backup"

    result = _run_cli(root, "--apply", "--backup-dir", str(backup))

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["repair_mode"] == "apply"
    assert report["post_apply"]["repair_plan"] == []
    assert Path(report["applied"][0]["backup"]).exists()
    assert pq.ParquetFile(legacy).schema_arrow.field("group_number").type == pa.int64()


def test_cli_rejects_backup_inside_source_root_and_maps_error_to_one(tmp_path):
    root = tmp_path / "logs"
    legacy = root / "etl_logs" / "analyst" / "dataflow_run_log" / "legacy.parquet"
    _write_legacy_file(legacy)

    result = _run_cli(root, "--apply", "--backup-dir", str(root / "backup"))

    assert result.returncode == 1
    assert "Backup directory must be outside source root" in result.stderr
    assert pq.ParquetFile(legacy).schema_arrow.field("group_number").type == pa.float64()

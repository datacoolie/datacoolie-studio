"""Audit log artifact schemas and safely repair lossless integer drift.

The default command is read-only. Use ``--apply`` only after reviewing the
JSON report; apply mode creates an external backup and atomically replaces only
Parquet files whose floating/integer drift has passed the lossless validation.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from datacoolie_studio.domains.analytics import schema, schema_compatibility  # noqa: E402


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _escape_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _describe_parquet(conn, path: Path) -> dict[str, str]:
    rows = conn.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{_escape_path(path)}', "
        "union_by_name=true, hive_partitioning=false)"
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def _integer_drift_stats(
    conn,
    path: Path,
    column: str,
    source_type: str,
) -> dict[str, Any]:
    quoted = _quote_identifier(column)
    invalid_predicate = schema_compatibility.lossless_integer_predicate(
        quoted,
        source_type,
    )
    row = conn.execute(
        f"""
        SELECT
          COUNT(*)::BIGINT AS row_count,
          COUNT(*) FILTER (WHERE {quoted} IS NOT NULL AND ({invalid_predicate}))::BIGINT AS invalid_count,
          MIN({quoted}) AS min_value,
          MAX({quoted}) AS max_value,
          COUNT(*) FILTER (WHERE {quoted} IS NULL)::BIGINT AS null_count
        FROM read_parquet(
          '{_escape_path(path)}',
          union_by_name=true,
          hive_partitioning=false
        )
        """
    ).fetchone()
    return {
        "row_count": int(row[0] or 0),
        "invalid_count": int(row[1] or 0),
        "min_value": row[2],
        "max_value": row[3],
        "null_count": int(row[4] or 0),
    }


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return type(value).__name__


def _json_value_matches_target(value: Any, target_type: str) -> bool:
    if value is None:
        return True
    target = schema_compatibility.normalize_type(target_type)
    if target == "VARCHAR":
        return isinstance(value, str)
    if target == "BOOLEAN":
        return isinstance(value, bool)
    if target == "BIGINT":
        return isinstance(value, int) and not isinstance(value, bool)
    if target == "DOUBLE":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _add_finding(
    findings: list[dict[str, Any]],
    *,
    category: str,
    artifact_type: str,
    path: Path,
    column: str | None = None,
    target_type: str | None = None,
    source_type: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    finding: dict[str, Any] = {
        "category": category,
        "artifact_type": artifact_type,
        "path": str(path),
    }
    if column is not None:
        finding["column"] = column
    if target_type is not None:
        finding["target_type"] = target_type
    if source_type is not None:
        finding["source_type"] = source_type
    if details:
        finding["details"] = details
    findings.append(finding)


def _analyst_root(root: Path) -> Path:
    """Resolve a source root to the streams consumed by analytics ingestion."""

    candidates = (
        root / "etl_logs" / "analyst",
        root / "analyst",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return root


def audit_log_root(root: Path) -> dict[str, Any]:
    """Return a deterministic schema report without changing the source."""

    root = root.expanduser().resolve()
    scan_root = _analyst_root(root)
    parquet_files = sorted(scan_root.rglob("*.parquet"))
    job_files = sorted(
        path
        for path in scan_root.rglob("*.jsonl")
        if "job_run_log" in path.parts
    )
    findings: list[dict[str, Any]] = []
    repair_plan: dict[str, list[str]] = defaultdict(list)
    parquet_type_counts: dict[str, Counter[str]] = defaultdict(Counter)
    missing_columns: Counter[str] = Counter()
    ignored_columns: Counter[str] = Counter()
    unexpected_columns: Counter[str] = Counter()
    incompatible_count = 0

    with duckdb.connect() as conn:
        expected_dataflow = {
            column: data_type
            for column, data_type in schema.DATAFLOW_COLUMN_TYPES.items()
            if column not in schema.GENERATED_CACHE_COLUMNS
        }
        for path in parquet_files:
            source_types = _describe_parquet(conn, path)
            for column, source_type in source_types.items():
                parquet_type_counts[column][source_type] += 1
                if column in schema.STUDIO_CACHE_COLUMNS:
                    continue
                if column in schema.IGNORED_DATAFLOW_SOURCE_COLUMNS:
                    ignored_columns[column] += 1
                    _add_finding(
                        findings,
                        category="ignored",
                        artifact_type="dataflow_parquet",
                        path=path,
                        column=column,
                        source_type=source_type,
                    )
                    continue
                target_type = expected_dataflow.get(column)
                if target_type is None:
                    unexpected_columns[column] += 1
                    _add_finding(
                        findings,
                        category="unexpected",
                        artifact_type="dataflow_parquet",
                        path=path,
                        column=column,
                        source_type=source_type,
                    )
                    continue
                try:
                    compatibility = schema_compatibility.classify_type(
                        target_type,
                        source_type,
                    )
                except ValueError as exc:
                    incompatible_count += 1
                    _add_finding(
                        findings,
                        category="incompatible",
                        artifact_type="dataflow_parquet",
                        path=path,
                        column=column,
                        target_type=target_type,
                        source_type=source_type,
                        details={"message": str(exc)},
                    )
                    continue
                if not compatibility.requires_value_validation:
                    if compatibility.normalized_type != source_type.upper():
                        _add_finding(
                            findings,
                            category="compatible",
                            artifact_type="dataflow_parquet",
                            path=path,
                            column=column,
                            target_type=target_type,
                            source_type=source_type,
                        )
                    continue
                stats = _integer_drift_stats(conn, path, column, source_type)
                category = "repairable" if stats["invalid_count"] == 0 else "incompatible"
                if category == "repairable":
                    repair_plan[str(path)].append(column)
                else:
                    incompatible_count += 1
                _add_finding(
                    findings,
                    category=category,
                    artifact_type="dataflow_parquet",
                    path=path,
                    column=column,
                    target_type=target_type,
                    source_type=source_type,
                    details=stats,
                )

            for column in sorted(set(expected_dataflow) - set(source_types)):
                missing_columns[column] += 1

        job_records = 0
        malformed_job_lines = 0
        expected_job = schema.JOB_COLUMN_TYPES
        for path in job_files:
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(),
                start=1,
            ):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    malformed_job_lines += 1
                    incompatible_count += 1
                    _add_finding(
                        findings,
                        category="incompatible",
                        artifact_type="job_jsonl",
                        path=path,
                        details={"line": line_number, "message": str(exc)},
                    )
                    continue
                if not isinstance(row, dict) or row.get("_type") != "job_run_log":
                    continue
                job_records += 1
                for column, value in row.items():
                    target_type = expected_job.get(column)
                    if target_type is None:
                        unexpected_columns[column] += 1
                        _add_finding(
                            findings,
                            category="unexpected",
                            artifact_type="job_jsonl",
                            path=path,
                            column=column,
                            details={"line": line_number, "json_type": _json_type(value)},
                        )
                    elif not _json_value_matches_target(value, target_type):
                        incompatible_count += 1
                        _add_finding(
                            findings,
                            category="incompatible",
                            artifact_type="job_jsonl",
                            path=path,
                            column=column,
                            target_type=target_type,
                            details={"line": line_number, "json_type": _json_type(value)},
                        )
                for column in sorted(set(expected_job) - set(row)):
                    missing_columns[column] += 1

    findings.sort(
        key=lambda finding: (
            finding["artifact_type"],
            finding["path"],
            finding.get("column", ""),
            finding["category"],
        )
    )
    repair_items = [
        {"path": path, "columns": sorted(columns)}
        for path, columns in sorted(repair_plan.items())
    ]
    summary = Counter(finding["category"] for finding in findings)
    return {
        "root": str(root),
        "scan_root": str(scan_root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parquet_files": len(parquet_files),
        "job_jsonl_files": len(job_files),
        "job_records": job_records,
        "malformed_job_lines": malformed_job_lines,
        "summary": dict(sorted(summary.items())),
        "parquet_type_counts": {
            column: dict(sorted(types.items()))
            for column, types in sorted(parquet_type_counts.items())
        },
        "missing_columns": dict(sorted(missing_columns.items())),
        "ignored_columns": dict(sorted(ignored_columns.items())),
        "unexpected_columns": dict(sorted(unexpected_columns.items())),
        "findings": findings,
        "repair_plan": repair_items,
        "incompatible_count": incompatible_count,
    }


def _assert_backup_is_external(root: Path, backup_dir: Path) -> None:
    root_resolved = root.resolve()
    backup_resolved = backup_dir.resolve()
    try:
        backup_resolved.relative_to(root_resolved)
    except ValueError:
        return
    raise ValueError(f"Backup directory must be outside source root: {backup_dir}")


def _repair_parquet_file(
    path: Path,
    columns: list[str],
    *,
    root: Path,
    backup_dir: Path,
) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    relative = path.resolve().relative_to(root.resolve())
    backup_path = backup_dir / relative
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.exists():
        raise FileExistsError(
            f"Backup already exists; choose a new backup directory: {backup_path}"
        )
    shutil.copy2(path, backup_path)

    original = pq.read_table(path)
    updated = original
    for column in columns:
        index = updated.schema.get_field_index(column)
        if index < 0:
            raise ValueError(f"Repair column is missing from {path}: {column}")
        field = updated.schema.field(index)
        casted = pc.cast(updated[column], pa.int64(), safe=True)
        updated = updated.set_column(
            index,
            pa.field(
                field.name,
                pa.int64(),
                nullable=field.nullable,
                metadata=field.metadata,
            ),
            casted,
        )

    if updated.num_rows != original.num_rows:
        raise ValueError(f"Repair changed row count for {path}")
    for column in columns:
        before = original[column].to_pylist()
        after = updated[column].to_pylist()
        for old, new in zip(before, after):
            if old is None and new is None:
                continue
            if old is None or new is None or int(old) != new:
                raise ValueError(f"Repair changed values for {path}: {column}")

    temporary = path.with_name(f".{path.name}.schema-repair.tmp")
    try:
        pq.write_table(updated, temporary, compression="snappy")
        verified = pq.read_table(temporary)
        if verified.num_rows != original.num_rows:
            raise ValueError(f"Verification changed row count for {path}")
        for column in columns:
            if verified.schema.field(column).type != pa.int64():
                raise ValueError(f"Verification did not produce BIGINT for {path}: {column}")
            if verified[column].to_pylist() != updated[column].to_pylist():
                raise ValueError(f"Verification changed values for {path}: {column}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "path": str(path),
        "backup": str(backup_path),
        "columns": columns,
        "row_count": original.num_rows,
    }


def apply_repairs(report: dict[str, Any], *, backup_dir: Path) -> list[dict[str, Any]]:
    root = Path(report["root"])
    if report["incompatible_count"]:
        raise ValueError("Cannot apply repair while incompatible findings remain")
    _assert_backup_is_external(root, backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    applied = []
    try:
        for item in report["repair_plan"]:
            applied.append(
                _repair_parquet_file(
                    Path(item["path"]),
                    list(item["columns"]),
                    root=root,
                    backup_dir=backup_dir,
                )
            )
    except Exception:
        for item in reversed(applied):
            shutil.copy2(item["backup"], item["path"])
        raise
    return applied


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="log root to audit")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="rewrite only validated lossless integer drift; default is read-only",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="external backup directory used with --apply",
    )
    parser.add_argument("--output", type=Path, help="also write the JSON report here")
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = audit_log_root(args.root)
    if args.apply:
        default_backup = args.root.resolve().parent / (
            f"{args.root.resolve().name}.schema-repair-"
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )
        backup_dir = (args.backup_dir or default_backup).expanduser().resolve()
        report["repair_mode"] = "apply"
        report["backup_dir"] = str(backup_dir)
        report["applied"] = apply_repairs(report, backup_dir=backup_dir)
        report["post_apply"] = audit_log_root(args.root)
    else:
        report["repair_mode"] = "dry-run"
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 2 if report["incompatible_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

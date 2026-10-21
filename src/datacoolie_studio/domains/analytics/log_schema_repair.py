"""Transactional repair operations for log artifact schema drift."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Mapping


def _assert_backup_is_external(root: Path, backup_dir: Path) -> None:
    """Reject backups that could be overwritten by a source-root scan."""

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
    """Back up and atomically rewrite one validated Parquet artifact."""

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


def apply_repairs(report: Mapping[str, Any], *, backup_dir: Path) -> list[dict[str, Any]]:
    """Apply a validated repair plan and restore prior files on later failure."""

    root = Path(report["root"])
    if report["incompatible_count"]:
        raise ValueError("Cannot apply repair while incompatible findings remain")
    _assert_backup_is_external(root, backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    applied: list[dict[str, Any]] = []
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

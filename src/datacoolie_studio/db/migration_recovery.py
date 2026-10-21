from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import Engine, make_url

from datacoolie_studio.core.config import backup_dir

logger = logging.getLogger(__name__)


class WorkspaceMigrationError(RuntimeError):
    """Raised when a workspace migration fails after its safety preflight."""

    def __init__(self, message: str, *, backup_path: Path | None) -> None:
        self.backup_path = backup_path
        super().__init__(message)


def create_workspace_backup(
    engine: Engine,
    *,
    source_revision: str | None,
    target_revision: str,
    target_heads: tuple[str, ...],
    source_state: str,
    allow_existing_foreign_key_violations: bool = False,
) -> Path | None:
    """Create and verify a retained SQLite backup plus its migration manifest."""

    if engine.dialect.name != "sqlite":
        raise RuntimeError("Studio workspace migration backups require SQLite")

    url = make_url(str(engine.url))
    database = url.database
    if database in {None, "", ":memory:"}:
        logger.warning(
            "Workspace migration has no persistent backup because the database "
            "is in-memory"
        )
        return None

    source_path = Path(database).expanduser()
    if not source_path.exists():
        raise RuntimeError(f"Workspace database does not exist: {source_path}")

    backup_root = backup_dir() / "workspace-migrations"
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    timestamp_label = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
    source_label = _safe_revision_label(source_revision or "unversioned")
    target_label = _safe_revision_label(
        target_heads[0] if len(target_heads) == 1 else target_revision
    )
    backup_path = backup_root / (
        f"{source_path.stem}.pre-alembic-{source_label}-to-{target_label}-"
        f"{timestamp_label}-{uuid4().hex[:8]}.db"
    )

    source = None
    target = None
    try:
        import sqlite3

        source = sqlite3.connect(str(source_path))
        target = sqlite3.connect(str(backup_path))
        source.backup(target)
        integrity = target.execute("PRAGMA integrity_check").fetchone()
        foreign_keys = target.execute("PRAGMA foreign_key_check").fetchall()
        target.commit()
        if integrity != ("ok",):
            raise RuntimeError(
                f"Workspace migration backup failed SQLite integrity check: {integrity}"
            )
        if foreign_keys and not allow_existing_foreign_key_violations:
            raise RuntimeError(
                "Workspace migration backup failed SQLite foreign-key check: "
                f"{foreign_keys[:3]}"
            )
        if foreign_keys:
            logger.warning(
                "Workspace migration backup retained %s existing SQLite "
                "foreign-key violations for the legacy cleanup step",
                len(foreign_keys),
            )
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()

    manifest_path = backup_path.with_suffix(".json")
    manifest = {
        "backup_kind": "datacoolie-studio-workspace-migration",
        "backup_path": str(backup_path.resolve()),
        "database_identity": str(source_path.resolve()),
        "created_at": timestamp.isoformat(),
        "source_state": source_state,
        "source_revision": source_revision,
        "target_revision": target_revision,
        "target_head": list(target_heads),
        "backup_sha256": _sha256(backup_path),
        "integrity_check": "ok",
        "foreign_key_check": "ok" if not foreign_keys else "existing_violations",
        "foreign_key_violation_count": len(foreign_keys),
        "foreign_key_violation_sample": [list(row) for row in foreign_keys[:10]],
    }
    _write_json_atomically(manifest_path, manifest)
    logger.info(
        "Created Studio workspace migration backup at %s (manifest: %s)",
        backup_path,
        manifest_path,
    )
    return backup_path


def _safe_revision_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomically(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()

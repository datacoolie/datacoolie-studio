"""Index frozen producer filename candidates for system log manifests.

The backfill is deliberately self-contained.  Migration history must not call
the mutable runtime parser: a future parser correction must not reinterpret a
workspace that has already completed this revision.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from alembic import op
import sqlalchemy as sa


revision = "b6d4f8a1c209"
down_revision = "a3b7d9e2f104"
branch_labels = None
depends_on = None

_PAGE_SIZE = 256
_FILENAME_RE = re.compile(
    r"^system_(?P<stamp>[0-9]{8}_[0-9]{6})_"
    r"(?P<job_num>[0-9]+)_(?P<job_index>[0-9]+)_(?P<token>.+)\.json$"
)
_TOKEN_SAFE = "-_.~"


def upgrade() -> None:
    op.add_column(
        "log_file_manifest",
        sa.Column("filename_job_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "log_file_manifest",
        sa.Column("filename_job_id_alt", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_log_file_manifest_source_kind_filename_job",
        "log_file_manifest",
        ["source_id", "file_kind", "filename_job_id"],
        unique=False,
    )
    op.create_index(
        "ix_log_file_manifest_source_kind_filename_job_alt",
        "log_file_manifest",
        ["source_id", "file_kind", "filename_job_id_alt"],
        unique=False,
    )
    backfill_filename_candidates(op.get_bind())


def downgrade() -> None:
    op.drop_index(
        "ix_log_file_manifest_source_kind_filename_job_alt",
        table_name="log_file_manifest",
    )
    op.drop_index(
        "ix_log_file_manifest_source_kind_filename_job",
        table_name="log_file_manifest",
    )
    with op.batch_alter_table("log_file_manifest") as batch_op:
        batch_op.drop_column("filename_job_id_alt")
        batch_op.drop_column("filename_job_id")


def backfill_filename_candidates(connection, *, page_size: int = _PAGE_SIZE) -> None:
    """Apply this revision's frozen URI-only backfill to an open connection.

    The legacy bridge uses this same immutable derivation when it has to
    upgrade a database without running Alembic.  Keeping the connection as an
    argument avoids importing the mutable runtime filename parser there.
    """

    last_id = 0
    while True:
        rows = connection.execute(
            sa.text(
                "SELECT id, file_uri FROM log_file_manifest "
                "WHERE id > :last_id AND file_kind = 'system_json' "
                "ORDER BY id LIMIT :page_size"
            ),
            {"last_id": last_id, "page_size": page_size},
        ).mappings().all()
        if not rows:
            return
        updates = []
        for row in rows:
            parsed = _parse_frozen(str(row["file_uri"]))
            updates.append(
                {
                    "id": int(row["id"]),
                    "filename_job_id": parsed[0] if parsed else None,
                    "filename_job_id_alt": parsed[1] if parsed else None,
                }
            )
        connection.execute(
            sa.text(
                "UPDATE log_file_manifest SET filename_job_id = :filename_job_id, "
                "filename_job_id_alt = :filename_job_id_alt WHERE id = :id"
            ),
            updates,
        )
        last_id = int(rows[-1]["id"])


def _parse_frozen(file_uri: str) -> tuple[str, str | None] | None:
    try:
        basename = _basename_frozen(file_uri)
    except (TypeError, ValueError, OSError):
        return None
    match = _FILENAME_RE.fullmatch(basename)
    if match is None:
        return None
    try:
        job_num = int(match.group("job_num"), 10)
        job_index = int(match.group("job_index"), 10)
        if job_num < 1 or job_index < 0 or job_index >= job_num:
            return None
        datetime.strptime(match.group("stamp"), "%Y%m%d_%H%M%S").replace(
            tzinfo=timezone.utc
        )
        token = match.group("token")
        decoded, alternate = _decode_frozen_token(token)
        if decoded is None:
            return None
    except (TypeError, ValueError, UnicodeError):
        return None
    return decoded, alternate


def _decode_frozen_token(token: str) -> tuple[str | None, str | None]:
    suffix = re.fullmatch(r"(?P<job>.+)_part_(?P<sequence>[0-9]{8,})", token)
    if suffix is not None and _valid_sequence(suffix.group("sequence")):
        base = _decode_canonical_token(suffix.group("job"))
        if base is not None:
            return (
                f"{base}_part_{suffix.group('sequence')}",
                base,
            )
    return _decode_canonical_token(token), None


def _decode_canonical_token(token: str) -> str | None:
    decoded = unquote(token, encoding="utf-8", errors="strict")
    return decoded if _canonical_token(decoded) == token else None


def _valid_sequence(sequence: str) -> bool:
    return len(sequence) >= 8 and sequence.isdecimal() and sequence.lstrip("0") != ""


def _canonical_token(value: str) -> str:
    raw = str(value).strip()
    if not raw:
        return "default"
    token = quote(raw, safe=_TOKEN_SAFE, encoding="utf-8", errors="strict")
    if raw.startswith(".") and token.startswith("."):
        token = "%2E" + token[1:]
    if raw.endswith(".") and token.endswith("."):
        token = token[:-1] + "%2E"
    return token or "default"


def _basename_frozen(file_uri: str) -> str:
    text = str(file_uri).strip()
    parsed = urlparse(text)
    if parsed.scheme.lower() == "file":
        return Path(unquote(parsed.path)).name
    if parsed.scheme and not _looks_like_windows_drive(text):
        return text.rstrip("/").rsplit("/", 1)[-1]
    return Path(text).name


def _looks_like_windows_drive(value: str) -> bool:
    return len(value) >= 2 and value[1] == ":" and value[0].isalpha()

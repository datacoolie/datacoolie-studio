"""normalize legacy empty-log validation markers

Revision ID: e4c8a1b9d2f0
Revises: d7b34e91c6a2
Create Date: 2026-09-19 00:00:00.000000
"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4c8a1b9d2f0"
down_revision: Union[str, Sequence[str], None] = "d7b34e91c6a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGACY_MESSAGE = "No execution or system log files found"
EMPTY_MESSAGE = "Log source is readable but contains no matching log files"
MIGRATION_MARKER = "e4c8a1b9d2f0"


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, read_check_result_json
            FROM environment_sources
            WHERE source_kind = 'logs' AND read_check_status = 'error'
            """
        )
    ).mappings()
    for row in rows:
        result = _json_object(row["read_check_result_json"])
        if result.get("message") != LEGACY_MESSAGE:
            continue
        result.update(
            {
                "status": "warning",
                "message": EMPTY_MESSAGE,
                "empty": True,
                "record_counts": {
                    "dataflow_json_files": 0,
                    "job_json_files": 0,
                    "system_json_files": 0,
                },
                "records_scanned": 0,
                "errors": [],
                "migration_marker": MIGRATION_MARKER,
            }
        )
        connection.execute(
            sa.text(
                """
                UPDATE environment_sources
                SET read_check_status = 'warning', read_check_result_json = :result
                WHERE id = :id
                """
            ),
            {"id": row["id"], "result": json.dumps(result, sort_keys=True)},
        )


def downgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, read_check_result_json
            FROM environment_sources
            WHERE source_kind = 'logs' AND read_check_status = 'warning'
            """
        )
    ).mappings()
    for row in rows:
        result = _json_object(row["read_check_result_json"])
        if result.get("migration_marker") != MIGRATION_MARKER:
            continue
        result.pop("migration_marker", None)
        result.pop("empty", None)
        result.update(
            {
                "status": "error",
                "message": LEGACY_MESSAGE,
                "errors": [{"message": LEGACY_MESSAGE}],
            }
        )
        connection.execute(
            sa.text(
                """
                UPDATE environment_sources
                SET read_check_status = 'error', read_check_result_json = :result
                WHERE id = :id
                """
            ),
            {"id": row["id"], "result": json.dumps(result, sort_keys=True)},
        )


def _json_object(value: str | None) -> dict[str, object]:
    try:
        result = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return result if isinstance(result, dict) else {}

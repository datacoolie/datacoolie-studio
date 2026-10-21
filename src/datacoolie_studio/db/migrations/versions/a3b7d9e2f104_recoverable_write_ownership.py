"""Keep failed, recoverable metadata writes in their environment slot."""

from alembic import op
import sqlalchemy as sa

revision = "a3b7d9e2f104"
down_revision = "f2a6c8b1d903"
branch_labels = None
depends_on = None

_INDEX = "uq_metadata_write_operations_active_environment"


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT environment_id, operation_key, state FROM metadata_write_operations "
        "WHERE state IN ('prepared', 'applying', 'partial', 'failed') "
        "ORDER BY environment_id, operation_key"
    )).all()
    owners: dict[int, list[str]] = {}
    for environment_id, operation_key, state in rows:
        owners.setdefault(environment_id, []).append(f"{operation_key} ({state})")
    conflicts = [
        f"environment {environment_id}: {', '.join(operations)}"
        for environment_id, operations in owners.items() if len(operations) > 1
    ]
    if conflicts:
        raise RuntimeError(
            "Recoverable metadata write ownership overlaps; resolve through a "
            "prior-schema application or reviewed backup repair before upgrading: "
            + "; ".join(conflicts)
        )
    op.drop_index(_INDEX, table_name="metadata_write_operations")
    op.create_index(
        _INDEX, "metadata_write_operations", ["environment_id"], unique=True,
        sqlite_where=sa.text("state IN ('prepared', 'applying', 'partial', 'failed')"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="metadata_write_operations")
    op.create_index(
        _INDEX, "metadata_write_operations", ["environment_id"], unique=True,
        sqlite_where=sa.text("state IN ('prepared', 'applying', 'partial')"),
    )

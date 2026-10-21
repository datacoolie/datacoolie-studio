"""simplify log scan state and index batch logging sessions

Revision ID: c1f6a2e9b7d4
Revises: aae9ff4de82c
Create Date: 2026-09-17 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1f6a2e9b7d4"
down_revision: Union[str, Sequence[str], None] = "aae9ff4de82c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "log_file_manifest",
        sa.Column("log_session_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "log_file_manifest",
        sa.Column("event_min_timestamp", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "log_file_manifest",
        sa.Column("event_max_timestamp", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_log_file_manifest_source_kind_session",
        "log_file_manifest",
        ["source_id", "file_kind", "log_session_id"],
        unique=False,
    )

    # The key column already exists in the baseline. Prefer its exact hourly
    # value, then the old checkpoint key, and finally the old date column. This
    # keeps the migration lossless for workspaces created before keyed
    # partitions were introduced while making the new key the only authority.
    op.execute(
        sa.text(
            """
            UPDATE log_stream_states
            SET last_scanned_partition_key = COALESCE(
                last_scanned_partition_key,
                checkpoint_partition_key,
                CASE
                    WHEN last_scanned_partition_value IS NOT NULL
                    THEN strftime('%Y-%m-%d', last_scanned_partition_value)
                    ELSE NULL
                END
            )
            """
        )
    )
    with op.batch_alter_table("log_stream_states") as batch_op:
        batch_op.drop_column("checkpoint_partition_value")
        batch_op.drop_column("checkpoint_partition_key")
        batch_op.drop_column("boundary_last_modified")
        batch_op.drop_column("last_scanned_partition_value")


def downgrade() -> None:
    with op.batch_alter_table("log_stream_states") as batch_op:
        batch_op.add_column(sa.Column("checkpoint_partition_value", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("checkpoint_partition_key", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("boundary_last_modified", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("last_scanned_partition_value", sa.Date(), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE log_stream_states
            SET checkpoint_partition_key = last_scanned_partition_key,
                last_scanned_partition_value = CASE
                    WHEN last_scanned_partition_key IS NOT NULL
                    THEN substr(last_scanned_partition_key, 1, 10)
                    ELSE NULL
                END,
                checkpoint_partition_value = CASE
                    WHEN last_scanned_partition_key IS NOT NULL
                    THEN substr(last_scanned_partition_key, 1, 10)
                    ELSE NULL
                END
            """
        )
    )
    op.drop_index(
        "ix_log_file_manifest_source_kind_session",
        table_name="log_file_manifest",
    )
    op.drop_column("log_file_manifest", "event_max_timestamp")
    op.drop_column("log_file_manifest", "event_min_timestamp")
    op.drop_column("log_file_manifest", "log_session_id")

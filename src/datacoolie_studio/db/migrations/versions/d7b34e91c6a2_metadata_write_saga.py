"""add durable metadata write operations

Revision ID: d7b34e91c6a2
Revises: c1f6a2e9b7d4
Create Date: 2026-09-18 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7b34e91c6a2"
down_revision: Union[str, Sequence[str], None] = "c1f6a2e9b7d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "metadata_write_operations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_key", sa.String(length=80), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("editor_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environments.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_key",
            name="uq_metadata_write_operations_key",
        ),
    )
    op.add_column(
        "metadata_backups",
        sa.Column("backup_ref", sa.Text(), nullable=True),
    )
    op.add_column(
        "metadata_backups",
        sa.Column("backup_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "metadata_backups",
        sa.Column("backup_size", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_metadata_write_operations_environment_state",
        "metadata_write_operations",
        ["environment_id", "state"],
        unique=False,
    )
    op.create_index(
        "ix_metadata_write_operations_expires_at",
        "metadata_write_operations",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "uq_metadata_write_operations_active_environment",
        "metadata_write_operations",
        ["environment_id"],
        unique=True,
        sqlite_where=sa.text("state IN ('prepared', 'applying', 'partial')"),
    )

    op.create_table(
        "metadata_write_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("item_order", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("target_uri", sa.Text(), nullable=False),
        sa.Column("target_descriptor_json", sa.Text(), nullable=True),
        sa.Column("binding_snapshot_json", sa.Text(), nullable=True),
        sa.Column("expected_revision_json", sa.Text(), nullable=True),
        sa.Column("expected_content_hash", sa.String(length=64), nullable=True),
        sa.Column("expected_size", sa.Integer(), nullable=True),
        sa.Column("expected_provider_revision", sa.Text(), nullable=True),
        sa.Column("backup_ref", sa.Text(), nullable=True),
        sa.Column("backup_hash", sa.String(length=64), nullable=True),
        sa.Column("backup_size", sa.Integer(), nullable=True),
        sa.Column("payload_ref", sa.Text(), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column("payload_size", sa.Integer(), nullable=True),
        sa.Column("applied_revision_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["metadata_write_operations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["environment_sources.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_id",
            "item_order",
            name="uq_metadata_write_items_operation_order",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "target_uri",
            name="uq_metadata_write_items_operation_target",
        ),
    )
    op.create_index(
        "ix_metadata_write_items_operation_state",
        "metadata_write_items",
        ["operation_id", "state"],
        unique=False,
    )
    op.create_index(
        "ix_metadata_write_items_source_id",
        "metadata_write_items",
        ["source_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_metadata_write_items_source_id",
        table_name="metadata_write_items",
    )
    op.drop_index(
        "ix_metadata_write_items_operation_state",
        table_name="metadata_write_items",
    )
    op.drop_table("metadata_write_items")
    op.drop_column("metadata_backups", "backup_ref")
    op.drop_column("metadata_backups", "backup_hash")
    op.drop_column("metadata_backups", "backup_size")
    op.drop_index(
        "uq_metadata_write_operations_active_environment",
        table_name="metadata_write_operations",
    )
    op.drop_index(
        "ix_metadata_write_operations_expires_at",
        table_name="metadata_write_operations",
    )
    op.drop_index(
        "ix_metadata_write_operations_environment_state",
        table_name="metadata_write_operations",
    )
    op.drop_table("metadata_write_operations")

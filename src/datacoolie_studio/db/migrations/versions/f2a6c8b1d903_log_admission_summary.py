"""Persist revision-bound log schema admission evidence."""

from alembic import op
import sqlalchemy as sa

revision = "f2a6c8b1d903"
down_revision = "e4c8a1b9d2f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("log_file_manifest", sa.Column("admission_summary_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("log_file_manifest", "admission_summary_json")

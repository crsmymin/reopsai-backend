"""add persona telecom behavior scores

Revision ID: 20260529_000014
Revises: 20260520_000013
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260529_000014"
down_revision = "20260520_000013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "personas",
        sa.Column("telecom_behavior_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade():
    op.drop_column("personas", "telecom_behavior_scores")

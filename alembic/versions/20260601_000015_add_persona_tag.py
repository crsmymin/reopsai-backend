"""add persona tag

Revision ID: 20260601_000015
Revises: 20260529_000014
Create Date: 2026-06-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260601_000015"
down_revision = "20260529_000014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("personas", sa.Column("tag", sa.String(length=20), nullable=True))
    op.create_index("ix_personas_tag", "personas", ["tag"])


def downgrade():
    op.drop_index("ix_personas_tag", table_name="personas")
    op.drop_column("personas", "tag")

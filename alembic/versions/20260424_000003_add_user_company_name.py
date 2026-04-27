"""add company_name to users

Revision ID: 20260424_000003
Revises: 20260423_000002
Create Date: 2026-04-24 11:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260424_000003"
down_revision = "20260423_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("company_name", sa.String(length=255), nullable=True))
    op.create_index("ix_users_company_name", "users", ["company_name"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_company_name", table_name="users")
    op.drop_column("users", "company_name")

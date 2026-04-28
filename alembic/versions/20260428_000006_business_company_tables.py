"""business company tables

Revision ID: 20260428_000006
Revises: 20260427_000005
Create Date: 2026-04-28 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260428_000006"
down_revision = "20260427_000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("department", sa.String(length=255), nullable=True))
    op.create_index("ix_users_department", "users", ["department"], unique=False)
    op.execute("UPDATE users SET department = NULL")
    op.drop_index("ix_users_company_name", table_name="users")
    op.drop_column("users", "company_name")

    op.execute("UPDATE users SET account_type = 'business' WHERE account_type = 'enterprise'")

    op.create_table(
        "company_members",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="member"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("company_id", "user_id", name="uq_company_members_company_user"),
    )
    op.create_index("ix_company_members_company_id", "company_members", ["company_id"], unique=False)
    op.create_index("ix_company_members_user_id", "company_members", ["user_id"], unique=False)

    op.create_table(
        "company_usage_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("feature_key", sa.String(length=64), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_company_usage_events_company_id", "company_usage_events", ["company_id"], unique=False)
    op.create_index("ix_company_usage_events_user_id", "company_usage_events", ["user_id"], unique=False)
    op.create_index("ix_company_usage_events_feature_key", "company_usage_events", ["feature_key"], unique=False)
    op.create_index("ix_company_usage_events_occurred_at", "company_usage_events", ["occurred_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_company_usage_events_occurred_at", table_name="company_usage_events")
    op.drop_index("ix_company_usage_events_feature_key", table_name="company_usage_events")
    op.drop_index("ix_company_usage_events_user_id", table_name="company_usage_events")
    op.drop_index("ix_company_usage_events_company_id", table_name="company_usage_events")
    op.drop_table("company_usage_events")

    op.drop_index("ix_company_members_user_id", table_name="company_members")
    op.drop_index("ix_company_members_company_id", table_name="company_members")
    op.drop_table("company_members")

    op.execute("UPDATE users SET account_type = 'enterprise' WHERE account_type = 'business'")

    op.add_column("users", sa.Column("company_name", sa.String(length=255), nullable=True))
    op.create_index("ix_users_company_name", "users", ["company_name"], unique=False)
    op.drop_index("ix_users_department", table_name="users")
    op.drop_column("users", "department")

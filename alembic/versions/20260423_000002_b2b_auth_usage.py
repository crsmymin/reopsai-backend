"""b2b auth and usage metering

Revision ID: 20260423_000002
Revises: 20260413_000001
Create Date: 2026-04-23 12:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260423_000002"
down_revision = "20260413_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("account_type", sa.String(length=32), nullable=False, server_default="individual"),
    )
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column("password_reset_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_users_account_type", "users", ["account_type"], unique=False)

    op.add_column(
        "teams",
        sa.Column("plan_code", sa.String(length=64), nullable=False, server_default="starter"),
    )
    op.create_index("ix_teams_plan_code", "teams", ["plan_code"], unique=False)

    op.create_table(
        "team_usage_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("feature_key", sa.String(length=64), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_team_usage_events_team_id", "team_usage_events", ["team_id"], unique=False)
    op.create_index("ix_team_usage_events_user_id", "team_usage_events", ["user_id"], unique=False)
    op.create_index("ix_team_usage_events_feature_key", "team_usage_events", ["feature_key"], unique=False)
    op.create_index("ix_team_usage_events_occurred_at", "team_usage_events", ["occurred_at"], unique=False)

    # backfill
    op.execute("UPDATE users SET tier='super' WHERE tier='admin'")
    op.execute("UPDATE users SET account_type='enterprise' WHERE tier='enterprise'")
    op.execute("UPDATE users SET password_reset_required=true WHERE account_type='enterprise'")


def downgrade() -> None:
    op.drop_index("ix_team_usage_events_occurred_at", table_name="team_usage_events")
    op.drop_index("ix_team_usage_events_feature_key", table_name="team_usage_events")
    op.drop_index("ix_team_usage_events_user_id", table_name="team_usage_events")
    op.drop_index("ix_team_usage_events_team_id", table_name="team_usage_events")
    op.drop_table("team_usage_events")

    op.drop_index("ix_teams_plan_code", table_name="teams")
    op.drop_column("teams", "plan_code")

    op.drop_index("ix_users_account_type", table_name="users")
    op.drop_column("users", "password_reset_required")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "account_type")

    # best-effort rollback for tier migration
    op.execute("UPDATE users SET tier='admin' WHERE tier='super'")

"""remove team company links

Revision ID: 20260428_000007
Revises: 20260428_000006
Create Date: 2026-04-28 13:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260428_000007"
down_revision = "20260428_000006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_team_usage_events_company_id", table_name="team_usage_events")
    op.drop_constraint("fk_team_usage_events_company_id_companies", "team_usage_events", type_="foreignkey")
    op.drop_column("team_usage_events", "company_id")

    op.drop_index("ix_teams_company_id", table_name="teams")
    op.drop_constraint("fk_teams_company_id_companies", "teams", type_="foreignkey")
    op.drop_column("teams", "company_id")


def downgrade() -> None:
    op.add_column("teams", sa.Column("company_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_teams_company_id_companies", "teams", "companies", ["company_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_teams_company_id", "teams", ["company_id"], unique=False)

    op.add_column("team_usage_events", sa.Column("company_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_team_usage_events_company_id_companies",
        "team_usage_events",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_team_usage_events_company_id", "team_usage_events", ["company_id"], unique=False)

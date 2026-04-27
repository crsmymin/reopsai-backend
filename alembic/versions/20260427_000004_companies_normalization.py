"""companies normalization

Revision ID: 20260427_000004
Revises: 20260424_000003
Create Date: 2026-04-27 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260427_000004"
down_revision = "20260424_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("name", name="uq_companies_name"),
    )
    op.create_index("ix_companies_name", "companies", ["name"], unique=False)
    op.create_index("ix_companies_status", "companies", ["status"], unique=False)

    op.add_column("users", sa.Column("company_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_users_company_id_companies", "users", "companies", ["company_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_users_company_id", "users", ["company_id"], unique=False)

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

    op.execute(
        """
        INSERT INTO companies (name, status)
        SELECT DISTINCT trim(company_name), 'active'
        FROM users
        WHERE company_name IS NOT NULL AND trim(company_name) <> ''
        ON CONFLICT (name) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE users
        SET company_id = companies.id
        FROM companies
        WHERE users.company_id IS NULL
          AND users.company_name IS NOT NULL
          AND trim(users.company_name) = companies.name
        """
    )
    op.execute(
        """
        UPDATE teams
        SET company_id = users.company_id
        FROM users
        WHERE teams.company_id IS NULL
          AND teams.owner_id = users.id
          AND users.company_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE team_usage_events
        SET company_id = teams.company_id
        FROM teams
        WHERE team_usage_events.company_id IS NULL
          AND team_usage_events.team_id = teams.id
          AND teams.company_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_team_usage_events_company_id", table_name="team_usage_events")
    op.drop_constraint("fk_team_usage_events_company_id_companies", "team_usage_events", type_="foreignkey")
    op.drop_column("team_usage_events", "company_id")

    op.drop_index("ix_teams_company_id", table_name="teams")
    op.drop_constraint("fk_teams_company_id_companies", "teams", type_="foreignkey")
    op.drop_column("teams", "company_id")

    op.drop_index("ix_users_company_id", table_name="users")
    op.drop_constraint("fk_users_company_id_companies", "users", type_="foreignkey")
    op.drop_column("users", "company_id")

    op.drop_index("ix_companies_status", table_name="companies")
    op.drop_index("ix_companies_name", table_name="companies")
    op.drop_table("companies")

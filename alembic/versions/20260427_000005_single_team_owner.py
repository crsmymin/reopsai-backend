"""enforce single team owner

Revision ID: 20260427_000005
Revises: 20260427_000004
Create Date: 2026-04-27 16:00:00
"""

from alembic import op


revision = "20260427_000005"
down_revision = "20260427_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure every active teams.owner_id has a matching owner membership.
    op.execute(
        """
        INSERT INTO team_members (team_id, user_id, role)
        SELECT t.id, t.owner_id, 'owner'
        FROM teams t
        WHERE t.owner_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM team_members tm
              WHERE tm.team_id = t.id
                AND tm.user_id = t.owner_id
          )
        ON CONFLICT (team_id, user_id) DO NOTHING
        """
    )

    # teams.owner_id is the canonical single owner. Demote every other owner role.
    op.execute(
        """
        UPDATE team_members tm
        SET role = CASE
            WHEN t.owner_id IS NOT NULL AND tm.user_id = t.owner_id THEN 'owner'
            ELSE 'member'
        END
        FROM teams t
        WHERE tm.team_id = t.id
        """
    )

    # For legacy teams with owner roles but no teams.owner_id, choose the earliest owner.
    op.execute(
        """
        WITH first_owner AS (
            SELECT DISTINCT ON (team_id) team_id, user_id
            FROM team_members
            WHERE role = 'owner'
            ORDER BY team_id, joined_at ASC, id ASC
        )
        UPDATE teams t
        SET owner_id = first_owner.user_id
        FROM first_owner
        WHERE t.id = first_owner.team_id
          AND t.owner_id IS NULL
        """
    )

    # Re-apply role normalization after filling missing owner_id values.
    op.execute(
        """
        UPDATE team_members tm
        SET role = CASE
            WHEN t.owner_id IS NOT NULL AND tm.user_id = t.owner_id THEN 'owner'
            ELSE 'member'
        END
        FROM teams t
        WHERE tm.team_id = t.id
        """
    )

    op.create_index(
        "uq_team_members_one_owner_per_team",
        "team_members",
        ["team_id"],
        unique=True,
        postgresql_where="role = 'owner'",
    )


def downgrade() -> None:
    op.drop_index("uq_team_members_one_owner_per_team", table_name="team_members")

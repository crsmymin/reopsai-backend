"""add team id to llm usage metering

Revision ID: 20260429_000010
Revises: 20260429_000009
Create Date: 2026-04-29 11:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260429_000010"
down_revision = "20260429_000009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_usage_events", sa.Column("team_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_llm_usage_events_team_id_teams",
        "llm_usage_events",
        "teams",
        ["team_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_llm_usage_events_team_id", "llm_usage_events", ["team_id"], unique=False)

    op.add_column("llm_usage_daily_aggregates", sa.Column("team_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_llm_usage_daily_aggregates_team_id_teams",
        "llm_usage_daily_aggregates",
        "teams",
        ["team_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_llm_usage_daily_aggregates_team_id", "llm_usage_daily_aggregates", ["team_id"], unique=False)

    op.execute(
        """
        WITH owner_teams AS (
            SELECT DISTINCT ON (owner_id) owner_id AS user_id, id AS team_id
            FROM teams
            WHERE owner_id IS NOT NULL AND status != 'deleted'
            ORDER BY owner_id, created_at ASC, id ASC
        ),
        member_teams AS (
            SELECT DISTINCT ON (tm.user_id) tm.user_id, tm.team_id
            FROM team_members tm
            JOIN teams t ON t.id = tm.team_id
            WHERE t.status != 'deleted'
            ORDER BY tm.user_id, tm.joined_at ASC, tm.id ASC
        ),
        primary_teams AS (
            SELECT user_id, team_id FROM owner_teams
            UNION ALL
            SELECT mt.user_id, mt.team_id
            FROM member_teams mt
            WHERE NOT EXISTS (
                SELECT 1 FROM owner_teams ot WHERE ot.user_id = mt.user_id
            )
        )
        UPDATE llm_usage_events event
        SET team_id = primary_teams.team_id
        FROM primary_teams
        WHERE event.team_id IS NULL
          AND event.user_id = primary_teams.user_id
        """
    )
    op.execute(
        """
        WITH owner_teams AS (
            SELECT DISTINCT ON (owner_id) owner_id AS user_id, id AS team_id
            FROM teams
            WHERE owner_id IS NOT NULL AND status != 'deleted'
            ORDER BY owner_id, created_at ASC, id ASC
        ),
        member_teams AS (
            SELECT DISTINCT ON (tm.user_id) tm.user_id, tm.team_id
            FROM team_members tm
            JOIN teams t ON t.id = tm.team_id
            WHERE t.status != 'deleted'
            ORDER BY tm.user_id, tm.joined_at ASC, tm.id ASC
        ),
        primary_teams AS (
            SELECT user_id, team_id FROM owner_teams
            UNION ALL
            SELECT mt.user_id, mt.team_id
            FROM member_teams mt
            WHERE NOT EXISTS (
                SELECT 1 FROM owner_teams ot WHERE ot.user_id = mt.user_id
            )
        )
        UPDATE llm_usage_daily_aggregates aggregate
        SET team_id = primary_teams.team_id
        FROM primary_teams
        WHERE aggregate.team_id IS NULL
          AND aggregate.user_id = primary_teams.user_id
        """
    )

    op.drop_index(
        "uq_llm_usage_daily_aggregate_dimension",
        table_name="llm_usage_daily_aggregates",
    )
    op.execute(
        """
        WITH grouped AS (
            SELECT
                MIN(id) AS keep_id,
                ARRAY_AGG(id ORDER BY id) AS ids,
                usage_date,
                COALESCE(company_id, -1) AS company_key,
                COALESCE(team_id, -1) AS team_key,
                COALESCE(user_id, -1) AS user_key,
                provider,
                model,
                feature_key,
                SUM(request_count) AS request_count,
                SUM(prompt_tokens) AS prompt_tokens,
                SUM(completion_tokens) AS completion_tokens,
                SUM(cached_input_tokens) AS cached_input_tokens,
                SUM(reasoning_tokens) AS reasoning_tokens,
                SUM(total_tokens) AS total_tokens,
                SUM(billable_weighted_tokens) AS billable_weighted_tokens,
                SUM(estimated_cost_usd) AS estimated_cost_usd,
                MAX(updated_at) AS updated_at
            FROM llm_usage_daily_aggregates
            GROUP BY
                usage_date,
                COALESCE(company_id, -1),
                COALESCE(team_id, -1),
                COALESCE(user_id, -1),
                provider,
                model,
                feature_key
            HAVING COUNT(*) > 1
        ),
        updated AS (
            UPDATE llm_usage_daily_aggregates target
            SET
                request_count = grouped.request_count,
                prompt_tokens = grouped.prompt_tokens,
                completion_tokens = grouped.completion_tokens,
                cached_input_tokens = grouped.cached_input_tokens,
                reasoning_tokens = grouped.reasoning_tokens,
                total_tokens = grouped.total_tokens,
                billable_weighted_tokens = grouped.billable_weighted_tokens,
                estimated_cost_usd = grouped.estimated_cost_usd,
                updated_at = grouped.updated_at
            FROM grouped
            WHERE target.id = grouped.keep_id
            RETURNING grouped.ids, grouped.keep_id
        )
        DELETE FROM llm_usage_daily_aggregates target
        USING updated
        WHERE target.id = ANY(updated.ids)
          AND target.id <> updated.keep_id
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_llm_usage_daily_aggregate_dimension
        ON llm_usage_daily_aggregates (
            usage_date,
            COALESCE(company_id, -1),
            COALESCE(team_id, -1),
            COALESCE(user_id, -1),
            provider,
            model,
            feature_key
        )
        """
    )


def downgrade() -> None:
    op.drop_index(
        "uq_llm_usage_daily_aggregate_dimension",
        table_name="llm_usage_daily_aggregates",
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_llm_usage_daily_aggregate_dimension
        ON llm_usage_daily_aggregates (
            usage_date,
            COALESCE(company_id, -1),
            COALESCE(user_id, -1),
            provider,
            model,
            feature_key
        )
        """
    )
    op.drop_index("ix_llm_usage_daily_aggregates_team_id", table_name="llm_usage_daily_aggregates")
    op.drop_constraint(
        "fk_llm_usage_daily_aggregates_team_id_teams",
        "llm_usage_daily_aggregates",
        type_="foreignkey",
    )
    op.drop_column("llm_usage_daily_aggregates", "team_id")

    op.drop_index("ix_llm_usage_events_team_id", table_name="llm_usage_events")
    op.drop_constraint(
        "fk_llm_usage_events_team_id_teams",
        "llm_usage_events",
        type_="foreignkey",
    )
    op.drop_column("llm_usage_events", "team_id")

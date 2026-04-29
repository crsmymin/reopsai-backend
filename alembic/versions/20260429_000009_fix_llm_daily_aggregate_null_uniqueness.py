"""fix llm daily aggregate uniqueness with nullable dimensions

Revision ID: 20260429_000009
Revises: 20260428_000008
Create Date: 2026-04-29 10:00:00
"""

from alembic import op


revision = "20260429_000009"
down_revision = "20260428_000008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        WITH grouped AS (
            SELECT
                MIN(id) AS keep_id,
                ARRAY_AGG(id ORDER BY id) AS ids,
                usage_date,
                COALESCE(company_id, -1) AS company_key,
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
            GROUP BY usage_date, COALESCE(company_id, -1), COALESCE(user_id, -1), provider, model, feature_key
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
    op.drop_constraint(
        "uq_llm_usage_daily_aggregate_dimension",
        "llm_usage_daily_aggregates",
        type_="unique",
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


def downgrade() -> None:
    op.drop_index(
        "uq_llm_usage_daily_aggregate_dimension",
        table_name="llm_usage_daily_aggregates",
    )
    op.create_unique_constraint(
        "uq_llm_usage_daily_aggregate_dimension",
        "llm_usage_daily_aggregates",
        ["usage_date", "company_id", "user_id", "provider", "model", "feature_key"],
    )

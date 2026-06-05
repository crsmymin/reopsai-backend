"""add gpt-5.4 and gpt-5.4-mini llm model prices

Revision ID: 20260605_000019
Revises: 20260602_000018
Create Date: 2026-06-05
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "20260605_000019"
down_revision = "20260602_000018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    price_table = sa.table(
        "llm_model_prices",
        sa.column("provider", sa.String),
        sa.column("model", sa.String),
        sa.column("effective_from", sa.DateTime(timezone=True)),
        sa.column("currency", sa.String),
        sa.column("input_per_1m", sa.Numeric),
        sa.column("cached_input_per_1m", sa.Numeric),
        sa.column("output_per_1m", sa.Numeric),
        sa.column("reasoning_policy", sa.String),
        sa.column("source_url", sa.Text),
    )
    effective_from = datetime(2026, 6, 5, tzinfo=timezone.utc)
    op.bulk_insert(
        price_table,
        [
            {
                "provider": "openai",
                "model": "gpt-5.4",
                "effective_from": effective_from,
                "currency": "USD",
                "input_per_1m": 2.50,
                "cached_input_per_1m": 0.25,
                "output_per_1m": 15.00,
                "reasoning_policy": "reasoning_billed_as_output",
                "source_url": "https://openai.com/api/pricing/",
            },
            {
                "provider": "openai",
                "model": "gpt-5.4-mini",
                "effective_from": effective_from,
                "currency": "USD",
                "input_per_1m": 0.75,
                "cached_input_per_1m": 0.075,
                "output_per_1m": 4.50,
                "reasoning_policy": "reasoning_billed_as_output",
                "source_url": "https://openai.com/api/pricing/",
            },
        ],
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM llm_model_prices
        WHERE provider = 'openai'
          AND model IN ('gpt-5.4', 'gpt-5.4-mini')
          AND effective_from = '2026-06-05 00:00:00+00'
        """
    )

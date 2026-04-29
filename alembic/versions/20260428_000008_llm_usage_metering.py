"""llm usage metering and company token ledger

Revision ID: 20260428_000008
Revises: 20260428_000007
Create Date: 2026-04-28 18:00:00
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone


revision = "20260428_000008"
down_revision = "20260428_000007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_model_prices",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
        sa.Column("input_per_1m", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("cached_input_per_1m", sa.Numeric(12, 6), nullable=True),
        sa.Column("output_per_1m", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("reasoning_policy", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("provider", "model", "effective_from", name="uq_llm_model_prices_provider_model_effective"),
    )
    op.create_index("ix_llm_model_prices_provider", "llm_model_prices", ["provider"], unique=False)
    op.create_index("ix_llm_model_prices_model", "llm_model_prices", ["model"], unique=False)
    op.create_index("ix_llm_model_prices_effective_from", "llm_model_prices", ["effective_from"], unique=False)
    op.create_index("ix_llm_model_prices_effective_to", "llm_model_prices", ["effective_to"], unique=False)

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
    effective_from = datetime(2026, 4, 28, tzinfo=timezone.utc)
    op.bulk_insert(
        price_table,
        [
            {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "effective_from": effective_from,
                "currency": "USD",
                "input_per_1m": 0.15,
                "cached_input_per_1m": 0.075,
                "output_per_1m": 0.60,
                "reasoning_policy": "output_tokens_include_reasoning_when_reported",
                "source_url": "https://platform.openai.com/docs/pricing/",
            },
            {
                "provider": "openai",
                "model": "gpt-4o",
                "effective_from": effective_from,
                "currency": "USD",
                "input_per_1m": 2.50,
                "cached_input_per_1m": 1.25,
                "output_per_1m": 10.00,
                "reasoning_policy": "output_tokens_include_reasoning_when_reported",
                "source_url": "https://platform.openai.com/docs/pricing/",
            },
            {
                "provider": "openai",
                "model": "gpt-5",
                "effective_from": effective_from,
                "currency": "USD",
                "input_per_1m": 1.25,
                "cached_input_per_1m": 0.125,
                "output_per_1m": 10.00,
                "reasoning_policy": "reasoning_billed_as_output",
                "source_url": "https://platform.openai.com/docs/pricing/",
            },
            {
                "provider": "openai",
                "model": "gpt-5.2",
                "effective_from": effective_from,
                "currency": "USD",
                "input_per_1m": 1.75,
                "cached_input_per_1m": 0.175,
                "output_per_1m": 14.00,
                "reasoning_policy": "reasoning_billed_as_output",
                "source_url": "https://platform.openai.com/docs/pricing/",
            },
            {
                "provider": "gemini",
                "model": "gemini-2.0-flash",
                "effective_from": effective_from,
                "currency": "USD",
                "input_per_1m": 0.10,
                "cached_input_per_1m": 0.025,
                "output_per_1m": 0.40,
                "reasoning_policy": "output_tokens_include_thinking_when_reported",
                "source_url": "https://ai.google.dev/gemini-api/docs/pricing",
            },
            {
                "provider": "gemini",
                "model": "gemini-2.5-flash",
                "effective_from": effective_from,
                "currency": "USD",
                "input_per_1m": 0.30,
                "cached_input_per_1m": 0.03,
                "output_per_1m": 2.50,
                "reasoning_policy": "output_tokens_include_thinking_when_reported",
                "source_url": "https://ai.google.dev/gemini-api/docs/pricing",
            },
            {
                "provider": "gemini",
                "model": "gemini-2.5-flash-lite",
                "effective_from": effective_from,
                "currency": "USD",
                "input_per_1m": 0.10,
                "cached_input_per_1m": 0.01,
                "output_per_1m": 0.40,
                "reasoning_policy": "output_tokens_include_thinking_when_reported",
                "source_url": "https://ai.google.dev/gemini-api/docs/pricing",
            },
            {
                "provider": "gemini",
                "model": "gemini-2.5-pro",
                "effective_from": effective_from,
                "currency": "USD",
                "input_per_1m": 1.25,
                "cached_input_per_1m": 0.125,
                "output_per_1m": 10.00,
                "reasoning_policy": "output_tokens_include_thinking_when_reported",
                "source_url": "https://ai.google.dev/gemini-api/docs/pricing",
            },
        ],
    )

    op.create_table(
        "llm_usage_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("feature_key", sa.String(length=64), nullable=True),
        sa.Column("endpoint", sa.String(length=255), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("billable_weighted_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Numeric(14, 8), nullable=False, server_default="0"),
        sa.Column("price_catalog_id", sa.BigInteger(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["price_catalog_id"], ["llm_model_prices.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_llm_usage_events_company_id", "llm_usage_events", ["company_id"], unique=False)
    op.create_index("ix_llm_usage_events_user_id", "llm_usage_events", ["user_id"], unique=False)
    op.create_index("ix_llm_usage_events_provider", "llm_usage_events", ["provider"], unique=False)
    op.create_index("ix_llm_usage_events_model", "llm_usage_events", ["model"], unique=False)
    op.create_index("ix_llm_usage_events_feature_key", "llm_usage_events", ["feature_key"], unique=False)
    op.create_index("ix_llm_usage_events_request_id", "llm_usage_events", ["request_id"], unique=False)
    op.create_index("ix_llm_usage_events_price_catalog_id", "llm_usage_events", ["price_catalog_id"], unique=False)
    op.create_index("ix_llm_usage_events_occurred_at", "llm_usage_events", ["occurred_at"], unique=False)

    op.create_table(
        "company_token_ledger",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("delta_weighted_tokens", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("reference_event_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reference_event_id"], ["llm_usage_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_company_token_ledger_company_id", "company_token_ledger", ["company_id"], unique=False)
    op.create_index("ix_company_token_ledger_reason", "company_token_ledger", ["reason"], unique=False)
    op.create_index("ix_company_token_ledger_reference_event_id", "company_token_ledger", ["reference_event_id"], unique=False)
    op.create_index("ix_company_token_ledger_created_by", "company_token_ledger", ["created_by"], unique=False)
    op.create_index("ix_company_token_ledger_created_at", "company_token_ledger", ["created_at"], unique=False)

    op.create_table(
        "llm_usage_daily_aggregates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("feature_key", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("billable_weighted_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Numeric(14, 8), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "usage_date",
            "company_id",
            "user_id",
            "provider",
            "model",
            "feature_key",
            name="uq_llm_usage_daily_aggregate_dimension",
        ),
    )
    op.create_index("ix_llm_usage_daily_aggregates_usage_date", "llm_usage_daily_aggregates", ["usage_date"], unique=False)
    op.create_index("ix_llm_usage_daily_aggregates_company_id", "llm_usage_daily_aggregates", ["company_id"], unique=False)
    op.create_index("ix_llm_usage_daily_aggregates_user_id", "llm_usage_daily_aggregates", ["user_id"], unique=False)
    op.create_index("ix_llm_usage_daily_aggregates_provider", "llm_usage_daily_aggregates", ["provider"], unique=False)
    op.create_index("ix_llm_usage_daily_aggregates_model", "llm_usage_daily_aggregates", ["model"], unique=False)
    op.create_index("ix_llm_usage_daily_aggregates_feature_key", "llm_usage_daily_aggregates", ["feature_key"], unique=False)

    op.execute(
        """
        INSERT INTO company_token_ledger (company_id, delta_weighted_tokens, reason, note)
        SELECT id, 100000, 'initial_grant', 'Initial 100k weighted token grant'
        FROM companies
        WHERE status != 'deleted'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_llm_usage_daily_aggregates_feature_key", table_name="llm_usage_daily_aggregates")
    op.drop_index("ix_llm_usage_daily_aggregates_model", table_name="llm_usage_daily_aggregates")
    op.drop_index("ix_llm_usage_daily_aggregates_provider", table_name="llm_usage_daily_aggregates")
    op.drop_index("ix_llm_usage_daily_aggregates_user_id", table_name="llm_usage_daily_aggregates")
    op.drop_index("ix_llm_usage_daily_aggregates_company_id", table_name="llm_usage_daily_aggregates")
    op.drop_index("ix_llm_usage_daily_aggregates_usage_date", table_name="llm_usage_daily_aggregates")
    op.drop_table("llm_usage_daily_aggregates")

    op.drop_index("ix_company_token_ledger_created_at", table_name="company_token_ledger")
    op.drop_index("ix_company_token_ledger_created_by", table_name="company_token_ledger")
    op.drop_index("ix_company_token_ledger_reference_event_id", table_name="company_token_ledger")
    op.drop_index("ix_company_token_ledger_reason", table_name="company_token_ledger")
    op.drop_index("ix_company_token_ledger_company_id", table_name="company_token_ledger")
    op.drop_table("company_token_ledger")

    op.drop_index("ix_llm_usage_events_occurred_at", table_name="llm_usage_events")
    op.drop_index("ix_llm_usage_events_price_catalog_id", table_name="llm_usage_events")
    op.drop_index("ix_llm_usage_events_request_id", table_name="llm_usage_events")
    op.drop_index("ix_llm_usage_events_feature_key", table_name="llm_usage_events")
    op.drop_index("ix_llm_usage_events_model", table_name="llm_usage_events")
    op.drop_index("ix_llm_usage_events_provider", table_name="llm_usage_events")
    op.drop_index("ix_llm_usage_events_user_id", table_name="llm_usage_events")
    op.drop_index("ix_llm_usage_events_company_id", table_name="llm_usage_events")
    op.drop_table("llm_usage_events")

    op.drop_index("ix_llm_model_prices_effective_to", table_name="llm_model_prices")
    op.drop_index("ix_llm_model_prices_effective_from", table_name="llm_model_prices")
    op.drop_index("ix_llm_model_prices_model", table_name="llm_model_prices")
    op.drop_index("ix_llm_model_prices_provider", table_name="llm_model_prices")
    op.drop_table("llm_model_prices")

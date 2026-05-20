"""add persona tests and interviews parity columns

Revision ID: 20260520_000013
Revises: 20260519_000012
Create Date: 2026-05-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260520_000013"
down_revision = "20260519_000012"
branch_labels = None
depends_on = None


jsonb = postgresql.JSONB(astext_type=sa.Text())


def upgrade():
    for column in (
        sa.Column("interview_pack", jsonb, nullable=True),
        sa.Column("interview_pack_source_hash", sa.String(length=128), nullable=True),
        sa.Column("interview_pack_model", sa.String(length=128), nullable=True),
        sa.Column("interview_pack_version", sa.String(length=64), nullable=True),
        sa.Column("interview_pack_updated_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("personas", column)

    for column in (
        sa.Column("persona_goal_fit", sa.Text(), nullable=True),
        sa.Column("pin_comments", jsonb, nullable=True),
        sa.Column("flow_analysis", jsonb, nullable=True),
        sa.Column("confidence", jsonb, nullable=True),
        sa.Column("evidence_ids", jsonb, nullable=True),
        sa.Column("strengths", jsonb, nullable=True),
        sa.Column("risks", jsonb, nullable=True),
        sa.Column("recommendations", jsonb, nullable=True),
        sa.Column("screen_insights", jsonb, nullable=True),
    ):
        op.add_column("persona_ui_test_results", column)

    op.create_table(
        "persona_interviews",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("updated_by_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("product_description", sa.Text(), nullable=True),
        sa.Column("length", sa.String(length=32), server_default="quick", nullable=False),
        sa.Column("question_set", jsonb, nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("pack_model", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("persona_ids", jsonb, nullable=True),
        sa.Column("summary", jsonb, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "persona_interview_results",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("interview_id", sa.BigInteger(), sa.ForeignKey("persona_interviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("persona_id", sa.BigInteger(), sa.ForeignKey("personas.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="completed", nullable=False),
        sa.Column("persona_snapshot", jsonb, nullable=True),
        sa.Column("summary", jsonb, nullable=True),
        sa.Column("turns", jsonb, nullable=True),
        sa.Column("pack", jsonb, nullable=True),
        sa.Column("raw_response", jsonb, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("interview_id", "persona_id", name="uq_persona_interview_results_interview_persona"),
    )

    for table, columns in {
        "persona_interviews": ["company_id", "created_by_user_id", "status", "created_at", "deleted_at"],
        "persona_interview_results": ["company_id", "interview_id", "persona_id", "status", "created_at"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    for table, columns in {
        "persona_interview_results": ["company_id", "interview_id", "persona_id", "status", "created_at"],
        "persona_interviews": ["company_id", "created_by_user_id", "status", "created_at", "deleted_at"],
    }.items():
        for column in reversed(columns):
            op.drop_index(f"ix_{table}_{column}", table_name=table)
    op.drop_table("persona_interview_results")
    op.drop_table("persona_interviews")

    for column in reversed(
        [
            "persona_goal_fit",
            "pin_comments",
            "flow_analysis",
            "confidence",
            "evidence_ids",
            "strengths",
            "risks",
            "recommendations",
            "screen_insights",
        ]
    ):
        op.drop_column("persona_ui_test_results", column)

    for column in reversed(
        [
            "interview_pack",
            "interview_pack_source_hash",
            "interview_pack_model",
            "interview_pack_version",
            "interview_pack_updated_at",
        ]
    ):
        op.drop_column("personas", column)

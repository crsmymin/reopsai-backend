"""add persona interview sources

Revision ID: 20260602_000016
Revises: 20260601_000015
Create Date: 2026-06-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260602_000016"
down_revision = "20260601_000015"
branch_labels = None
depends_on = None


def upgrade():
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "persona_interview_sources",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("updated_by_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("participant_code", sa.String(length=128), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=32), server_default="ko", nullable=False),
        sa.Column("source_status", sa.String(length=32), server_default="uploaded", nullable=False),
        sa.Column("processing_error", sa.Text(), nullable=True),
        sa.Column("metadata", jsonb, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_persona_interview_sources_company_id", "persona_interview_sources", ["company_id"])
    op.create_index("ix_persona_interview_sources_team_id", "persona_interview_sources", ["team_id"])
    op.create_index("ix_persona_interview_sources_created_by_user_id", "persona_interview_sources", ["created_by_user_id"])
    op.create_index("ix_persona_interview_sources_title", "persona_interview_sources", ["title"])
    op.create_index("ix_persona_interview_sources_participant_code", "persona_interview_sources", ["participant_code"])
    op.create_index("ix_persona_interview_sources_source_status", "persona_interview_sources", ["source_status"])
    op.create_index("ix_persona_interview_sources_created_at", "persona_interview_sources", ["created_at"])
    op.create_index("ix_persona_interview_sources_deleted_at", "persona_interview_sources", ["deleted_at"])


def downgrade():
    op.drop_index("ix_persona_interview_sources_deleted_at", table_name="persona_interview_sources")
    op.drop_index("ix_persona_interview_sources_created_at", table_name="persona_interview_sources")
    op.drop_index("ix_persona_interview_sources_source_status", table_name="persona_interview_sources")
    op.drop_index("ix_persona_interview_sources_participant_code", table_name="persona_interview_sources")
    op.drop_index("ix_persona_interview_sources_title", table_name="persona_interview_sources")
    op.drop_index("ix_persona_interview_sources_created_by_user_id", table_name="persona_interview_sources")
    op.drop_index("ix_persona_interview_sources_team_id", table_name="persona_interview_sources")
    op.drop_index("ix_persona_interview_sources_company_id", table_name="persona_interview_sources")
    op.drop_table("persona_interview_sources")

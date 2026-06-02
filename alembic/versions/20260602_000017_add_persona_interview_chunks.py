"""add persona interview chunks

Revision ID: 20260602_000017
Revises: 20260602_000016
Create Date: 2026-06-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260602_000017"
down_revision = "20260602_000016"
branch_labels = None
depends_on = None


def upgrade():
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "persona_interview_chunks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "source_id",
            sa.BigInteger(),
            sa.ForeignKey("persona_interview_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.BigInteger(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_chunk_id", sa.String(length=64), nullable=False),
        sa.Column("experience_text", sa.Text(), nullable=False),
        sa.Column("source_quote", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("target_variables", jsonb, nullable=False, server_default="[]"),
        sa.Column("behavioral_signals", jsonb, nullable=True),
        sa.Column("tags", jsonb, nullable=True),
        sa.Column("evidence_strength", sa.String(length=16), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("embedding_vector_id", sa.String(length=255), nullable=True),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_persona_interview_chunks_source_id", "persona_interview_chunks", ["source_id"])
    op.create_index("ix_persona_interview_chunks_company_id", "persona_interview_chunks", ["company_id"])
    op.create_index("ix_persona_interview_chunks_external_chunk_id", "persona_interview_chunks", ["external_chunk_id"])
    op.create_index("ix_persona_interview_chunks_embedding_vector_id", "persona_interview_chunks", ["embedding_vector_id"])
    op.create_unique_constraint(
        "uq_persona_interview_chunks_source_external",
        "persona_interview_chunks",
        ["source_id", "external_chunk_id"],
    )


def downgrade():
    op.drop_constraint("uq_persona_interview_chunks_source_external", "persona_interview_chunks", type_="unique")
    op.drop_index("ix_persona_interview_chunks_embedding_vector_id", table_name="persona_interview_chunks")
    op.drop_index("ix_persona_interview_chunks_external_chunk_id", table_name="persona_interview_chunks")
    op.drop_index("ix_persona_interview_chunks_company_id", table_name="persona_interview_chunks")
    op.drop_index("ix_persona_interview_chunks_source_id", table_name="persona_interview_chunks")
    op.drop_table("persona_interview_chunks")

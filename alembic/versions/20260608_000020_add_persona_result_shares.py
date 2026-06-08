"""add persona result share links

Revision ID: 20260608_000020
Revises: 20260605_000019
Create Date: 2026-06-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260608_000020"
down_revision = "20260605_000019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persona_result_shares",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("token_salt", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_persona_result_shares_token_hash"),
    )
    op.create_index(op.f("ix_persona_result_shares_company_id"), "persona_result_shares", ["company_id"], unique=False)
    op.create_index(op.f("ix_persona_result_shares_created_at"), "persona_result_shares", ["created_at"], unique=False)
    op.create_index(op.f("ix_persona_result_shares_created_by_user_id"), "persona_result_shares", ["created_by_user_id"], unique=False)
    op.create_index(op.f("ix_persona_result_shares_expires_at"), "persona_result_shares", ["expires_at"], unique=False)
    op.create_index(op.f("ix_persona_result_shares_resource_id"), "persona_result_shares", ["resource_id"], unique=False)
    op.create_index("ix_persona_result_shares_resource", "persona_result_shares", ["company_id", "resource_type", "resource_id"], unique=False)
    op.create_index(op.f("ix_persona_result_shares_resource_type"), "persona_result_shares", ["resource_type"], unique=False)
    op.create_index(op.f("ix_persona_result_shares_revoked_at"), "persona_result_shares", ["revoked_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_persona_result_shares_revoked_at"), table_name="persona_result_shares")
    op.drop_index(op.f("ix_persona_result_shares_resource_type"), table_name="persona_result_shares")
    op.drop_index("ix_persona_result_shares_resource", table_name="persona_result_shares")
    op.drop_index(op.f("ix_persona_result_shares_resource_id"), table_name="persona_result_shares")
    op.drop_index(op.f("ix_persona_result_shares_expires_at"), table_name="persona_result_shares")
    op.drop_index(op.f("ix_persona_result_shares_created_by_user_id"), table_name="persona_result_shares")
    op.drop_index(op.f("ix_persona_result_shares_created_at"), table_name="persona_result_shares")
    op.drop_index(op.f("ix_persona_result_shares_company_id"), table_name="persona_result_shares")
    op.drop_table("persona_result_shares")

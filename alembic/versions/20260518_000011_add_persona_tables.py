"""add persona migration tables

Revision ID: 20260518_000011
Revises: 20260429_000010
Create Date: 2026-05-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260518_000011"
down_revision = "20260429_000010"
branch_labels = None
depends_on = None


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def _ownership_columns():
    return (
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("updated_by_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )


def upgrade():
    jsonb = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "persona_assets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        *_ownership_columns(),
        sa.Column("asset_type", sa.String(length=64), nullable=False),
        sa.Column("storage_backend", sa.String(length=32), server_default="local", nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("metadata", jsonb, nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "persona_folders",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        *_ownership_columns(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=64), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("company_id", "name", name="uq_persona_folders_company_name"),
    )

    op.create_table(
        "personas",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        *_ownership_columns(),
        sa.Column("folder_id", sa.BigInteger(), sa.ForeignKey("persona_folders.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_external_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("gender", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("personality", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=32), server_default="ko", nullable=False),
        sa.Column("source_type", sa.String(length=64), server_default="manual", nullable=False),
        sa.Column("source_data", jsonb, nullable=True),
        sa.Column("image_asset_id", sa.BigInteger(), sa.ForeignKey("persona_assets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("image_prompt", sa.Text(), nullable=True),
        sa.Column("schema_version", sa.Integer(), server_default="3", nullable=False),
        sa.Column("locale", sa.String(length=32), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("profile", jsonb, nullable=True),
        sa.Column("telecom_profile", jsonb, nullable=True),
        sa.Column("generation_metadata", jsonb, nullable=True),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "persona_memory_settings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("persona_id", sa.BigInteger(), sa.ForeignKey("personas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("enable_memory", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("memory_strength", sa.Integer(), server_default="70", nullable=False),
        sa.Column("apply_to_chat", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("apply_to_tests", sa.Boolean(), server_default="true", nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("persona_id", name="uq_persona_memory_settings_persona"),
    )

    op.create_table(
        "persona_activities",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("persona_id", sa.BigInteger(), sa.ForeignKey("personas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activity_type", sa.String(length=64), nullable=False),
        sa.Column("activity_id", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("was_validated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("was_correct", sa.Boolean(), nullable=True),
        sa.Column("metadata", jsonb, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "persona_learned_traits",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("persona_id", sa.BigInteger(), sa.ForeignKey("personas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trait", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("source_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("sources", jsonb, nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "persona_figma_accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("figma_user_id", sa.String(length=255), nullable=False),
        sa.Column("figma_email", sa.String(length=320), nullable=True),
        sa.Column("figma_handle", sa.String(length=255), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("figma_avatar_url", sa.Text(), nullable=True),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("company_id", "created_by_user_id", "figma_user_id", name="uq_persona_figma_accounts_user_figma"),
    )

    op.create_table(
        "persona_figma_files",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("figma_account_id", sa.BigInteger(), sa.ForeignKey("persona_figma_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("figma_file_key", sa.String(length=255), nullable=False),
        sa.Column("figma_file_name", sa.Text(), nullable=False),
        sa.Column("figma_file_link", sa.Text(), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("sync_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("company_id", "figma_file_key", name="uq_persona_figma_files_company_key"),
    )

    op.create_table(
        "persona_figma_flows",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("figma_file_id", sa.BigInteger(), sa.ForeignKey("persona_figma_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("figma_page_id", sa.String(length=255), nullable=True),
        sa.Column("figma_page_name", sa.Text(), nullable=True),
        sa.Column("figma_start_node_id", sa.String(length=255), nullable=False),
        sa.Column("figma_flow_name", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("metadata", jsonb, nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("figma_file_id", "figma_start_node_id", name="uq_persona_figma_flows_file_start"),
    )

    op.create_table(
        "persona_ui_tests",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        *_ownership_columns(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("device_type", sa.String(length=32), server_default="pc", nullable=False),
        sa.Column("validation_type", sa.String(length=32), server_default="single", nullable=False),
        sa.Column("scope_type", sa.String(length=32), server_default="screen", nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("persona_count", sa.Integer(), nullable=True),
        sa.Column("screen_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("summary", jsonb, nullable=True),
        sa.Column("source_data", jsonb, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "persona_ui_test_results",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_id", sa.BigInteger(), sa.ForeignKey("persona_ui_tests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("persona_id", sa.BigInteger(), sa.ForeignKey("personas.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="completed", nullable=False),
        sa.Column("screen_index", sa.Integer(), nullable=True),
        sa.Column("choice", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("scores", jsonb, nullable=True),
        sa.Column("feedback", jsonb, nullable=True),
        sa.Column("persona_snapshot", jsonb, nullable=True),
        sa.Column("evidence", jsonb, nullable=True),
        sa.Column("raw_response", jsonb, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("test_id", "persona_id", name="uq_persona_ui_test_results_test_persona"),
    )

    op.create_table(
        "persona_ab_tests",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        *_ownership_columns(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("service_context", sa.Text(), nullable=True),
        sa.Column("mode", sa.String(length=32), server_default="single", nullable=False),
        sa.Column("screens", jsonb, nullable=True),
        sa.Column("transitions", jsonb, nullable=True),
        sa.Column("context_data", jsonb, nullable=True),
        sa.Column("summary", jsonb, nullable=True),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("enable_consistency_validation", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("consistency_run_count", sa.Integer(), server_default="3", nullable=False),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "persona_ab_test_results",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ab_test_id", sa.BigInteger(), sa.ForeignKey("persona_ab_tests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("persona_id", sa.BigInteger(), sa.ForeignKey("personas.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="completed", nullable=False),
        sa.Column("persona_snapshot", jsonb, nullable=True),
        sa.Column("scores", jsonb, nullable=True),
        sa.Column("feedback", jsonb, nullable=True),
        sa.Column("confidence", jsonb, nullable=True),
        sa.Column("evidence_ids", jsonb, nullable=True),
        sa.Column("raw_response", jsonb, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("ab_test_id", "persona_id", name="uq_persona_ab_test_results_test_persona"),
    )

    for table, columns in {
        "persona_assets": ["company_id", "created_by_user_id", "asset_type", "created_at"],
        "persona_folders": ["company_id", "created_by_user_id", "created_at", "deleted_at"],
        "personas": ["company_id", "created_by_user_id", "folder_id", "source_type", "created_at", "deleted_at"],
        "persona_memory_settings": ["company_id", "persona_id"],
        "persona_activities": ["company_id", "persona_id", "activity_type", "created_at"],
        "persona_learned_traits": ["company_id", "persona_id", "category", "is_active"],
        "persona_figma_accounts": ["company_id", "created_by_user_id", "figma_user_id", "deleted_at"],
        "persona_figma_files": ["company_id", "figma_account_id", "figma_file_key", "sync_status"],
        "persona_figma_flows": ["company_id", "figma_file_id", "active"],
        "persona_ui_tests": ["company_id", "created_by_user_id", "source_type", "status", "created_at", "deleted_at"],
        "persona_ui_test_results": ["company_id", "test_id", "persona_id", "status", "created_at"],
        "persona_ab_tests": ["company_id", "created_by_user_id", "status", "created_at", "deleted_at"],
        "persona_ab_test_results": ["company_id", "ab_test_id", "persona_id", "status", "created_at"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    for table in (
        "persona_ab_test_results",
        "persona_ab_tests",
        "persona_ui_test_results",
        "persona_ui_tests",
        "persona_figma_flows",
        "persona_figma_files",
        "persona_figma_accounts",
        "persona_learned_traits",
        "persona_activities",
        "persona_memory_settings",
        "personas",
        "persona_folders",
        "persona_assets",
    ):
        op.drop_table(table)

"""initial schema

Revision ID: 20260413_000001
Revises:
Create Date: 2026-04-13 15:10:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260413_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("google_id", sa.String(length=255), nullable=True),
        sa.Column("tier", sa.String(length=32), nullable=False, server_default="free"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("google_id", name="uq_users_google_id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)
    op.create_index("ix_users_tier", "users", ["tier"], unique=False)

    op.create_table(
        "projects",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=True),
        sa.Column("product_url", sa.Text(), nullable=True),
        sa.Column("keywords", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("slug", name="uq_projects_slug"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"], unique=False)
    op.create_index("ix_projects_slug", "projects", ["slug"], unique=False)

    op.create_table(
        "studies",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=True),
        sa.Column("initial_input", sa.Text(), nullable=True),
        sa.Column("keywords", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("methodologies", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("participant_count", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("timeline", sa.Text(), nullable=True),
        sa.Column("budget", sa.Text(), nullable=True),
        sa.Column("target_audience", sa.Text(), nullable=True),
        sa.Column("additional_requirements", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("slug", name="uq_studies_slug"),
    )
    op.create_index("ix_studies_project_id", "studies", ["project_id"], unique=False)
    op.create_index("ix_studies_slug", "studies", ["slug"], unique=False)

    op.create_table(
        "artifacts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("study_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_artifacts_study_id", "artifacts", ["study_id"], unique=False)
    op.create_index("ix_artifacts_owner_id", "artifacts", ["owner_id"], unique=False)
    op.create_index("ix_artifacts_artifact_type", "artifacts", ["artifact_type"], unique=False)
    op.create_index("ix_artifacts_status", "artifacts", ["status"], unique=False)

    op.create_table(
        "teams",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_teams_owner_id", "teams", ["owner_id"], unique=False)

    op.create_table(
        "team_members",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="member"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
    )
    op.create_index("ix_team_members_team_id", "team_members", ["team_id"], unique=False)
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"], unique=False)

    op.create_table(
        "study_schedules",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("study_id", sa.BigInteger(), nullable=False),
        sa.Column("final_participants", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("study_id", name="uq_study_schedules_study_id"),
    )
    op.create_index("ix_study_schedules_study_id", "study_schedules", ["study_id"], unique=False)

    op.create_table(
        "user_feedback",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("vote", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("study_id", sa.BigInteger(), nullable=True),
        sa.Column("study_name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_user_feedback_category", "user_feedback", ["category"], unique=False)
    op.create_index("ix_user_feedback_user_id", "user_feedback", ["user_id"], unique=False)
    op.create_index("ix_user_feedback_study_id", "user_feedback", ["study_id"], unique=False)

    op.create_table(
        "artifact_edit_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("artifact_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("before_markdown", sa.Text(), nullable=False),
        sa.Column("after_markdown", sa.Text(), nullable=False),
        sa.Column("selection_from", sa.Integer(), nullable=True),
        sa.Column("selection_to", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "(selection_from IS NULL AND selection_to IS NULL) "
            "OR (selection_from IS NOT NULL AND selection_to IS NOT NULL AND selection_from < selection_to)",
            name="chk_artifact_edit_history_selection_span",
        ),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_artifact_edit_history_artifact_id_created_at",
        "artifact_edit_history",
        ["artifact_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_artifact_edit_history_user_id_created_at",
        "artifact_edit_history",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_edit_history_user_id_created_at", table_name="artifact_edit_history")
    op.drop_index("ix_artifact_edit_history_artifact_id_created_at", table_name="artifact_edit_history")
    op.drop_table("artifact_edit_history")

    op.drop_index("ix_user_feedback_study_id", table_name="user_feedback")
    op.drop_index("ix_user_feedback_user_id", table_name="user_feedback")
    op.drop_index("ix_user_feedback_category", table_name="user_feedback")
    op.drop_table("user_feedback")

    op.drop_index("ix_study_schedules_study_id", table_name="study_schedules")
    op.drop_table("study_schedules")

    op.drop_index("ix_team_members_user_id", table_name="team_members")
    op.drop_index("ix_team_members_team_id", table_name="team_members")
    op.drop_table("team_members")

    op.drop_index("ix_teams_owner_id", table_name="teams")
    op.drop_table("teams")

    op.drop_index("ix_artifacts_status", table_name="artifacts")
    op.drop_index("ix_artifacts_artifact_type", table_name="artifacts")
    op.drop_index("ix_artifacts_owner_id", table_name="artifacts")
    op.drop_index("ix_artifacts_study_id", table_name="artifacts")
    op.drop_table("artifacts")

    op.drop_index("ix_studies_slug", table_name="studies")
    op.drop_index("ix_studies_project_id", table_name="studies")
    op.drop_table("studies")

    op.drop_index("ix_projects_slug", table_name="projects")
    op.drop_index("ix_projects_owner_id", table_name="projects")
    op.drop_table("projects")

    op.drop_index("ix_users_tier", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")


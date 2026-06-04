"""use active-only unique index for persona folder names

Revision ID: 20260602_000018
Revises: 20260602_000017
Create Date: 2026-06-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260602_000018"
down_revision = "20260602_000017"
branch_labels = None
depends_on = None


ACTIVE_FOLDER_NAME_INDEX = "ux_persona_folders_company_name_active"
LEGACY_FOLDER_NAME_CONSTRAINT = "uq_persona_folders_company_name"


def _archive_deleted_folder_names():
    op.execute(
        """
        UPDATE persona_folders
        SET name = CONCAT(LEFT(name, 220), '__deleted_', id::text)
        WHERE deleted_at IS NOT NULL
        """
    )


def upgrade():
    op.drop_constraint(LEGACY_FOLDER_NAME_CONSTRAINT, "persona_folders", type_="unique")
    _archive_deleted_folder_names()
    op.create_index(
        ACTIVE_FOLDER_NAME_INDEX,
        "persona_folders",
        ["company_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade():
    op.drop_index(ACTIVE_FOLDER_NAME_INDEX, table_name="persona_folders")
    _archive_deleted_folder_names()
    op.create_unique_constraint(
        LEGACY_FOLDER_NAME_CONSTRAINT,
        "persona_folders",
        ["company_id", "name"],
    )

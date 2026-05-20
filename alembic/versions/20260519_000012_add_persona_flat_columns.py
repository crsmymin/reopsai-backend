"""add persona flat columns

Revision ID: 20260519_000012
Revises: 20260518_000011
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260519_000012"
down_revision = "20260518_000011"
branch_labels = None
depends_on = None


PERSONA_FLAT_COLUMNS = (
    sa.Column("image_data", sa.LargeBinary(), nullable=True),
    sa.Column("image_mime_type", sa.String(length=255), nullable=True),
    sa.Column("attitudes", sa.Text(), nullable=True),
    sa.Column("biography", sa.Text(), nullable=True),
    sa.Column("demeanour", sa.Text(), nullable=True),
    sa.Column("ethnicity", sa.Text(), nullable=True),
    sa.Column("interests", sa.Text(), nullable=True),
    sa.Column("generation", sa.String(length=64), nullable=True),
    sa.Column("motivation", sa.Text(), nullable=True),
    sa.Column("upbringing", sa.Text(), nullable=True),
    sa.Column("quote", sa.Text(), nullable=True),
    sa.Column("additional_info", sa.Text(), nullable=True),
    sa.Column("behaviours", sa.Text(), nullable=True),
    sa.Column("cultural_background", sa.Text(), nullable=True),
    sa.Column("current_city", sa.Text(), nullable=True),
    sa.Column("current_country", sa.Text(), nullable=True),
    sa.Column("income", sa.Text(), nullable=True),
    sa.Column("locations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column("organisation", sa.Text(), nullable=True),
    sa.Column("preferences", sa.Text(), nullable=True),
    sa.Column("role_area", sa.Text(), nullable=True),
    sa.Column("role_level", sa.Text(), nullable=True),
    sa.Column("sector", sa.Text(), nullable=True),
    sa.Column("social_context", sa.Text(), nullable=True),
    sa.Column("telecom_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column("telecom_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column("ux_interaction", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column("telecom_behavior_dimensions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
)


def upgrade():
    for column in PERSONA_FLAT_COLUMNS:
        op.add_column("personas", column)


def downgrade():
    for column_name in reversed([column.name for column in PERSONA_FLAT_COLUMNS]):
        op.drop_column("personas", column_name)

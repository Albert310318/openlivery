"""Add tenant-scoped welcome flyer configuration."""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import (
    create_index_if_missing,
    create_unique_constraint_if_missing,
    require_columns,
    table_exists,
)

revision = "0039_restaurant_welcome_flyer"
down_revision = "0038_payment_confirmed_by"
branch_labels = None
depends_on = None


def upgrade():
    if not table_exists("restaurant_welcome_flyers"):
        op.create_table(
            "restaurant_welcome_flyers",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("client_id", sa.UUID(), nullable=False),
            sa.Column("image_data", sa.LargeBinary(), nullable=False),
            sa.Column("mime_type", sa.String(length=40), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
            sa.Column("message", sa.Text(), server_default="", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("client_id", name="uq_restaurant_welcome_flyers_client_id"),
        )
    else:
        require_columns(
            "restaurant_welcome_flyers",
            ("id", "client_id", "image_data", "mime_type", "filename", "enabled", "message", "created_at", "updated_at"),
        )
    create_unique_constraint_if_missing(
        "uq_restaurant_welcome_flyers_client_id",
        "restaurant_welcome_flyers",
        ["client_id"],
    )
    create_index_if_missing(
        "ix_restaurant_welcome_flyers_client_id",
        "restaurant_welcome_flyers",
        ["client_id"],
    )


def downgrade():
    op.drop_index("ix_restaurant_welcome_flyers_client_id", table_name="restaurant_welcome_flyers")
    op.drop_table("restaurant_welcome_flyers")

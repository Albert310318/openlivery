"""Track delivery dispatch attempts and provider confirmations."""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import (
    create_index_if_missing,
    create_unique_constraint_if_missing,
    require_columns,
    table_exists,
)

revision = "0041_delivery_notifications"
down_revision = "0040_message_media"
branch_labels = None
depends_on = None


def upgrade():
    if not table_exists("restaurant_delivery_notifications"):
        op.create_table(
            "restaurant_delivery_notifications",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("client_id", sa.UUID(), nullable=False),
            sa.Column("order_id", sa.UUID(), nullable=False),
            sa.Column("recipient", sa.String(length=40), nullable=False),
            sa.Column("provider", sa.String(length=30), nullable=True),
            sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
            sa.Column("external_message_id", sa.String(length=255), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["order_id"], ["restaurant_orders.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("order_id", name="uq_restaurant_delivery_notifications_order"),
        )
    else:
        require_columns(
            "restaurant_delivery_notifications",
            (
                "id", "client_id", "order_id", "recipient", "provider", "status",
                "external_message_id", "error_message", "attempts", "sent_at", "created_at", "updated_at",
            ),
        )
    create_unique_constraint_if_missing(
        "uq_restaurant_delivery_notifications_order",
        "restaurant_delivery_notifications",
        ["order_id"],
    )
    create_index_if_missing(
        "ix_restaurant_delivery_notifications_client_id",
        "restaurant_delivery_notifications",
        ["client_id"],
    )
    create_index_if_missing(
        "ix_restaurant_delivery_notifications_order_id",
        "restaurant_delivery_notifications",
        ["order_id"],
    )


def downgrade():
    op.drop_index("ix_restaurant_delivery_notifications_order_id", table_name="restaurant_delivery_notifications")
    op.drop_index("ix_restaurant_delivery_notifications_client_id", table_name="restaurant_delivery_notifications")
    op.drop_table("restaurant_delivery_notifications")

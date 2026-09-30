"""Prepare restaurant payment notification matching."""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import (
    add_column_if_missing,
    create_index_if_missing,
    require_columns,
    table_exists,
)


revision = "0032_payment_notifications"
down_revision = "0031_waiter_item_cancellation"
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        "restaurant_payment_methods",
        sa.Column("notification_email", sa.String(length=320), nullable=True),
    )
    created = not table_exists("payment_notifications")
    if created:
        op.create_table(
            "payment_notifications",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("client_id", sa.Uuid(), nullable=False),
            sa.Column("payment_method_id", sa.Uuid(), nullable=False),
            sa.Column("notification_email", sa.String(length=320), nullable=True),
            sa.Column("external_operation_id", sa.String(length=180), nullable=True),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("sender", sa.String(length=320), nullable=True),
            sa.Column("subject", sa.String(length=500), nullable=True),
            sa.Column("metadata", sa.JSON(), server_default="{}", nullable=False),
            sa.Column("status", sa.String(length=20), server_default="unmatched", nullable=False),
            sa.Column("matched_order_id", sa.Uuid(), nullable=True),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["payment_method_id"], ["restaurant_payment_methods.id"]),
            sa.ForeignKeyConstraint(["matched_order_id"], ["restaurant_orders.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "client_id",
                "payment_method_id",
                "external_operation_id",
                name="uq_payment_notifications_receiver_operation",
            ),
            sa.CheckConstraint(
                "status IN ('unmatched', 'ambiguous', 'matched')",
                name="ck_payment_notifications_status",
            ),
        )
    else:
        require_columns(
            "payment_notifications",
            (
                "id",
                "client_id",
                "payment_method_id",
                "notification_email",
                "external_operation_id",
                "amount",
                "occurred_at",
                "received_at",
                "sender",
                "subject",
                "metadata",
                "status",
                "matched_order_id",
                "processed_at",
            ),
        )

    create_index_if_missing("ix_payment_notifications_client_id", "payment_notifications", ["client_id"])
    create_index_if_missing(
        "ix_payment_notifications_payment_method_id",
        "payment_notifications",
        ["payment_method_id"],
    )
    create_index_if_missing("ix_payment_notifications_occurred_at", "payment_notifications", ["occurred_at"])
    create_index_if_missing(
        "ix_payment_notifications_matched_order_id",
        "payment_notifications",
        ["matched_order_id"],
    )
    create_index_if_missing(
        "ix_payment_notifications_client_status",
        "payment_notifications",
        ["client_id", "status"],
    )


def downgrade():
    op.drop_index("ix_payment_notifications_client_status", table_name="payment_notifications")
    op.drop_index("ix_payment_notifications_matched_order_id", table_name="payment_notifications")
    op.drop_index("ix_payment_notifications_occurred_at", table_name="payment_notifications")
    op.drop_index("ix_payment_notifications_payment_method_id", table_name="payment_notifications")
    op.drop_index("ix_payment_notifications_client_id", table_name="payment_notifications")
    op.drop_table("payment_notifications")
    op.drop_column("restaurant_payment_methods", "notification_email")

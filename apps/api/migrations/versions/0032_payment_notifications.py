"""Prepare restaurant payment notification matching."""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import (
    add_column_if_missing,
    create_index_if_missing,
    create_check_constraint_if_missing,
    create_foreign_key_if_missing,
    create_unique_constraint_if_missing,
    require_columns,
    table_exists,
)
from migrations.restaurant_schema_bridge import ensure_historical_restaurant_schema


revision = "0032_payment_notifications"
down_revision = "0031_waiter_item_cancellation"
branch_labels = None
depends_on = None


PAYMENT_METHOD_COLUMNS = (
    "id",
    "client_id",
    "method",
    "display_name",
    "instructions",
    "account_name",
    "account_number",
    "qr_image_url",
    "receipt_required",
    "is_active",
    "created_at",
    "updated_at",
)


def _ensure_payment_methods_table():
    """Restore the 0027 parent table when a bridged 0031 DB lacks it."""
    if not table_exists("restaurant_payment_methods"):
        op.create_table(
            "restaurant_payment_methods",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("client_id", sa.Uuid(), nullable=False),
            sa.Column("method", sa.String(length=30), nullable=False),
            sa.Column("display_name", sa.String(length=180), server_default="", nullable=False),
            sa.Column("instructions", sa.Text(), server_default="", nullable=False),
            sa.Column("account_name", sa.String(length=180), server_default="", nullable=False),
            sa.Column("account_number", sa.String(length=180), server_default="", nullable=False),
            sa.Column("qr_image_url", sa.Text(), nullable=True),
            sa.Column("receipt_required", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("client_id", "method", name="uq_restaurant_payment_methods_client_method"),
            sa.CheckConstraint(
                "method IN ('cash', 'yape', 'plin', 'transfer', 'card', 'other')",
                name="ck_restaurant_payment_methods_method",
            ),
        )
    else:
        require_columns("restaurant_payment_methods", PAYMENT_METHOD_COLUMNS)

    create_unique_constraint_if_missing(
        "uq_restaurant_payment_methods_client_method",
        "restaurant_payment_methods",
        ["client_id", "method"],
    )
    create_check_constraint_if_missing(
        "ck_restaurant_payment_methods_method",
        "restaurant_payment_methods",
        "method IN ('cash', 'yape', 'plin', 'transfer', 'card', 'other')",
    )
    create_foreign_key_if_missing(
        "restaurant_payment_methods_client_id_fkey",
        "restaurant_payment_methods",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="CASCADE",
    )
    create_index_if_missing(
        "ix_restaurant_payment_methods_client_id",
        "restaurant_payment_methods",
        ["client_id"],
    )


def upgrade():
    ensure_historical_restaurant_schema()
    _ensure_payment_methods_table()
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

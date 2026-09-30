"""Store manual restaurant payment review and submitted voucher data."""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import add_column_if_missing


revision = "0036_manual_payment_review"
down_revision = "0035_order_reported_amount"
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        "restaurant_orders",
        sa.Column("payment_reported_at", sa.DateTime(timezone=True), nullable=True),
    )
    add_column_if_missing(
        "restaurant_orders",
        sa.Column("payment_review_status", sa.String(length=20), nullable=False, server_default="pending"),
    )
    add_column_if_missing(
        "restaurant_orders",
        sa.Column("payment_rejection_reason", sa.Text(), nullable=True),
    )
    add_column_if_missing(
        "restaurant_orders",
        sa.Column("payment_rejected_at", sa.DateTime(timezone=True), nullable=True),
    )
    add_column_if_missing(
        "restaurant_orders",
        sa.Column("payment_receipt_data", sa.LargeBinary(), nullable=True),
    )
    add_column_if_missing(
        "restaurant_orders",
        sa.Column("payment_receipt_filename", sa.String(length=255), nullable=True),
    )
    add_column_if_missing(
        "restaurant_orders",
        sa.Column("payment_receipt_mime", sa.String(length=120), nullable=True),
    )


def downgrade():
    op.drop_column("restaurant_orders", "payment_receipt_mime")
    op.drop_column("restaurant_orders", "payment_receipt_filename")
    op.drop_column("restaurant_orders", "payment_receipt_data")
    op.drop_column("restaurant_orders", "payment_rejected_at")
    op.drop_column("restaurant_orders", "payment_rejection_reason")
    op.drop_column("restaurant_orders", "payment_review_status")
    op.drop_column("restaurant_orders", "payment_reported_at")

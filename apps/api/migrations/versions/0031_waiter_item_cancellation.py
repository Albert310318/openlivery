"""Allow waiter order lines to be cancelled without deleting audit data."""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import add_column_if_missing


revision = "0031_waiter_item_cancellation"
down_revision = "0031_restaurant_staff"
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        "restaurant_order_items",
        sa.Column("is_cancelled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    add_column_if_missing(
        "restaurant_order_items",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("restaurant_order_items", "cancelled_at")
    op.drop_column("restaurant_order_items", "is_cancelled")

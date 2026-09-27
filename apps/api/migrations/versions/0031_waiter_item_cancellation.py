"""Allow waiter order lines to be cancelled without deleting audit data."""

from alembic import op
import sqlalchemy as sa


revision = "0031_waiter_item_cancellation"
down_revision = "0030_user_email_verification"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("restaurant_order_items", sa.Column("is_cancelled", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("restaurant_order_items", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("restaurant_order_items", "cancelled_at")
    op.drop_column("restaurant_order_items", "is_cancelled")

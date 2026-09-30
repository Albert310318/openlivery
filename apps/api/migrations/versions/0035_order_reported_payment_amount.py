"""Store the amount reported by a restaurant customer before IMAP verification."""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import add_column_if_missing


revision = "0035_order_reported_amount"
down_revision = "0034_payment_identity"
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        "restaurant_orders",
        sa.Column("reported_payment_amount", sa.Numeric(12, 2), nullable=True),
    )


def downgrade():
    op.drop_column("restaurant_orders", "reported_payment_amount")

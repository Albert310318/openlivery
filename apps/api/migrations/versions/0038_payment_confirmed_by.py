"""Track the user who confirms or collects a restaurant payment."""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import (
    add_column_if_missing,
    create_foreign_key_if_missing,
    create_index_if_missing,
)


revision = "0038_payment_confirmed_by"
down_revision = "0037_delivery_whatsapp"
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        "restaurant_orders",
        sa.Column("payment_confirmed_by_user_id", sa.UUID(), nullable=True),
    )
    create_foreign_key_if_missing(
        "fk_restaurant_orders_payment_confirmed_by_user_id_users",
        "restaurant_orders",
        "users",
        ["payment_confirmed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    create_index_if_missing(
        "ix_restaurant_orders_payment_confirmed_by_user_id",
        "restaurant_orders",
        ["payment_confirmed_by_user_id"],
    )


def downgrade():
    op.drop_index("ix_restaurant_orders_payment_confirmed_by_user_id", table_name="restaurant_orders")
    op.drop_constraint("fk_restaurant_orders_payment_confirmed_by_user_id_users", "restaurant_orders", type_="foreignkey")
    op.drop_column("restaurant_orders", "payment_confirmed_by_user_id")

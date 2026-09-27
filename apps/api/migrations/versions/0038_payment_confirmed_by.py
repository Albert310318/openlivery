"""Track the user who confirms or collects a restaurant payment."""

from alembic import op
import sqlalchemy as sa


revision = "0038_payment_confirmed_by"
down_revision = "0037_delivery_whatsapp"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("restaurant_orders", sa.Column("payment_confirmed_by_user_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_restaurant_orders_payment_confirmed_by_user_id_users",
        "restaurant_orders",
        "users",
        ["payment_confirmed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_restaurant_orders_payment_confirmed_by_user_id",
        "restaurant_orders",
        ["payment_confirmed_by_user_id"],
    )


def downgrade():
    op.drop_index("ix_restaurant_orders_payment_confirmed_by_user_id", table_name="restaurant_orders")
    op.drop_constraint("fk_restaurant_orders_payment_confirmed_by_user_id_users", "restaurant_orders", type_="foreignkey")
    op.drop_column("restaurant_orders", "payment_confirmed_by_user_id")

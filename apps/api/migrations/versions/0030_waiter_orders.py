"""Support waiter-origin restaurant orders."""

from alembic import op
import sqlalchemy as sa


revision = "0030_waiter_orders"
down_revision = "0029_restaurant_orders"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_restaurant_orders_source", "restaurant_orders", type_="check")
    op.drop_constraint("ck_restaurant_orders_payment_status", "restaurant_orders", type_="check")

    op.alter_column(
        "restaurant_orders",
        "agent_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.drop_constraint(
        "restaurant_orders_agent_id_fkey",
        "restaurant_orders",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "restaurant_orders_agent_id_fkey",
        "restaurant_orders",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.alter_column(
        "restaurant_orders",
        "conversation_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )

    op.add_column(
        "restaurant_orders",
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "restaurant_orders_created_by_user_id_fkey",
        "restaurant_orders",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_restaurant_orders_created_by_user_id",
        "restaurant_orders",
        ["created_by_user_id"],
    )

    op.create_check_constraint(
        "ck_restaurant_orders_source",
        "restaurant_orders",
        "source IN ('whatsapp','waiter','table','playground')",
    )
    op.create_check_constraint(
        "ck_restaurant_orders_payment_status",
        "restaurant_orders",
        "payment_status IN ('pending','reported','confirmed','rejected','pay_at_table')",
    )


def downgrade():
    op.drop_constraint("ck_restaurant_orders_source", "restaurant_orders", type_="check")
    op.drop_constraint("ck_restaurant_orders_payment_status", "restaurant_orders", type_="check")

    op.drop_index("ix_restaurant_orders_created_by_user_id", table_name="restaurant_orders")
    op.drop_constraint(
        "restaurant_orders_created_by_user_id_fkey",
        "restaurant_orders",
        type_="foreignkey",
    )
    op.drop_column("restaurant_orders", "created_by_user_id")

    op.drop_constraint(
        "restaurant_orders_agent_id_fkey",
        "restaurant_orders",
        type_="foreignkey",
    )
    op.alter_column(
        "restaurant_orders",
        "agent_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.create_foreign_key(
        "restaurant_orders_agent_id_fkey",
        "restaurant_orders",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column(
        "restaurant_orders",
        "conversation_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )

    op.create_check_constraint(
        "ck_restaurant_orders_source",
        "restaurant_orders",
        "source IN ('whatsapp','table','playground')",
    )
    op.create_check_constraint(
        "ck_restaurant_orders_payment_status",
        "restaurant_orders",
        "payment_status IN ('pending','reported','confirmed','rejected')",
    )

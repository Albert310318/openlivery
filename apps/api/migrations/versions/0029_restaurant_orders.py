"""Add restaurant operational orders and cumulative table accounts."""

from alembic import op
import sqlalchemy as sa


revision = "0029_restaurant_orders"
down_revision = "0028_restaurant_staff_roles"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "restaurant_table_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("table_number", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="open", nullable=False),
        sa.Column("is_open", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cashier_user_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cashier_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('open', 'paid', 'closed')", name="ck_restaurant_table_accounts_status"),
    )
    op.create_index("ix_restaurant_table_accounts_client_id", "restaurant_table_accounts", ["client_id"])
    op.create_index("ix_restaurant_table_accounts_open", "restaurant_table_accounts", ["client_id", "table_number"], unique=True, postgresql_where=sa.text("is_open = true"))

    op.create_table(
        "restaurant_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("order_number", sa.String(length=24), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("modality", sa.String(length=20), nullable=False),
        sa.Column("table_account_id", sa.Uuid(), nullable=True),
        sa.Column("table_number", sa.String(length=40), nullable=True),
        sa.Column("customer_name", sa.String(length=180), nullable=True),
        sa.Column("customer_phone", sa.String(length=80), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("address_reference", sa.Text(), nullable=True),
        sa.Column("delivery_zone_id", sa.Uuid(), nullable=True),
        sa.Column("delivery_zone_name", sa.String(length=180), nullable=True),
        sa.Column("subtotal", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("delivery_fee", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("total", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("order_status", sa.String(length=30), server_default="confirmed", nullable=False),
        sa.Column("payment_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("payment_method", sa.String(length=30), nullable=True),
        sa.Column("receipt_submitted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("payment_reference", sa.String(length=180), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=180), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payment_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["table_account_id"], ["restaurant_table_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["delivery_zone_id"], ["restaurant_delivery_zones.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "order_number", name="uq_restaurant_orders_client_number"),
        sa.UniqueConstraint("client_id", "idempotency_key", name="uq_restaurant_orders_client_idempotency"),
        sa.CheckConstraint("source IN ('whatsapp', 'waiter')", name="ck_restaurant_orders_source"),
        sa.CheckConstraint("modality IN ('dine_in', 'pickup', 'delivery')", name="ck_restaurant_orders_modality"),
        sa.CheckConstraint("order_status IN ('pending_payment', 'confirmed', 'preparing', 'ready', 'on_the_way', 'delivered', 'served', 'closed', 'cancelled')", name="ck_restaurant_orders_status"),
        sa.CheckConstraint("payment_status IN ('pending', 'confirmed', 'not_required')", name="ck_restaurant_orders_payment_status"),
    )
    op.create_index("ix_restaurant_orders_client_id", "restaurant_orders", ["client_id"])
    op.create_index("ix_restaurant_orders_client_status", "restaurant_orders", ["client_id", "order_status"])
    op.create_index("ix_restaurant_orders_table_account_id", "restaurant_orders", ["table_account_id"])
    op.create_index("ix_restaurant_orders_conversation_id", "restaurant_orders", ["conversation_id"])
    op.create_index("ix_restaurant_orders_created_at", "restaurant_orders", ["created_at"])

    op.create_table(
        "restaurant_order_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("product_name", sa.String(length=180), nullable=False),
        sa.Column("base_unit_price", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("line_subtotal", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("variants", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("extras", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("observations", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["restaurant_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["restaurant_menu_products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_restaurant_order_items_order_id", "restaurant_order_items", ["order_id"])
    op.create_index("ix_restaurant_order_items_client_id", "restaurant_order_items", ["client_id"])


def downgrade():
    op.drop_table("restaurant_order_items")
    op.drop_table("restaurant_orders")
    op.drop_table("restaurant_table_accounts")

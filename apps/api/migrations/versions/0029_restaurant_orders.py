"""Add restaurant menu and order workflow."""

from alembic import op
import sqlalchemy as sa


revision = "0029_restaurant_orders"
down_revision = "0028_google_calendar"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("clients", sa.Column("restaurant_currency", sa.String(length=3), server_default="PEN", nullable=False))
    op.add_column("clients", sa.Column("restaurant_payment_instructions", sa.Text(), server_default="", nullable=False))
    op.add_column("clients", sa.Column("restaurant_kitchen_phone", sa.String(length=32), nullable=True))
    op.add_column("clients", sa.Column("restaurant_delivery_phone", sa.String(length=32), nullable=True))

    op.create_table(
        "restaurant_menu_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agency_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="PEN", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("price >= 0", name="ck_restaurant_menu_item_price"),
        sa.ForeignKeyConstraint(["agency_id"], ["agencies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "name", name="uq_restaurant_menu_item_client_name"),
    )
    op.create_index("ix_restaurant_menu_items_agency_id", "restaurant_menu_items", ["agency_id"])
    op.create_index("ix_restaurant_menu_items_client_id", "restaurant_menu_items", ["client_id"])

    op.create_table(
        "restaurant_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_code", sa.String(length=24), nullable=False),
        sa.Column("agency_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("table_label", sa.String(length=80), nullable=True),
        sa.Column("fulfillment_type", sa.String(length=20), nullable=True),
        sa.Column("delivery_address", sa.Text(), nullable=True),
        sa.Column("customer_name", sa.String(length=180), nullable=True),
        sa.Column("customer_phone", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=30), server_default="draft", nullable=False),
        sa.Column("payment_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("payment_method", sa.String(length=80), nullable=True),
        sa.Column("payment_reference", sa.String(length=180), nullable=True),
        sa.Column("subtotal", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("total", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="PEN", nullable=False),
        sa.Column("customer_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kitchen_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','awaiting_confirmation','awaiting_payment','payment_reported','paid','kitchen','ready','out_for_delivery','served','delivered','cancelled')",
            name="ck_restaurant_orders_status",
        ),
        sa.CheckConstraint(
            "payment_status IN ('pending','reported','confirmed','rejected')",
            name="ck_restaurant_orders_payment_status",
        ),
        sa.CheckConstraint("source IN ('whatsapp','table','playground')", name="ck_restaurant_orders_source"),
        sa.CheckConstraint(
            "fulfillment_type IS NULL OR fulfillment_type IN ('delivery','table','pickup')",
            name="ck_restaurant_orders_fulfillment",
        ),
        sa.ForeignKeyConstraint(["agency_id"], ["agencies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_code"),
    )
    op.create_index("ix_restaurant_orders_public_code", "restaurant_orders", ["public_code"])
    op.create_index("ix_restaurant_orders_agency_id", "restaurant_orders", ["agency_id"])
    op.create_index("ix_restaurant_orders_client_id", "restaurant_orders", ["client_id"])
    op.create_index("ix_restaurant_orders_agent_id", "restaurant_orders", ["agent_id"])
    op.create_index("ix_restaurant_orders_conversation_id", "restaurant_orders", ["conversation_id"])
    op.create_index("ix_restaurant_orders_status", "restaurant_orders", ["status"])
    op.create_index("ix_restaurant_orders_payment_status", "restaurant_orders", ["payment_status"])

    op.create_table(
        "restaurant_order_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("menu_item_id", sa.Uuid(), nullable=False),
        sa.Column("item_name", sa.String(length=180), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=False),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_restaurant_order_items_quantity"),
        sa.CheckConstraint("unit_price >= 0", name="ck_restaurant_order_items_unit_price"),
        sa.CheckConstraint("line_total >= 0", name="ck_restaurant_order_items_line_total"),
        sa.ForeignKeyConstraint(["order_id"], ["restaurant_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["menu_item_id"], ["restaurant_menu_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_restaurant_order_items_order_id", "restaurant_order_items", ["order_id"])
    op.create_index("ix_restaurant_order_items_menu_item_id", "restaurant_order_items", ["menu_item_id"])


def downgrade():
    op.drop_table("restaurant_order_items")
    op.drop_table("restaurant_orders")
    op.drop_table("restaurant_menu_items")
    op.drop_column("clients", "restaurant_delivery_phone")
    op.drop_column("clients", "restaurant_kitchen_phone")
    op.drop_column("clients", "restaurant_payment_instructions")
    op.drop_column("clients", "restaurant_currency")

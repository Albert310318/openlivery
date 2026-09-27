"""Add structured restaurant onboarding data."""

from alembic import op
import sqlalchemy as sa


revision = "0027_restaurant_onboarding"
down_revision = "0026_user_password_recovery"
branch_labels = None
depends_on = None


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "restaurant_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("address", sa.Text(), server_default="", nullable=False),
        sa.Column("phone", sa.String(length=80), server_default="", nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="PEN", nullable=False),
        sa.Column("opening_hours", sa.JSON(), server_default="{}", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", name="uq_restaurant_profiles_client_id"),
    )
    op.create_index("ix_restaurant_profiles_client_id", "restaurant_profiles", ["client_id"])

    op.create_table(
        "restaurant_menu_categories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_restaurant_menu_categories_client_id", "restaurant_menu_categories", ["client_id"])

    op.create_table(
        "restaurant_menu_products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("price", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("is_available", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["restaurant_menu_categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_restaurant_menu_products_client_id", "restaurant_menu_products", ["client_id"])
    op.create_index("ix_restaurant_menu_products_category_id", "restaurant_menu_products", ["category_id"])

    for table in ("restaurant_menu_product_variants", "restaurant_menu_product_extras"):
        price_column = "price_delta" if table.endswith("variants") else "price"
        op.create_table(
            table,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("client_id", sa.Uuid(), nullable=False),
            sa.Column("product_id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=180), nullable=False),
            sa.Column(price_column, sa.Numeric(12, 2), server_default="0", nullable=False),
            sa.Column("is_available", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("position", sa.Integer(), server_default="0", nullable=False),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["product_id"], ["restaurant_menu_products.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(f"ix_{table}_client_id", table, ["client_id"])
        op.create_index(f"ix_{table}_product_id", table, ["product_id"])

    op.create_table(
        "restaurant_modalities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("dine_in_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("pickup_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("delivery_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", name="uq_restaurant_modalities_client_id"),
    )
    op.create_index("ix_restaurant_modalities_client_id", "restaurant_modalities", ["client_id"])

    op.create_table(
        "restaurant_delivery_zones",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("fee", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("minimum_order", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), server_default="45", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_restaurant_delivery_zones_client_id", "restaurant_delivery_zones", ["client_id"])

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
        *_timestamps(),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "method", name="uq_restaurant_payment_methods_client_method"),
        sa.CheckConstraint("method IN ('cash', 'yape', 'plin', 'transfer', 'card', 'other')", name="ck_restaurant_payment_methods_method"),
    )
    op.create_index("ix_restaurant_payment_methods_client_id", "restaurant_payment_methods", ["client_id"])

    op.create_table(
        "restaurant_staff",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "user_id", name="uq_restaurant_staff_client_user"),
        sa.CheckConstraint("role IN ('manager_cashier', 'waiter', 'kitchen', 'delivery')", name="ck_restaurant_staff_role"),
    )
    op.create_index("ix_restaurant_staff_client_id", "restaurant_staff", ["client_id"])
    op.create_index("ix_restaurant_staff_user_id", "restaurant_staff", ["user_id"])


def downgrade():
    op.drop_index("ix_restaurant_staff_user_id", table_name="restaurant_staff")
    op.drop_index("ix_restaurant_staff_client_id", table_name="restaurant_staff")
    op.drop_table("restaurant_staff")
    op.drop_index("ix_restaurant_payment_methods_client_id", table_name="restaurant_payment_methods")
    op.drop_table("restaurant_payment_methods")
    op.drop_index("ix_restaurant_delivery_zones_client_id", table_name="restaurant_delivery_zones")
    op.drop_table("restaurant_delivery_zones")
    op.drop_index("ix_restaurant_modalities_client_id", table_name="restaurant_modalities")
    op.drop_table("restaurant_modalities")
    for table in ("restaurant_menu_product_extras", "restaurant_menu_product_variants"):
        op.drop_index(f"ix_{table}_product_id", table_name=table)
        op.drop_index(f"ix_{table}_client_id", table_name=table)
        op.drop_table(table)
    op.drop_index("ix_restaurant_menu_products_category_id", table_name="restaurant_menu_products")
    op.drop_index("ix_restaurant_menu_products_client_id", table_name="restaurant_menu_products")
    op.drop_table("restaurant_menu_products")
    op.drop_index("ix_restaurant_menu_categories_client_id", table_name="restaurant_menu_categories")
    op.drop_table("restaurant_menu_categories")
    op.drop_index("ix_restaurant_profiles_client_id", table_name="restaurant_profiles")
    op.drop_table("restaurant_profiles")

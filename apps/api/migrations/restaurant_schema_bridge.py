"""Repair missing restaurant tables from the historical 0027/0029 line.

Production can have an Alembic revision marker beyond the tables created by
the original restaurant migrations.  This bridge creates only absent tables;
existing tables are checked and never dropped or recreated.
"""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import (
    add_column_if_missing,
    check_constraint_sql,
    create_check_constraint_if_missing,
    create_foreign_key_if_missing,
    create_index_if_missing,
    create_unique_constraint_if_missing,
    require_columns,
    table_exists,
)


def _ensure_table(table_name: str, required_columns: tuple[str, ...], create_table) -> None:
    if not table_exists(table_name):
        create_table()
    else:
        require_columns(table_name, required_columns)


def _ensure_restaurant_profiles() -> None:
    _ensure_table(
        "restaurant_profiles",
        ("id", "client_id", "address", "phone", "currency", "opening_hours", "created_at", "updated_at"),
        lambda: op.create_table(
            "restaurant_profiles",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("client_id", sa.Uuid(), nullable=False),
            sa.Column("address", sa.Text(), server_default="", nullable=False),
            sa.Column("phone", sa.String(length=80), server_default="", nullable=False),
            sa.Column("currency", sa.String(length=3), server_default="PEN", nullable=False),
            sa.Column("opening_hours", sa.JSON(), server_default="{}", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("client_id", name="uq_restaurant_profiles_client_id"),
        ),
    )
    create_unique_constraint_if_missing(
        "uq_restaurant_profiles_client_id", "restaurant_profiles", ["client_id"]
    )
    create_foreign_key_if_missing(
        "restaurant_profiles_client_id_fkey",
        "restaurant_profiles",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="CASCADE",
    )
    create_index_if_missing("ix_restaurant_profiles_client_id", "restaurant_profiles", ["client_id"])


def _ensure_user_phone() -> None:
    """Reapply the schema part of 0028 when the revision marker skipped it."""
    if not table_exists("users"):
        raise RuntimeError("users does not exist; cannot repair the 0028 dependency")
    add_column_if_missing("users", sa.Column("phone", sa.String(length=80), nullable=True))
    create_index_if_missing("ix_users_phone", "users", ["phone"], unique=True)


def _ensure_menu_categories() -> None:
    _ensure_table(
        "restaurant_menu_categories",
        ("id", "client_id", "name", "position", "is_active", "created_at", "updated_at"),
        lambda: op.create_table(
            "restaurant_menu_categories",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("client_id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=180), nullable=False),
            sa.Column("position", sa.Integer(), server_default="0", nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        ),
    )
    create_foreign_key_if_missing(
        "restaurant_menu_categories_client_id_fkey",
        "restaurant_menu_categories",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="CASCADE",
    )
    create_index_if_missing("ix_restaurant_menu_categories_client_id", "restaurant_menu_categories", ["client_id"])


def _ensure_menu_products() -> None:
    _ensure_table(
        "restaurant_menu_products",
        (
            "id", "client_id", "category_id", "name", "description", "price", "image_url",
            "is_available", "position", "created_at", "updated_at",
        ),
        lambda: op.create_table(
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
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["category_id"], ["restaurant_menu_categories.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        ),
    )
    create_foreign_key_if_missing(
        "restaurant_menu_products_client_id_fkey",
        "restaurant_menu_products",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="CASCADE",
    )
    create_foreign_key_if_missing(
        "restaurant_menu_products_category_id_fkey",
        "restaurant_menu_products",
        "restaurant_menu_categories",
        ["category_id"],
        ["id"],
        ondelete="CASCADE",
    )
    create_index_if_missing("ix_restaurant_menu_products_client_id", "restaurant_menu_products", ["client_id"])
    create_index_if_missing("ix_restaurant_menu_products_category_id", "restaurant_menu_products", ["category_id"])


def _ensure_menu_option_table(table_name: str, price_column: str) -> None:
    _ensure_table(
        table_name,
        ("id", "client_id", "product_id", "name", price_column, "is_available", "position"),
        lambda: op.create_table(
            table_name,
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
        ),
    )
    create_foreign_key_if_missing(
        f"{table_name}_client_id_fkey",
        table_name,
        "clients",
        ["client_id"],
        ["id"],
        ondelete="CASCADE",
    )
    create_foreign_key_if_missing(
        f"{table_name}_product_id_fkey",
        table_name,
        "restaurant_menu_products",
        ["product_id"],
        ["id"],
        ondelete="CASCADE",
    )
    create_index_if_missing(f"ix_{table_name}_client_id", table_name, ["client_id"])
    create_index_if_missing(f"ix_{table_name}_product_id", table_name, ["product_id"])


def ensure_restaurant_modalities_table() -> None:
    _ensure_table(
        "restaurant_modalities",
        ("id", "client_id", "dine_in_enabled", "pickup_enabled", "delivery_enabled", "created_at", "updated_at"),
        lambda: op.create_table(
            "restaurant_modalities",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("client_id", sa.Uuid(), nullable=False),
            sa.Column("dine_in_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("pickup_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("delivery_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("client_id", name="uq_restaurant_modalities_client_id"),
        ),
    )
    create_unique_constraint_if_missing(
        "uq_restaurant_modalities_client_id", "restaurant_modalities", ["client_id"]
    )
    create_foreign_key_if_missing(
        "restaurant_modalities_client_id_fkey",
        "restaurant_modalities",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="CASCADE",
    )
    create_index_if_missing("ix_restaurant_modalities_client_id", "restaurant_modalities", ["client_id"])


def _ensure_delivery_zones() -> None:
    _ensure_table(
        "restaurant_delivery_zones",
        (
            "id", "client_id", "name", "fee", "minimum_order", "estimated_minutes", "is_active",
            "created_at", "updated_at",
        ),
        lambda: op.create_table(
            "restaurant_delivery_zones",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("client_id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=180), nullable=False),
            sa.Column("fee", sa.Numeric(12, 2), server_default="0", nullable=False),
            sa.Column("minimum_order", sa.Numeric(12, 2), server_default="0", nullable=False),
            sa.Column("estimated_minutes", sa.Integer(), server_default="45", nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        ),
    )
    create_foreign_key_if_missing(
        "restaurant_delivery_zones_client_id_fkey",
        "restaurant_delivery_zones",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="CASCADE",
    )
    create_index_if_missing("ix_restaurant_delivery_zones_client_id", "restaurant_delivery_zones", ["client_id"])


def _ensure_restaurant_staff() -> None:
    _ensure_table(
        "restaurant_staff",
        ("id", "client_id", "user_id", "role", "is_active", "created_at", "updated_at"),
        lambda: op.create_table(
            "restaurant_staff",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("client_id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("role", sa.String(length=30), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("client_id", "user_id", name="uq_restaurant_staff_client_user"),
            sa.CheckConstraint(
                "role IN ('admin', 'cashier', 'waiter', 'kitchen', 'delivery')",
                name="ck_restaurant_staff_role",
            ),
        ),
    )
    current_role_check = check_constraint_sql("restaurant_staff", "ck_restaurant_staff_role")
    if current_role_check is None:
        create_check_constraint_if_missing(
            "ck_restaurant_staff_role",
            "restaurant_staff",
            "role IN ('admin', 'cashier', 'waiter', 'kitchen', 'delivery')",
        )
    elif "cashier" not in current_role_check or "admin" not in current_role_check:
        op.drop_constraint("ck_restaurant_staff_role", "restaurant_staff", type_="check")
        op.execute(
            "UPDATE restaurant_staff SET role = 'admin' WHERE role = 'manager_cashier'"
        )
        op.create_check_constraint(
            "ck_restaurant_staff_role",
            "restaurant_staff",
            "role IN ('admin', 'cashier', 'waiter', 'kitchen', 'delivery')",
        )
    create_unique_constraint_if_missing(
        "uq_restaurant_staff_client_user", "restaurant_staff", ["client_id", "user_id"]
    )
    create_foreign_key_if_missing(
        "restaurant_staff_client_id_fkey",
        "restaurant_staff",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="CASCADE",
    )
    create_foreign_key_if_missing(
        "restaurant_staff_user_id_fkey",
        "restaurant_staff",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    create_index_if_missing("ix_restaurant_staff_client_id", "restaurant_staff", ["client_id"])
    create_index_if_missing("ix_restaurant_staff_user_id", "restaurant_staff", ["user_id"])


def _ensure_table_accounts() -> None:
    _ensure_table(
        "restaurant_table_accounts",
        (
            "id", "client_id", "table_number", "status", "is_open", "opened_at", "paid_at",
            "closed_at", "cashier_user_id",
        ),
        lambda: op.create_table(
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
        ),
    )
    create_check_constraint_if_missing(
        "ck_restaurant_table_accounts_status",
        "restaurant_table_accounts",
        "status IN ('open', 'paid', 'closed')",
    )
    create_foreign_key_if_missing(
        "restaurant_table_accounts_client_id_fkey",
        "restaurant_table_accounts",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="CASCADE",
    )
    create_foreign_key_if_missing(
        "restaurant_table_accounts_cashier_user_id_fkey",
        "restaurant_table_accounts",
        "users",
        ["cashier_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    create_index_if_missing("ix_restaurant_table_accounts_client_id", "restaurant_table_accounts", ["client_id"])
    create_index_if_missing(
        "ix_restaurant_table_accounts_open",
        "restaurant_table_accounts",
        ["client_id", "table_number"],
        unique=True,
        postgresql_where=sa.text("is_open = true"),
    )


def ensure_historical_restaurant_schema() -> None:
    """Ensure the 0027/0029 tables needed by the current application exist."""
    _ensure_user_phone()
    _ensure_restaurant_profiles()
    _ensure_menu_categories()
    _ensure_menu_products()
    _ensure_menu_option_table("restaurant_menu_product_variants", "price_delta")
    _ensure_menu_option_table("restaurant_menu_product_extras", "price")
    ensure_restaurant_modalities_table()
    _ensure_delivery_zones()
    _ensure_restaurant_staff()
    _ensure_table_accounts()

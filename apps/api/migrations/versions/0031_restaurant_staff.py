"""Bridge the production 0031 staff revision into the local migration line."""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import (
    add_column_if_missing,
    create_check_constraint_if_missing,
    create_foreign_key_if_missing,
    create_index_if_missing,
    create_unique_constraint_if_missing,
    require_columns,
    table_exists,
)


revision = "0031_restaurant_staff"
down_revision = "0030_user_email_verification"
branch_labels = None
depends_on = None


STAFF_COLUMNS = (
    "id",
    "agency_id",
    "client_id",
    "name",
    "phone",
    "phone_normalized",
    "password_hash",
    "role",
    "is_active",
    "credentials_version",
    "created_at",
    "updated_at",
)


def upgrade():
    if not table_exists("restaurant_staff_users"):
        op.create_table(
            "restaurant_staff_users",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("agency_id", sa.Uuid(), nullable=False),
            sa.Column("client_id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=180), nullable=False),
            sa.Column("phone", sa.String(length=40), nullable=False),
            sa.Column("phone_normalized", sa.String(length=32), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("credentials_version", sa.Integer(), server_default="0", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("role IN ('waiter','kitchen','delivery')", name="ck_restaurant_staff_role"),
            sa.ForeignKeyConstraint(["agency_id"], ["agencies.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("client_id", "phone_normalized", name="uq_restaurant_staff_client_phone"),
        )
    else:
        require_columns("restaurant_staff_users", STAFF_COLUMNS)

    create_check_constraint_if_missing(
        "ck_restaurant_staff_role",
        "restaurant_staff_users",
        "role IN ('waiter','kitchen','delivery')",
    )
    create_unique_constraint_if_missing(
        "uq_restaurant_staff_client_phone",
        "restaurant_staff_users",
        ["client_id", "phone_normalized"],
    )
    create_foreign_key_if_missing(
        "restaurant_staff_users_agency_id_fkey",
        "restaurant_staff_users",
        "agencies",
        ["agency_id"],
        ["id"],
        ondelete="CASCADE",
    )
    create_foreign_key_if_missing(
        "restaurant_staff_users_client_id_fkey",
        "restaurant_staff_users",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="CASCADE",
    )

    create_index_if_missing("ix_restaurant_staff_users_agency_id", "restaurant_staff_users", ["agency_id"])
    create_index_if_missing("ix_restaurant_staff_users_client_id", "restaurant_staff_users", ["client_id"])
    create_index_if_missing("ix_restaurant_staff_users_phone_normalized", "restaurant_staff_users", ["phone_normalized"])
    create_index_if_missing("ix_restaurant_staff_users_role", "restaurant_staff_users", ["role"])

    add_column_if_missing("restaurant_orders", sa.Column("created_by_staff_id", sa.Uuid(), nullable=True))
    create_foreign_key_if_missing(
        "restaurant_orders_created_by_staff_id_fkey",
        "restaurant_orders",
        "restaurant_staff_users",
        ["created_by_staff_id"],
        ["id"],
        ondelete="SET NULL",
    )
    create_index_if_missing(
        "ix_restaurant_orders_created_by_staff_id",
        "restaurant_orders",
        ["created_by_staff_id"],
    )


def downgrade():
    # The bridge is intentionally irreversible: it must never remove staff
    # credentials, session-related data, or order relationships.
    pass

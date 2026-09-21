"""Add restaurant staff accounts and staff order ownership."""

from alembic import op
import sqlalchemy as sa


revision = "0031_restaurant_staff"
down_revision = "0030_waiter_orders"
branch_labels = None
depends_on = None


def upgrade():
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
    op.create_index("ix_restaurant_staff_users_agency_id", "restaurant_staff_users", ["agency_id"])
    op.create_index("ix_restaurant_staff_users_client_id", "restaurant_staff_users", ["client_id"])
    op.create_index("ix_restaurant_staff_users_phone_normalized", "restaurant_staff_users", ["phone_normalized"])
    op.create_index("ix_restaurant_staff_users_role", "restaurant_staff_users", ["role"])

    op.add_column("restaurant_orders", sa.Column("created_by_staff_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "restaurant_orders_created_by_staff_id_fkey",
        "restaurant_orders",
        "restaurant_staff_users",
        ["created_by_staff_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_restaurant_orders_created_by_staff_id", "restaurant_orders", ["created_by_staff_id"])


def downgrade():
    op.drop_index("ix_restaurant_orders_created_by_staff_id", table_name="restaurant_orders")
    op.drop_constraint("restaurant_orders_created_by_staff_id_fkey", "restaurant_orders", type_="foreignkey")
    op.drop_column("restaurant_orders", "created_by_staff_id")

    op.drop_index("ix_restaurant_staff_users_role", table_name="restaurant_staff_users")
    op.drop_index("ix_restaurant_staff_users_phone_normalized", table_name="restaurant_staff_users")
    op.drop_index("ix_restaurant_staff_users_client_id", table_name="restaurant_staff_users")
    op.drop_index("ix_restaurant_staff_users_agency_id", table_name="restaurant_staff_users")
    op.drop_table("restaurant_staff_users")

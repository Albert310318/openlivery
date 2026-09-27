"""Split restaurant staff roles and add a reusable user phone identifier."""

from alembic import op
import sqlalchemy as sa


revision = "0028_restaurant_staff_roles"
down_revision = "0027_restaurant_onboarding"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("phone", sa.String(length=80), nullable=True))
    op.create_index("ix_users_phone", "users", ["phone"], unique=True)

    op.drop_constraint("ck_restaurant_staff_role", "restaurant_staff", type_="check")
    op.execute("UPDATE restaurant_staff SET role = 'admin' WHERE role = 'manager_cashier'")
    op.create_check_constraint(
        "ck_restaurant_staff_role",
        "restaurant_staff",
        "role IN ('admin', 'cashier', 'waiter', 'kitchen', 'delivery')",
    )


def downgrade():
    op.drop_constraint("ck_restaurant_staff_role", "restaurant_staff", type_="check")
    op.execute("UPDATE restaurant_staff SET role = 'manager_cashier' WHERE role = 'admin'")
    op.create_check_constraint(
        "ck_restaurant_staff_role",
        "restaurant_staff",
        "role IN ('manager_cashier', 'waiter', 'kitchen', 'delivery')",
    )
    op.drop_index("ix_users_phone", table_name="users")
    op.drop_column("users", "phone")

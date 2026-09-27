"""Track initial email verification for AYV users."""

from alembic import op
import sqlalchemy as sa


revision = "0030_user_email_verification"
down_revision = "0029_restaurant_orders"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("email_verification_pending", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    op.drop_column("users", "email_verification_pending")

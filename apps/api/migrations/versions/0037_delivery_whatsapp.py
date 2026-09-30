"""Add the restaurant delivery contact number."""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import add_column_if_missing


revision = "0037_delivery_whatsapp"
down_revision = "0036_manual_payment_review"
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        "restaurant_modalities",
        sa.Column("delivery_whatsapp", sa.String(length=80), nullable=True),
    )


def downgrade():
    op.drop_column("restaurant_modalities", "delivery_whatsapp")

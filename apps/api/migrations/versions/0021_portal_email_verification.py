"""Add portal email verification without grandfathering existing addresses."""
import sqlalchemy as sa
from alembic import op

revision = "0021_portal_email_verification"
down_revision = "0020_sales_advisor_handoff"
branch_labels = None
depends_on = None

DATES = ("portal_email_verified_at", "portal_verification_expires_at",
         "portal_verification_last_sent_at", "portal_verification_send_window_started_at")
COUNTERS = ("portal_verification_attempts", "portal_verification_send_count", "portal_credentials_version")


def upgrade():
    for name in DATES:
        op.add_column("clients", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.add_column("clients", sa.Column("portal_verification_code_hash", sa.String(255), nullable=True))
    for name in COUNTERS:
        op.add_column("clients", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    for name in (*COUNTERS, "portal_verification_code_hash", *DATES):
        op.drop_column("clients", name)

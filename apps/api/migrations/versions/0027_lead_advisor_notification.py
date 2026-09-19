"""Track the first advisor notification for each lead."""

from alembic import op
import sqlalchemy as sa


revision = "0027_lead_advisor_notification"
down_revision = "0026_user_password_recovery"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("leads", sa.Column("advisor_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "leads",
        sa.Column("advisor_notification_external_message_id", sa.String(length=255), nullable=True),
    )
    op.add_column("leads", sa.Column("advisor_notification_error", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("leads", "advisor_notification_error")
    op.drop_column("leads", "advisor_notification_external_message_id")
    op.drop_column("leads", "advisor_notified_at")

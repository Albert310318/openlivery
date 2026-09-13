"""Persist timed phone handovers and per-agent silence windows."""
from alembic import op
import sqlalchemy as sa

revision = "0044_phone_handover"
down_revision = "0043_usage_reply_link"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agents", sa.Column("phone_handover_minutes", sa.Integer(), nullable=False, server_default="10"))
    op.add_column("conversations", sa.Column("phone_pause_until", sa.DateTime(timezone=True)))
    op.add_column("conversations", sa.Column("phone_resume_claimed_until", sa.DateTime(timezone=True)))
    op.create_index("ix_conversations_phone_pause_until", "conversations", ["phone_pause_until"])


def downgrade():
    op.drop_index("ix_conversations_phone_pause_until", "conversations")
    op.drop_column("conversations", "phone_resume_claimed_until")
    op.drop_column("conversations", "phone_pause_until")
    op.drop_column("agents", "phone_handover_minutes")

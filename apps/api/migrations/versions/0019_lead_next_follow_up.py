"""Add the next scheduled follow-up to leads.

Revision ID: 0019_lead_next_follow_up
Revises: 0018_leads
"""

import sqlalchemy as sa
from alembic import op


revision = "0019_lead_next_follow_up"
down_revision = "0018_leads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("next_follow_up_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_leads_next_follow_up_at", "leads", ["next_follow_up_at"])


def downgrade() -> None:
    op.drop_index("ix_leads_next_follow_up_at", table_name="leads")
    op.drop_column("leads", "next_follow_up_at")

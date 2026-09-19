"""Add per-client sales advisor handoffs.

Revision ID: 0020_sales_advisor_handoff
Revises: 0019_lead_next_follow_up
"""

import sqlalchemy as sa
from alembic import op


revision = "0020_sales_advisor_handoff"
down_revision = "0019_lead_next_follow_up"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("sales_advisor_phone", sa.String(length=32), nullable=True))
    op.create_table(
        "lead_handoffs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agency_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("consent_message_id", sa.Uuid(), nullable=False),
        sa.Column("advisor_phone", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="sending", nullable=False),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('sending', 'sent', 'failed')", name="ck_lead_handoffs_status"),
        sa.ForeignKeyConstraint(["agency_id"], ["agencies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["consent_message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("consent_message_id", name="uq_lead_handoffs_consent_message"),
    )
    for column in ("agency_id", "client_id", "lead_id", "conversation_id", "consent_message_id"):
        op.create_index(f"ix_lead_handoffs_{column}", "lead_handoffs", [column])


def downgrade() -> None:
    for column in reversed(("agency_id", "client_id", "lead_id", "conversation_id", "consent_message_id")):
        op.drop_index(f"ix_lead_handoffs_{column}", table_name="lead_handoffs")
    op.drop_table("lead_handoffs")
    op.drop_column("clients", "sales_advisor_phone")

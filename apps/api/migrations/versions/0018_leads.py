"""Add leads captured from conversations.

Revision ID: 0018_leads
Revises: 0017_whatsapp_cloud_channel
"""

import sqlalchemy as sa
from alembic import op


revision = "0018_leads"
down_revision = "0017_whatsapp_cloud_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agency_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=True),
        sa.Column("phone", sa.String(length=80), nullable=True),
        sa.Column("phone_normalized", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("email_normalized", sa.String(length=320), nullable=True),
        sa.Column("interest", sa.Text(), nullable=True),
        sa.Column("budget", sa.String(length=180), nullable=True),
        sa.Column("preferred_contact_time", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=40), server_default="playground", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="new", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('new', 'qualified', 'follow_up', 'won', 'lost')",
            name="ck_leads_status",
        ),
        sa.ForeignKeyConstraint(["agency_id"], ["agencies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agency_id", "client_id", "phone_normalized", name="uq_leads_tenant_phone"),
        sa.UniqueConstraint("agency_id", "client_id", "email_normalized", name="uq_leads_tenant_email"),
    )
    op.create_index("ix_leads_agency_id", "leads", ["agency_id"])
    op.create_index("ix_leads_client_id", "leads", ["client_id"])
    op.create_index("ix_leads_agent_id", "leads", ["agent_id"])
    op.create_index("ix_leads_status", "leads", ["status"])
    op.create_index("ix_leads_created_at", "leads", ["created_at"])

    op.create_table(
        "lead_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", name="uq_lead_conversations_conversation"),
    )
    op.create_index("ix_lead_conversations_lead_id", "lead_conversations", ["lead_id"])
    op.create_index("ix_lead_conversations_conversation_id", "lead_conversations", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_lead_conversations_conversation_id", table_name="lead_conversations")
    op.drop_index("ix_lead_conversations_lead_id", table_name="lead_conversations")
    op.drop_table("lead_conversations")
    op.drop_index("ix_leads_created_at", table_name="leads")
    op.drop_index("ix_leads_status", table_name="leads")
    op.drop_index("ix_leads_agent_id", table_name="leads")
    op.drop_index("ix_leads_client_id", table_name="leads")
    op.drop_index("ix_leads_agency_id", table_name="leads")
    op.drop_table("leads")

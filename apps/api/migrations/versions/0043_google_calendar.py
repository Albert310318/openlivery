"""Add Google Calendar client integration and appointments."""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import (
    add_column_if_missing,
    create_check_constraint_if_missing,
    create_foreign_key_if_missing,
    create_index_if_missing,
    create_unique_constraint_if_missing,
    require_columns,
    table_exists,
)


revision = "0043_google_calendar"
down_revision = "0042_lead_advisor_notification"
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing(
        "clients",
        sa.Column("google_calendar_refresh_token_encrypted", sa.Text(), nullable=True),
    )
    add_column_if_missing(
        "clients",
        sa.Column("google_calendar_id", sa.String(length=255), server_default="primary", nullable=False),
    )
    add_column_if_missing(
        "clients",
        sa.Column("google_calendar_timezone", sa.String(length=64), server_default="America/Lima", nullable=False),
    )
    add_column_if_missing(
        "clients",
        sa.Column("calendar_workday_start", sa.String(length=5), server_default="09:00", nullable=False),
    )
    add_column_if_missing(
        "clients",
        sa.Column("calendar_workday_end", sa.String(length=5), server_default="18:00", nullable=False),
    )
    add_column_if_missing(
        "clients",
        sa.Column("calendar_working_days", sa.String(length=20), server_default="0,1,2,3,4,5", nullable=False),
    )
    add_column_if_missing(
        "clients",
        sa.Column("calendar_slot_minutes", sa.Integer(), server_default="30", nullable=False),
    )
    add_column_if_missing(
        "clients",
        sa.Column("calendar_buffer_minutes", sa.Integer(), server_default="0", nullable=False),
    )
    add_column_if_missing(
        "clients",
        sa.Column("calendar_min_notice_minutes", sa.Integer(), server_default="60", nullable=False),
    )
    add_column_if_missing(
        "clients",
        sa.Column("calendar_booking_horizon_days", sa.Integer(), server_default="30", nullable=False),
    )

    if not table_exists("calendar_appointments"):
        op.create_table(
            "calendar_appointments",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("agency_id", sa.Uuid(), nullable=False),
            sa.Column("client_id", sa.Uuid(), nullable=False),
            sa.Column("agent_id", sa.Uuid(), nullable=False),
            sa.Column("lead_id", sa.Uuid(), nullable=True),
            sa.Column("conversation_id", sa.Uuid(), nullable=False),
            sa.Column("google_event_id", sa.String(length=255), nullable=False),
            sa.Column("calendar_id", sa.String(length=255), server_default="primary", nullable=False),
            sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(length=20), server_default="confirmed", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("status IN ('confirmed', 'cancelled')", name="ck_calendar_appointments_status"),
            sa.ForeignKeyConstraint(["agency_id"], ["agencies.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("conversation_id", name="uq_calendar_appointments_conversation"),
        )
    else:
        require_columns(
            "calendar_appointments",
            (
                "id", "agency_id", "client_id", "agent_id", "lead_id", "conversation_id",
                "google_event_id", "calendar_id", "start_at", "end_at", "status", "created_at", "updated_at",
            ),
        )

    create_check_constraint_if_missing(
        "ck_calendar_appointments_status",
        "calendar_appointments",
        "status IN ('confirmed', 'cancelled')",
    )
    create_unique_constraint_if_missing(
        "uq_calendar_appointments_conversation",
        "calendar_appointments",
        ["conversation_id"],
    )
    for name, local, remote_table, remote in (
        ("calendar_appointments_agency_id_fkey", ["agency_id"], "agencies", ["id"]),
        ("calendar_appointments_client_id_fkey", ["client_id"], "clients", ["id"]),
        ("calendar_appointments_agent_id_fkey", ["agent_id"], "agents", ["id"]),
        ("calendar_appointments_lead_id_fkey", ["lead_id"], "leads", ["id"]),
        ("calendar_appointments_conversation_id_fkey", ["conversation_id"], "conversations", ["id"]),
    ):
        create_foreign_key_if_missing(
            name,
            "calendar_appointments",
            remote_table,
            local,
            remote,
            ondelete={
                "agencies": "CASCADE",
                "clients": "CASCADE",
                "agents": "RESTRICT",
                "leads": "SET NULL",
                "conversations": "CASCADE",
            }[remote_table],
        )
    create_index_if_missing("ix_calendar_appointments_agency_id", "calendar_appointments", ["agency_id"])
    create_index_if_missing("ix_calendar_appointments_client_id", "calendar_appointments", ["client_id"])
    create_index_if_missing("ix_calendar_appointments_agent_id", "calendar_appointments", ["agent_id"])
    create_index_if_missing("ix_calendar_appointments_lead_id", "calendar_appointments", ["lead_id"])
    create_index_if_missing(
        "ix_calendar_appointments_conversation_id",
        "calendar_appointments",
        ["conversation_id"],
    )


def downgrade():
    op.drop_table("calendar_appointments")
    for column in (
        "calendar_booking_horizon_days",
        "calendar_min_notice_minutes",
        "calendar_buffer_minutes",
        "calendar_slot_minutes",
        "calendar_working_days",
        "calendar_workday_end",
        "calendar_workday_start",
        "google_calendar_timezone",
        "google_calendar_id",
        "google_calendar_refresh_token_encrypted",
    ):
        op.drop_column("clients", column)

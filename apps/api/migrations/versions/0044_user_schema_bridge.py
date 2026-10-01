"""Repair user columns skipped by an incomplete historical migration line."""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import (
    add_column_if_missing,
    create_index_if_missing,
    require_columns,
    table_exists,
)


revision = "0044_user_schema_bridge"
down_revision = "0043_google_calendar"
branch_labels = None
depends_on = None


BASE_USER_COLUMNS = (
    "id",
    "agency_id",
    "name",
    "email",
    "password_hash",
    "role",
    "created_at",
)


def upgrade():
    if not table_exists("users"):
        raise RuntimeError("users does not exist; cannot repair the User schema")
    require_columns("users", BASE_USER_COLUMNS)

    # These are the additive User changes from 0023, 0026, 0028 and 0030.
    # Existing values are never rewritten; PostgreSQL fills only newly added
    # non-null columns from their safe historical defaults.
    add_column_if_missing(
        "users",
        sa.Column("is_vendiq_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    add_column_if_missing(
        "users",
        sa.Column("password_recovery_code_hash", sa.String(length=255), nullable=True),
    )
    add_column_if_missing(
        "users",
        sa.Column("password_recovery_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    add_column_if_missing(
        "users",
        sa.Column("password_recovery_last_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    add_column_if_missing(
        "users",
        sa.Column("password_recovery_send_window_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    add_column_if_missing(
        "users",
        sa.Column("password_recovery_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    add_column_if_missing(
        "users",
        sa.Column("password_recovery_send_count", sa.Integer(), server_default="0", nullable=False),
    )
    add_column_if_missing(
        "users",
        sa.Column("password_recovery_credentials_version", sa.Integer(), server_default="0", nullable=False),
    )
    add_column_if_missing(
        "users",
        sa.Column("phone", sa.String(length=80), nullable=True),
    )
    add_column_if_missing(
        "users",
        sa.Column("email_verification_pending", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    create_index_if_missing("ix_users_phone", "users", ["phone"], unique=True)


def downgrade():
    raise RuntimeError("0044_user_schema_bridge is irreversible to preserve repaired user data")

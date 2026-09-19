"""Persist password recovery challenges for privileged users."""

from alembic import op
import sqlalchemy as sa


revision = "0026_user_password_recovery"
down_revision = "0025_activation_identity_aliases"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("password_recovery_code_hash", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("password_recovery_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("password_recovery_last_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("password_recovery_send_window_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("password_recovery_attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("users", sa.Column("password_recovery_send_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("users", sa.Column("password_recovery_credentials_version", sa.Integer(), server_default="0", nullable=False))


def downgrade():
    op.drop_column("users", "password_recovery_credentials_version")
    op.drop_column("users", "password_recovery_send_count")
    op.drop_column("users", "password_recovery_attempts")
    op.drop_column("users", "password_recovery_send_window_started_at")
    op.drop_column("users", "password_recovery_last_sent_at")
    op.drop_column("users", "password_recovery_expires_at")
    op.drop_column("users", "password_recovery_code_hash")

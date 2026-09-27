"""Add encrypted Gmail-compatible payment mailboxes and message deduplication."""

from alembic import op
import sqlalchemy as sa


revision = "0033_payment_mailboxes"
down_revision = "0032_payment_notifications"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payment_notifications", sa.Column("source_message_id", sa.String(length=500), nullable=True))
    op.alter_column("payment_notifications", "payment_method_id", existing_type=sa.Uuid(), nullable=True)
    op.alter_column("payment_notifications", "amount", existing_type=sa.Numeric(12, 2), nullable=True)
    op.drop_constraint("ck_payment_notifications_status", "payment_notifications", type_="check")
    op.create_check_constraint(
        "ck_payment_notifications_status",
        "payment_notifications",
        "status IN ('unmatched', 'ambiguous', 'matched', 'review_required')",
    )
    op.create_unique_constraint(
        "uq_payment_notifications_client_message",
        "payment_notifications",
        ["client_id", "source_message_id"],
    )

    op.create_table(
        "restaurant_payment_mailboxes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("imap_host", sa.String(length=255), server_default="imap.gmail.com", nullable=False),
        sa.Column("imap_port", sa.Integer(), server_default="993", nullable=False),
        sa.Column("imap_ssl", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("encrypted_app_password", sa.Text(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("connection_status", sa.String(length=20), server_default="not_tested", nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", name="uq_restaurant_payment_mailboxes_client_id"),
    )
    op.create_index("ix_restaurant_payment_mailboxes_client_id", "restaurant_payment_mailboxes", ["client_id"])


def downgrade():
    op.drop_index("ix_restaurant_payment_mailboxes_client_id", table_name="restaurant_payment_mailboxes")
    op.drop_table("restaurant_payment_mailboxes")
    op.drop_constraint("uq_payment_notifications_client_message", "payment_notifications", type_="unique")
    op.drop_constraint("ck_payment_notifications_status", "payment_notifications", type_="check")
    op.create_check_constraint(
        "ck_payment_notifications_status",
        "payment_notifications",
        "status IN ('unmatched', 'ambiguous', 'matched')",
    )
    op.drop_column("payment_notifications", "source_message_id")
    op.alter_column("payment_notifications", "payment_method_id", existing_type=sa.Uuid(), nullable=False)
    op.alter_column("payment_notifications", "amount", existing_type=sa.Numeric(12, 2), nullable=False)

"""Usage records know the reply they belong to.

``usage_records`` gains the conversation and message behind each record,
how long the model took, the vendor that served it and the cached and
reasoning token counts, so cost can be reported per reply, per client and
per conversation from the core alone. Records from before stay as they are.
"""

from alembic import op
import sqlalchemy as sa

revision = "0043_usage_reply_link"
down_revision = "0042_openrouter"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("usage_records", sa.Column("conversation_id", sa.Uuid(), nullable=True))
    op.add_column("usage_records", sa.Column("message_id", sa.Uuid(), nullable=True))
    op.add_column("usage_records", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("usage_records", sa.Column("served_by", sa.String(length=60), nullable=False, server_default=""))
    op.add_column("usage_records", sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("usage_records", sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.create_foreign_key("fk_usage_records_conversation", "usage_records", "conversations", ["conversation_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_usage_records_message", "usage_records", "messages", ["message_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_usage_records_conversation_id", "usage_records", ["conversation_id"])


def downgrade():
    op.drop_index("ix_usage_records_conversation_id", table_name="usage_records")
    op.drop_constraint("fk_usage_records_message", "usage_records", type_="foreignkey")
    op.drop_constraint("fk_usage_records_conversation", "usage_records", type_="foreignkey")
    for column in ("reasoning_tokens", "cached_tokens", "served_by", "duration_ms", "message_id", "conversation_id"):
        op.drop_column("usage_records", column)

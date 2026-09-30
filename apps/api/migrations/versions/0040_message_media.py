"""Store conversation media for operator and channel delivery."""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import add_column_if_missing

revision = "0040_message_media"
down_revision = "0039_restaurant_welcome_flyer"
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing("messages", sa.Column("media_kind", sa.String(length=40), nullable=True))
    add_column_if_missing("messages", sa.Column("media_mime", sa.String(length=120), nullable=True))
    add_column_if_missing("messages", sa.Column("media_filename", sa.String(length=255), nullable=True))
    add_column_if_missing("messages", sa.Column("media_data", sa.LargeBinary(), nullable=True))


def downgrade():
    op.drop_column("messages", "media_data")
    op.drop_column("messages", "media_filename")
    op.drop_column("messages", "media_mime")
    op.drop_column("messages", "media_kind")

"""Store conversation media for operator and channel delivery."""

from alembic import op
import sqlalchemy as sa

revision = "0040_message_media"
down_revision = "0039_restaurant_welcome_flyer"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("media_kind", sa.String(length=40), nullable=True))
    op.add_column("messages", sa.Column("media_mime", sa.String(length=120), nullable=True))
    op.add_column("messages", sa.Column("media_filename", sa.String(length=255), nullable=True))
    op.add_column("messages", sa.Column("media_data", sa.LargeBinary(), nullable=True))


def downgrade():
    op.drop_column("messages", "media_data")
    op.drop_column("messages", "media_filename")
    op.drop_column("messages", "media_mime")
    op.drop_column("messages", "media_kind")

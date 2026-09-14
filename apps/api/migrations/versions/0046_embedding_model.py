"""Per-agent embedding model, recorded on every stored chunk."""
from alembic import op
import sqlalchemy as sa

revision = "0046_embedding_model"
down_revision = "0045_agent_tool_enabled_tools"
branch_labels = None
depends_on = None

# Every chunk indexed so far was embedded with this model, which was fixed in code.
DEFAULT = "openai/text-embedding-3-small"


def upgrade():
    op.add_column("agents", sa.Column("embedding_model", sa.String(180), nullable=False, server_default=DEFAULT))
    op.add_column("knowledge_chunks", sa.Column("embedding_model", sa.String(180), nullable=False, server_default=DEFAULT))


def downgrade():
    op.drop_column("knowledge_chunks", "embedding_model")
    op.drop_column("agents", "embedding_model")

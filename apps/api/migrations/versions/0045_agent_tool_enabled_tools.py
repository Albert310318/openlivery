"""Per-server allowlist of the MCP tools an agent may call."""
from alembic import op
import sqlalchemy as sa

revision = "0045_agent_tool_enabled_tools"
down_revision = "0044_phone_handover"
branch_labels = None
depends_on = None


def upgrade():
    # NULL keeps the previous behaviour (every discovered tool is exposed).
    op.add_column("agent_tools", sa.Column("enabled_tools", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("agent_tools", "enabled_tools")

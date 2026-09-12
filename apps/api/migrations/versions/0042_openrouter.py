"""OpenRouter becomes the only AI provider.

Every agent moves to ``provider = 'openrouter'`` and its model ids become
OpenRouter slugs: ``gpt-4.1-mini`` turns into ``openai/gpt-4.1-mini``,
``claude-sonnet-5`` into ``anthropic/claude-sonnet-5``. Vision and
transcription models get the ``openai/`` prefix the same way, and the old
``whisper-1`` default becomes ``openai/gpt-4o-mini-transcribe``.

Keys stored for the previous providers are dropped: they cannot be used through
OpenRouter, and an agency configures its OpenRouter key in Settings.

``usage_records.cost_usd`` records what each reply cost, as the provider
reported it; rows from before this migration keep it empty.
"""

from alembic import op
import sqlalchemy as sa

revision = "0042_openrouter"
down_revision = "0041_client_tz_portal_roles"
branch_labels = None
depends_on = None

# Ids that map to a slug the plain prefix rule would get wrong.
EXPLICIT_MODEL_MAP = {
    "gpt-5.6": "openai/gpt-5.6-sol",
    "whisper-1": "openai/gpt-4o-mini-transcribe",
}


def upgrade():
    op.add_column("usage_records", sa.Column("cost_usd", sa.Numeric(14, 8), nullable=True))
    op.alter_column("agents", "provider", server_default="openrouter")

    for old, new in EXPLICIT_MODEL_MAP.items():
        for column in ("model", "image_model", "audio_model"):
            op.execute(f"UPDATE agents SET {column} = '{new}' WHERE {column} = '{old}'")
    # The chat model takes the vendor of the provider it ran on; vision and
    # transcription always ran on OpenAI.
    op.execute("UPDATE agents SET model = 'openai/' || model WHERE provider = 'openai' AND model <> '' AND model NOT LIKE '%/%'")
    op.execute("UPDATE agents SET model = 'anthropic/' || model WHERE provider = 'anthropic' AND model <> '' AND model NOT LIKE '%/%'")
    op.execute("UPDATE agents SET image_model = 'openai/' || image_model WHERE image_model <> '' AND image_model NOT LIKE '%/%'")
    op.execute("UPDATE agents SET audio_model = 'openai/' || audio_model WHERE audio_model <> '' AND audio_model NOT LIKE '%/%'")
    op.execute("UPDATE agents SET audio_model = 'openai/gpt-4o-mini-transcribe' WHERE audio_model = ''")
    op.execute("UPDATE agents SET provider = 'openrouter'")
    op.execute("DELETE FROM provider_credentials WHERE provider <> 'openrouter'")


def downgrade():
    # Best effort: the vendor prefix says which provider the agent goes back
    # to. Dropped OpenAI and Anthropic keys are not recoverable.
    op.execute("DELETE FROM provider_credentials WHERE provider = 'openrouter'")
    op.execute("UPDATE agents SET provider = 'anthropic' WHERE model LIKE 'anthropic/%'")
    op.execute("UPDATE agents SET provider = 'openai' WHERE provider = 'openrouter'")
    for column in ("model", "image_model", "audio_model"):
        op.execute(f"UPDATE agents SET {column} = substr({column}, position('/' in {column}) + 1) WHERE {column} LIKE 'openai/%' OR {column} LIKE 'anthropic/%'")
    op.alter_column("agents", "provider", server_default="openai")
    op.drop_column("usage_records", "cost_usd")

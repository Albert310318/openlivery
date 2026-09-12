"""The AI provider: OpenRouter, bring your own key, one per agency.

OpenRouter fronts every vendor (OpenAI, Anthropic, Google, ...) behind one
OpenAI-compatible API, so an agency configures a single key and picks any
model by its OpenRouter slug (``openai/gpt-5.6-luna``, ``anthropic/claude-sonnet-5``).
The registry keeps its dict shape so a deployment can still swap the base URL
or resolve credentials differently without touching the call sites.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Agent, ProviderCredential
from ..security import decrypt_secret


PROVIDERS: dict[str, dict[str, str]] = {
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
}
SUPPORTED = tuple(PROVIDERS)
DEFAULT_PROVIDER = "openrouter"


def base_url_for(provider: str) -> str:
    return PROVIDERS[provider]["base_url"]


def resolve_provider_credentials(db: Session, agency_id, provider: str) -> tuple[str, str] | None:
    """(base_url, api_key) for an agency's provider key, or None if unknown or unset."""
    if provider not in PROVIDERS:
        return None
    credential = db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.agency_id == agency_id,
            ProviderCredential.provider == provider,
        )
    )
    if not credential:
        return None
    return base_url_for(provider), decrypt_secret(credential.encrypted_api_key)


def resolve_agent_credentials(db: Session, agent: Agent) -> tuple[str, str] | None:
    """(base_url, api_key) for the agent's provider using the agency's stored key."""
    return resolve_provider_credentials(db, agent.agency_id, agent.provider)

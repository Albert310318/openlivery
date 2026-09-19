from functools import lru_cache
from datetime import datetime, timezone
import logging
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


APP_DIR = Path(__file__).resolve().parents[1]   # apps/api
REPO_ROOT = Path(__file__).resolve().parents[3]  # monorepo root (used for a shared local .env)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    smtp_host: str = ""
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_use_tls: bool = True
    smtp_timeout: float = 10
    app_name: str = "OpenLivery API"
    database_url: str = "postgresql+psycopg://openlivery:openlivery@localhost:5432/openlivery"
    secret_key: str = "dev-local-change-this-key-please"
    encryption_key: str = "dev-local-change-this-key-too"
    frontend_url: str = "http://localhost:3000"
    access_token_minutes: int = 60 * 24 * 7
    # Session cookie flags. Defaults suit local HTTP; set cookie_secure=true (and
    # cookie_samesite=none when the frontend and API are on different sites)
    # behind HTTPS in production.
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    # Rate limiting on public/unauthenticated endpoints (per client IP). Disable
    # only for tests or when a proxy in front already enforces limits.
    rate_limit_enabled: bool = True
    # A self-hosted instance is single-agency by default: the first registration
    # creates the owner agency and closes public sign-up (like n8n's owner
    # setup). Enable only when one deployment must host many agencies.
    allow_multi_agency: bool = False
    # SSRF guard for agent HTTP tools: URLs resolving to private/loopback
    # addresses are rejected. Enable only on self-hosted deployments that need
    # tools to reach internal services.
    tools_allow_private_urls: bool = False
    storage_dir: Path = APP_DIR / "storage"
    backend_url: str = "http://localhost:8000"
    whatsapp_bridge_url: str = "http://localhost:3101"
    whatsapp_bridge_token: str = "dev-local-change-this-bridge-token"
    # Explicit UTC activation cutoff. An empty or invalid value intentionally
    # disables inbound automation/trial eligibility (fail closed).
    trial_activation_eligible_since: str = ""
    # Meta Graph API root used by the WhatsApp Cloud API channel; override to
    # point at a mock server in tests.
    meta_graph_base_url: str = "https://graph.facebook.com/v23.0"

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", APP_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def parse_trial_activation_eligible_since(value: str | None) -> datetime | None:
    """Parse the durable UTC cutoff, returning None on any unsafe value."""
    raw = (value or "").strip()
    if not raw:
        logger.warning("Trial activation disabled: TRIAL_ACTIVATION_ELIGIBLE_SINCE is missing")
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Trial activation disabled: invalid TRIAL_ACTIVATION_ELIGIBLE_SINCE=%r", raw)
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        logger.warning("Trial activation disabled: TRIAL_ACTIVATION_ELIGIBLE_SINCE must include a UTC offset")
        return None
    return parsed.astimezone(timezone.utc)


def trial_activation_eligible_since() -> datetime | None:
    return parse_trial_activation_eligible_since(get_settings().trial_activation_eligible_since)

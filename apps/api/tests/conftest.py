import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://openlivery:openlivery@localhost:5432/openlivery_test",
)
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("SOCIAL_WORKER_ENABLED", "false")

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import whatsapp_inbound  # noqa: E402


test_engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
TestingSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def immediate_replies(monkeypatch):
    """Answer inbound WhatsApp messages synchronously, whatever the agent's delay.

    The reply delay is a per-agent setting with a non-zero default, and most
    tests want the reply back in the inbound response. Tests of the delayed
    path patch a short delay of their own on top of this.
    """
    monkeypatch.setattr(whatsapp_inbound, "reply_delay_seconds", lambda agent: 0.0)


@pytest.fixture(autouse=True)
def fixed_model_catalog():
    """A known catalog, so no test reads OpenRouter and the prices the cost
    reports are asserted against stay put."""
    from app.services import model_catalog as catalog

    chat = [
        catalog.ModelInfo("openai/gpt-5.6-luna", "openai", "GPT-5.6 Luna", "openai", 1_050_000, 128_000, True, True, 0.0002, 0.0012),
        catalog.ModelInfo("openai/gpt-5.6-sol", "openai", "GPT-5.6 Sol", "openai", 1_050_000, 128_000, True, True, 0.001, 0.004),
        catalog.ModelInfo("openai/gpt-4.1", "openai", "GPT-4.1", "openai", 1_047_576, 32_768, True, True, 0.002, 0.008),
        catalog.ModelInfo("deepseek/deepseek-v4-flash", "deepseek", "DeepSeek V4 Flash", "deepseek", 128_000, 8_192, True, False, 0.0001, 0.0002),
        catalog.ModelInfo("anthropic/claude-sonnet-5", "anthropic", "Claude Sonnet 5", "anthropic", 1_000_000, 64_000, True, True, 0.003, 0.015),
    ]
    embeddings = [
        catalog.EmbeddingModelInfo("openai/text-embedding-3-small", "openai", "text-embedding-3-small", 8_192, 0.00002),
        catalog.EmbeddingModelInfo("openai/text-embedding-3-large", "openai", "text-embedding-3-large", 8_192, 0.00013),
    ]
    audio = [
        catalog.ModelInfo("openai/gpt-4o-mini-transcribe", "openai", "GPT-4o mini Transcribe", "transcribe", 16_000, 2_000, False, False, 0.003, 0.005),
        catalog.ModelInfo("openai/gpt-4o-transcribe", "openai", "GPT-4o Transcribe", "transcribe", 16_000, 2_000, False, False, 0.006, 0.01),
    ]
    catalog.set_snapshot(chat, embeddings, audio=audio)
    yield
    catalog._current = None


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)
    yield
    Base.metadata.drop_all(test_engine)


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def authenticated_client(client: TestClient):
    response = client.post(
        "/api/auth/register",
        json={
            "agency_name": "Agencia Prisma",
            "name": "Ana Admin",
            "email": "ana@prisma.com",
            "password": "contrasena-segura",
        },
    )
    assert response.status_code == 201
    return client


def customer_conversation(client: TestClient, agent_id: str) -> dict:
    """A conversation as a customer would open it (widget channel), so it shows
    in the client portal. Playground conversations are rehearsals and never do."""
    from app.models import Conversation

    created = client.post("/api/conversations", json={"agent_id": agent_id}).json()
    with TestingSession() as db:
        conversation = db.get(Conversation, created["id"])
        conversation.channel = "widget"
        db.commit()
    created["channel"] = "widget"
    return created


def login_legacy_owner(client: TestClient) -> dict:
    """Preserve access isolation for data created by older releases.

    Seed the existing owner directly; current setup must never create another
    agency. Login still needs to work for every existing owner after an upgrade.
    """
    from app.models import Agency, User
    from app.security import hash_password

    credentials = {"email": "legacy-owner@example.com", "password": "legacy-owner-password"}
    with TestingSession() as db:
        db.add(User(
            agency=Agency(name="Legacy agency", slug="legacy-agency"),
            name="Legacy owner",
            email=credentials["email"],
            password_hash=hash_password(credentials["password"]),
        ))
        db.commit()
    response = client.post("/api/auth/login", json=credentials)
    assert response.status_code == 200, response.text
    return response.json()

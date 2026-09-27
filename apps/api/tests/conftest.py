import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://openlivery:openlivery@localhost:5432/openlivery_test",
)
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("TRIAL_ACTIVATION_ELIGIBLE_SINCE", "2026-01-01T00:00:00+00:00")

from app.database import Base, get_db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Client, User, now_utc  # noqa: E402


test_engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
TestingSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def clean_database(monkeypatch):
    # Keep tests deterministic even when a developer's local .env enables
    # multi-agency registration. Individual tests opt into that mode below.
    monkeypatch.setattr(get_settings(), "allow_multi_agency", False)
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


@pytest.fixture
def global_admin_client(authenticated_client: TestClient):
    """Use an explicit AYV global admin only in tests creating extra clients.

    Normal authenticated users must remain unable to create arbitrary clients;
    this fixture models the legitimate platform-admin path instead of weakening
    the endpoint or promoting the shared normal-user fixture.
    """
    with TestingSession() as db:
        user = db.scalar(select(User).where(User.email == "ana@prisma.com"))
        assert user is not None
        user.is_vendiq_admin = True
        initial_client = db.scalar(select(Client).where(Client.agency_id == user.agency_id))
        assert initial_client is not None
        initial_client.portal_email_verified_at = now_utc()
        db.commit()
    return authenticated_client

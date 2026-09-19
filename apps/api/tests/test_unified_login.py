import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Client, now_utc
from app.security import hash_password
from conftest import TestingSession


def _portal(client: TestClient, name: str, email: str, *, enabled: bool = True):
    customer = client.post(
        "/api/clients",
        json={"name": name, "industry": "", "description": "", "general_context": "", "is_active": True},
    ).json()
    configured = client.patch(
        f"/api/clients/{customer['id']}/portal",
        json={"portal_enabled": enabled, "portal_email": email, "portal_password": "portal-password"},
    )
    assert configured.status_code == 200
    with TestingSession() as db:
        db.get(Client, uuid.UUID(customer["id"])).portal_email_verified_at = now_utc()
        db.commit()
    return customer


def test_unified_login_routes_admin_and_portal_and_separates_cookies(authenticated_client: TestClient):
    client = authenticated_client
    customer = _portal(client, "Chiclayo Tours", "ventas@chiclayotours.pe")

    # The compatible slug login also removes any previous administrator session.
    portal_login = client.post(
        f"/api/portal/{customer['portal_slug']}/login",
        json={"email": "ventas@chiclayotours.pe", "password": "portal-password"},
    )
    assert portal_login.status_code == 200
    assert client.cookies.get("portal_access_token")
    assert client.cookies.get("access_token") is None

    admin_login = client.post(
        "/api/auth/unified-login",
        json={"email": "ana@prisma.com", "password": "contrasena-segura"},
    )
    assert admin_login.status_code == 200
    assert admin_login.json() == {"principal_type": "admin", "redirect_to": "/"}
    assert client.cookies.get("access_token")
    assert client.cookies.get("portal_access_token") is None

    portal_login = client.post(
        "/api/auth/unified-login",
        json={
            "email": "ventas@chiclayotours.pe",
            "password": "portal-password",
            "client_id": "forged-client",
            "slug": "forged-slug",
        },
    )
    assert portal_login.status_code == 200
    assert portal_login.json() == {
        "principal_type": "portal",
        "redirect_to": f"/portal/{customer['portal_slug']}",
    }
    assert client.cookies.get("portal_access_token")
    assert client.cookies.get("access_token") is None


def test_unified_login_rejects_invalid_credentials_and_disabled_portal(authenticated_client: TestClient):
    client = authenticated_client
    enabled = _portal(client, "Enabled", "enabled@example.com")
    disabled = _portal(client, "Disabled", "disabled@example.com", enabled=False)

    invalid = client.post(
        "/api/auth/unified-login",
        json={"email": "enabled@example.com", "password": "wrong-password"},
    )
    assert invalid.status_code == 401

    unavailable = client.post(
        "/api/auth/unified-login",
        json={"email": "disabled@example.com", "password": "portal-password"},
    )
    assert unavailable.status_code == 401
    assert unavailable.json() == invalid.json()
    assert enabled["portal_slug"] != disabled["portal_slug"]


def test_portal_email_collisions_are_prevented(authenticated_client: TestClient):
    client = authenticated_client
    first = _portal(client, "First", "shared@example.com")
    second = client.post(
        "/api/clients",
        json={"name": "Second", "industry": "", "description": "", "general_context": "", "is_active": True},
    ).json()

    duplicate = client.patch(
        f"/api/clients/{second['id']}/portal",
        json={"portal_enabled": True, "portal_email": "shared@example.com", "portal_password": "portal-password"},
    )
    assert duplicate.status_code == 409

    admin_collision = client.patch(
        f"/api/clients/{second['id']}/portal",
        json={"portal_enabled": True, "portal_email": "ana@prisma.com", "portal_password": "portal-password"},
    )
    assert admin_collision.status_code == 409
    assert first["id"] != second["id"]


def test_unified_login_rejects_legacy_duplicate_portal_email(authenticated_client: TestClient):
    client = authenticated_client
    _portal(client, "First", "first@example.com")
    _portal(client, "Second", "second@example.com")
    with TestingSession() as db:
        portals = db.scalars(select(Client).order_by(Client.created_at)).all()
        for portal in portals:
            portal.portal_email = "legacy-duplicate@example.com"
            portal.portal_password_hash = hash_password("same-password")
        db.commit()

    ambiguous = client.post(
        "/api/auth/unified-login",
        json={"email": "legacy-duplicate@example.com", "password": "same-password"},
    )
    assert ambiguous.status_code == 401
    assert ambiguous.json() == {"detail": "Incorrect email or password"}



@pytest.fixture(autouse=True)
def mock_portal_email_transport(monkeypatch):
    monkeypatch.setattr("app.services.portal_verification.send_verification_email", lambda *args: None)

from sqlalchemy import select

from app.models import Agent, Client, ClientSubscription, User
from app.services import portal_verification
from conftest import TestingSession


def test_registration_stays_pending_until_email_is_verified(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        portal_verification,
        "send_verification_email",
        lambda email, code: sent.append((email, code)),
    )

    registered = client.post(
        "/api/auth/register",
        json={
            "agency_name": "Empresa Demo",
            "industry": "Turismo",
            "name": "Ana Responsable",
            "email": "ana@empresa-demo.pe",
            "password": "contrasena-segura",
        },
    )

    assert registered.status_code == 201
    assert registered.json()["status"] == "verification_required"
    assert sent[0][0] == "ana@empresa-demo.pe"
    assert not client.cookies.get("access_token")
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/clients").status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": "ana@empresa-demo.pe", "password": "contrasena-segura"},
    ).json()["status"] == "verification_required"
    assert not client.cookies.get("access_token")

    with TestingSession() as db:
        user = db.scalar(select(User).where(User.email == "ana@empresa-demo.pe"))
        company = db.scalar(select(Client).where(Client.name == "Empresa Demo"))
        assert user is not None and company is not None
        assert company.portal_enabled is False
        assert company.portal_email_verified_at is None
        assert db.scalar(select(Agent).where(Agent.client_id == company.id)) is None
        assert db.scalar(select(ClientSubscription).where(ClientSubscription.client_id == company.id)) is None

    verification = client.post(
        "/api/portal/email-verification/confirm",
        json={"code": sent[0][1]},
    )
    assert verification.status_code == 200
    assert verification.json() == {"principal_type": "admin", "redirect_to": "/onboarding"}
    assert client.cookies.get("access_token")
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/clients").status_code == 200


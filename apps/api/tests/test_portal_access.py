import pytest
from fastapi.testclient import TestClient

import uuid

from app.models import Agent, Lead, Client, now_utc
from conftest import TestingSession


def _client_with_portal(client: TestClient, name: str, email: str, password: str):
    customer = client.post(
        "/api/clients",
        json={"name": name, "industry": "Services", "description": f"{name} profile", "general_context": "", "is_active": True},
    ).json()
    configured = client.patch(
        f"/api/clients/{customer['id']}/portal",
        json={"portal_enabled": True, "portal_email": email, "portal_password": password},
    )
    assert configured.status_code == 200
    with TestingSession() as db:
        db.get(Client, uuid.UUID(customer["id"])).portal_email_verified_at = now_utc()
        db.commit()
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "name": f"Agent {name}", "description": f"Assigned to {name}", "instructions": "", "personality": "", "model": "", "is_active": True},
    ).json()
    conversation = client.post("/api/conversations", json={"agent_id": agent["id"]}).json()
    with TestingSession() as db:
        agency_id = db.get(Agent, uuid.UUID(agent["id"])).agency_id
        lead = Lead(
            agency_id=agency_id, client_id=customer["id"], agent_id=agent["id"],
            name=f"Lead {name}", email=f"lead@{customer['portal_slug']}.test", source="playground", status="new",
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
        lead_id = str(lead.id)
    return customer, agent, conversation, lead_id


def test_portal_resources_are_derived_from_session_and_isolated(authenticated_client: TestClient):
    client = authenticated_client
    alpha, alpha_agent, alpha_conversation, alpha_lead = _client_with_portal(client, "Alpha", "alpha@example.com", "alpha-password")
    beta, beta_agent, beta_conversation, beta_lead = _client_with_portal(client, "Beta", "beta@example.com", "beta-password")

    login = client.post(f"/api/portal/{alpha['portal_slug']}/login", json={"email": "alpha@example.com", "password": "alpha-password"})
    assert login.status_code == 200

    # A forged browser query never changes the tenant selected from the signed portal session.
    summary = client.get(f"/api/portal/{alpha['portal_slug']}/summary?client_id={beta['id']}")
    assert summary.status_code == 200
    assert summary.json()["client_name"] == "Alpha"
    assert summary.json()["leads"] == 1
    assert summary.json()["conversations"] == 1

    leads = client.get(f"/api/portal/{alpha['portal_slug']}/leads?client_id={beta['id']}")
    assert [row["id"] for row in leads.json()] == [alpha_lead]
    assert beta_lead not in [row["id"] for row in leads.json()]
    agents = client.get(f"/api/portal/{alpha['portal_slug']}/agents")
    assert [row["id"] for row in agents.json()] == [alpha_agent["id"]]
    conversations = client.get(f"/api/portal/{alpha['portal_slug']}/conversations")
    assert [row["id"] for row in conversations.json()] == [alpha_conversation["id"]]

    # A token issued for Alpha is invalid under Beta's slug.
    assert client.get(f"/api/portal/{beta['portal_slug']}/summary").status_code == 401
    assert client.get(f"/api/portal/{beta['portal_slug']}/leads").status_code == 401

    # Foreign conversation IDs cannot be read or mutated through Alpha's portal.
    foreign = f"/api/portal/{alpha['portal_slug']}/conversations/{beta_conversation['id']}"
    assert client.get(foreign).status_code == 404
    assert client.patch(f"{foreign}/mode", json={"mode": "human"}).status_code == 404
    assert client.post(f"{foreign}/reply", json={"content": "forged"}).status_code == 404

    # Existing administrator routes keep agency-wide access to both clients.
    admin_login = client.post(
        "/api/auth/login",
        json={"email": "ana@prisma.com", "password": "contrasena-segura"},
    )
    assert admin_login.status_code == 200
    assert client.get(f"/api/clients/{alpha['id']}").status_code == 200
    assert client.get(f"/api/clients/{beta['id']}").status_code == 200


@pytest.fixture(autouse=True)
def mock_portal_email_transport(monkeypatch):
    monkeypatch.setattr("app.services.portal_verification.send_verification_email", lambda *args: None)

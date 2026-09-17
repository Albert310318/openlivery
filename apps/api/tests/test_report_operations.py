"""The agency-level operational report: conversations, timing and volume
across the agency's clients."""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Conversation, Message, now_utc
from app.routers import portal as portal_router


def _client_with_conversation(client: TestClient, monkeypatch):
    customer = client.post("/api/clients", json={"name": "Ops Co", "is_active": True}).json()
    client.post(f"/api/clients/{customer['id']}/portal-users", json={"name": "Ana", "email": "ana@ops.co", "password": "secure-portal"})
    client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "name": "Host", "instructions": "", "personality": "", "model": "", "is_active": True},
    ).json()
    client.put(f"/api/whatsapp/channels/{customer['id']}", json={"agent_id": agent["id"]})
    client.post(f"/api/portal/{customer['portal_slug']}/login", json={"email": "ana@ops.co", "password": "secure-portal"})
    monkeypatch.setattr(portal_router, "send_channel_message", AsyncMock(return_value="wamid.o1"))
    contact = client.post(f"/api/portal/{customer['portal_slug']}/contacts", json={"name": "Rita", "phone": "573001112233"}).json()
    conv = client.post(
        f"/api/portal/{customer['portal_slug']}/contacts/{contact['id']}/conversations",
        json={"channel": "whatsapp", "text": "Hola Rita"},
    ).json()
    with SessionLocal() as db:
        db.add(Message(conversation_id=uuid.UUID(conv["id"]), role="user", content="Hola!", sender_type="visitor"))
        row = db.get(Conversation, uuid.UUID(conv["id"]))
        row.status = "resolved"
        row.resolved_at = now_utc()
        row.first_reply_at = row.created_at + timedelta(seconds=60)
        db.commit()
    return customer


def test_operations_report_rolls_up_the_agency(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer = _client_with_conversation(client, monkeypatch)
    today = now_utc().date()
    frm = (today - timedelta(days=6)).isoformat()

    report = client.get(f"/api/reports/operations?from={frm}&to={today.isoformat()}").json()
    totals = report["totals"]
    assert totals["conversations"] == 1 and totals["contacts"] == 1 and totals["new_contacts"] == 1
    assert totals["handoffs"] == 1 and totals["ai_resolved"] == 0 and totals["open"] == 0
    assert totals["inbound"] == 1 and totals["human_replies"] == 1 and totals["ai_replies"] == 0
    assert totals["first_reply_s"] == 60.0
    assert report["by_client"][0]["name"] == "Ops Co" and report["by_client"][0]["conversations"] == 1
    assert report["by_channel"][0]["id"] == "whatsapp" and report["by_channel"][0]["conversations"] == 1
    assert sum(day["conversations"] for day in report["by_period"]) == 1

    # A client filter keeps its own; a foreign channel empties it.
    scoped = client.get(f"/api/reports/operations?from={frm}&to={today.isoformat()}&client_id={customer['id']}").json()
    assert scoped["totals"]["conversations"] == 1
    empty = client.get(f"/api/reports/operations?from={frm}&to={today.isoformat()}&channel=widget").json()
    assert empty["totals"]["conversations"] == 0 and empty["by_client"] == []

    # A reversed range is corrected, not rejected (conversation-level report).
    assert client.get(f"/api/reports/operations?from={today.isoformat()}&to={frm}").status_code == 200


def test_report_filters_list_the_agency_scope(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    _client_with_conversation(client, monkeypatch)
    filters = client.get("/api/reports/filters").json()
    assert any(c["name"] == "Ops Co" for c in filters["clients"])
    assert any(a["name"] == "Host" for a in filters["agents"])
    assert "whatsapp" in filters["channels"]

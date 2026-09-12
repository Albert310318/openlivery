"""Cost reports: every reply's usage is linked to its conversation and
message and priced as the provider reported it (or at list price when it
did not), then rolled up by client, agent, model and day."""

from datetime import date, timedelta
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from conftest import TestingSession

from app.models import Message, UsageRecord
from app.routers import conversations as conversations_router
from app.services import ai as ai_service


def _period() -> str:
    today = date.today()
    return f"from={(today - timedelta(days=1)).isoformat()}&to={(today + timedelta(days=1)).isoformat()}"


def _setup(client: TestClient):
    customer = client.post("/api/clients", json={"name": "Bistro", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "provider": "openrouter", "model": "openai/gpt-5.6-luna", "name": "Host",
        "instructions": "", "personality": "", "is_active": True,
    }).json()
    conversation = client.post("/api/conversations", json={"agent_id": agent["id"]}).json()
    return customer, agent, conversation


def _reply(client: TestClient, monkeypatch, conversation: dict, completion: ai_service.Completion, content: str) -> dict:
    monkeypatch.setattr(conversations_router, "run_completion", AsyncMock(return_value=completion))
    resp = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": content})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_replies_are_linked_priced_and_rolled_up(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer, agent, conversation = _setup(client)
    priced = ai_service.Completion(text="Hi", input_tokens=1_000, output_tokens=100, cost_usd=0.5,
                                   served_by="Azure", cached_tokens=10, reasoning_tokens=3, duration_ms=1200)
    detail = _reply(client, monkeypatch, conversation, priced, "hello")
    # A reply the provider did not price: luna is 0.0002 per 1k input tokens
    # in the catalog, so a million tokens is estimated at 0.2.
    _reply(client, monkeypatch, conversation, ai_service.Completion(text="Again", input_tokens=1_000_000, output_tokens=0), "again")

    # The usage record points at the conversation and the assistant message.
    assistant_ids = {m["id"] for m in detail["messages"] if m["role"] == "assistant"}
    with TestingSession() as db:
        records = db.query(UsageRecord).order_by(UsageRecord.created_at).all()
        assert [str(r.conversation_id) for r in records] == [conversation["id"]] * 2
        assert str(records[0].message_id) in assistant_ids
        assert db.get(Message, records[0].message_id).content == "Hi"
        assert (records[0].served_by, records[0].cached_tokens, records[0].reasoning_tokens, records[0].duration_ms) == ("Azure", 10, 3, 1200)

    report = client.get(f"/api/reports/costs?{_period()}&tz=America/Bogota").json()
    totals = report["totals"]
    assert totals["cost_usd"] == 0.7 and totals["replies"] == 2 and totals["conversations"] == 1
    assert totals["input_tokens"] == 1_001_000 and totals["output_tokens"] == 100
    assert totals["avg_cost_per_reply_usd"] == 0.35
    assert report["by_client"] == [{"id": customer["id"], "name": "Bistro", "replies": 2, "input_tokens": 1_001_000, "output_tokens": 100, "cost_usd": 0.7}]
    assert report["by_agent"][0]["id"] == agent["id"] and report["by_agent"][0]["name"] == "Host"
    assert report["by_model"] == [{"id": "openai/gpt-5.6-luna", "name": "openai/gpt-5.6-luna", "replies": 2, "input_tokens": 1_001_000, "output_tokens": 100, "cost_usd": 0.7}]
    assert len(report["by_day"]) == 1 and report["by_day"][0]["replies"] == 2 and report["by_day"][0]["cost_usd"] == 0.7
    assert report["tz"] == "America/Bogota"
    assert client.get(f"/api/reports/costs?{_period()}&tz=Not/AZone").json()["tz"] == "UTC"

    page = client.get(f"/api/reports/replies?{_period()}&limit=1").json()
    assert page["total"] == 2
    newest = page["items"][0]
    assert newest["cost_usd"] == 0.2 and newest["estimated"] is True and newest["served_by"] == ""
    older = client.get(f"/api/reports/replies?{_period()}&limit=1&offset=1").json()["items"][0]
    assert older["cost_usd"] == 0.5 and older["estimated"] is False
    assert older["conversation_id"] == conversation["id"] and older["client_name"] == "Bistro" and older["agent_name"] == "Host"
    assert older["channel"] == "playground" and older["served_by"] == "Azure" and older["duration_ms"] == 1200
    assert older["cached_tokens"] == 10 and older["reasoning_tokens"] == 3 and older["tools"] == 0

    # Filters narrow, the search finds the conversation by id prefix.
    assert client.get(f"/api/reports/replies?{_period()}&model=openai/gpt-5.6-sol").json()["total"] == 0
    assert client.get(f"/api/reports/replies?{_period()}&client_id={customer['id']}").json()["total"] == 2
    assert client.get(f"/api/reports/replies?{_period()}&q={conversation['id'][:8]}").json()["total"] == 2
    assert client.get(f"/api/reports/replies?{_period()}&q=nobody").json()["total"] == 0

    csv_resp = client.get(f"/api/reports/replies?{_period()}&format=csv")
    assert csv_resp.status_code == 200 and csv_resp.headers["content-type"].startswith("text/csv")
    lines = csv_resp.text.strip().splitlines()
    assert lines[0].startswith("date,reply_id,conversation,contact,client,agent,channel,model,served_by")
    assert len(lines) == 3 and lines[1].endswith(",0.200000,yes,") and lines[2].endswith(",0.500000,,1200")


def test_reports_need_a_session_and_start_empty(client: TestClient):
    assert client.get(f"/api/reports/costs?{_period()}").status_code == 401
    assert client.get(f"/api/reports/replies?{_period()}").status_code == 401
    client.post("/api/auth/register", json={
        "agency_name": "Agencia Prisma", "name": "Ana Admin", "email": "ana@prisma.com", "password": "contrasena-segura",
    })
    empty = client.get(f"/api/reports/costs?{_period()}").json()
    assert empty["totals"]["replies"] == 0 and empty["by_client"] == [] and empty["by_day"] == []

import asyncio
import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import Contact, Conversation, Message, PortalUser, WhatsAppChannel, now_utc
from app.schemas import WhatsAppOutgoing
from app.services import phone_handover as handover, whatsapp_inbound as pipeline
from app.services.ai import Completion
from app.services.conversation_state import assign, set_mode, set_status
from conftest import TestingSession

CHAT = "573001112233@s.whatsapp.net"


@pytest.fixture
def setup(authenticated_client, monkeypatch):
    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Barber shop", "industry": "other"}).json()
    assert client.put("/api/providers/openrouter", json={"api_key": "sk-test"}).status_code == 200
    agent = client.post("/api/agents", json={"client_id": customer["id"], "name": "Appointments",
        "model": "openai/gpt-4.1-mini", "prompt_language": "en"}).json()
    channel = client.put(f"/api/whatsapp/channels/{customer['id']}", json={"agent_id": agent["id"]}).json()
    channel_id = uuid.UUID(channel["id"])
    with TestingSession() as db:
        row = db.get(WhatsAppChannel, channel_id)
        row.status = "connected"
        row.is_enabled = True
        db.commit()
    completion = AsyncMock(return_value=Completion(text="The cut costs $30."))
    send = AsyncMock(return_value="sent-1")
    monkeypatch.setattr(pipeline, "run_completion", completion)
    monkeypatch.setattr("app.services.whatsapp.send_channel_message", send)
    monkeypatch.setattr("app.routers.conversations.send_channel_message", send)
    return client, channel_id, {"X-Bridge-Token": get_settings().whatsapp_bridge_token}, completion, send


def phone(setup, mid="phone-1", **extra):
    client, channel_id, headers, *_ = setup
    response = client.post(f"/api/internal/whatsapp/channels/{channel_id}/outgoing", headers=headers,
        json={"external_message_id": mid, "remote_jid": CHAT, "text": "The cut costs $30.", **extra})
    assert response.status_code == 204, response.text
    with TestingSession() as db:
        return db.scalar(select(Conversation.id).where(Conversation.whatsapp_channel_id == channel_id))


def incoming(setup, mid="customer-1"):
    client, channel_id, headers, *_ = setup
    response = client.post(f"/api/internal/whatsapp/channels/{channel_id}/inbound", headers=headers,
        json={"external_message_id": mid, "remote_jid": CHAT, "text": "And with a beard trim?"})
    assert response.status_code == 200, response.text
    return response.json()


def expire(conversation_id):
    with TestingSession() as db:
        row = db.get(Conversation, conversation_id)
        row.phone_pause_until = now_utc() - timedelta(seconds=1)
        db.commit()


def sweep():
    with TestingSession() as db:
        asyncio.run(handover.resume_due(db))


def test_customer_does_not_resume_early_then_fresh_worker_answers_with_human_context(setup):
    client, _, _, completion, send = setup
    cid = phone(setup)
    assert incoming(setup)["mode"] == "human"
    sweep()
    completion.assert_not_awaited()
    expire(cid)
    sweep()
    sweep()
    completion.assert_awaited_once()
    send.assert_awaited_once()
    turns = completion.call_args.args[4]
    assert any("Written by a person from the business" in turn["content"] and "$30" in turn["content"] for turn in turns)
    state = client.get(f"/api/conversations/{cid}").json()
    assert state["mode"] == "ai" and state["phone_pause_until"] is None


@pytest.mark.parametrize("manual_first", [True, False])
def test_phone_never_overrides_manual_inbox_control(setup, manual_first):
    client, _, _, completion, send = setup
    cid = phone(setup)
    if manual_first:
        client.patch(f"/api/conversations/{cid}/mode", json={"mode": "human"})
        phone(setup, "another-phone-reply")
    else:
        # Already human from the phone; choosing human again must still cancel the timer.
        client.patch(f"/api/conversations/{cid}/mode", json={"mode": "human"})
    assert incoming(setup)["mode"] == "human"
    sweep()
    state = client.get(f"/api/conversations/{cid}").json()
    assert state["mode"] == "human" and state["phone_pause_until"] is None
    completion.assert_not_awaited()
    send.assert_not_awaited()


def test_assignment_and_inbox_reply_cancel_the_timer(setup):
    client, _, _, _, _ = setup
    cid = phone(setup)
    with TestingSession() as db:
        row = db.get(Conversation, cid)
        person = PortalUser(client_id=row.client_id, name="Operator", email="operator@example.com", password_hash="unused")
        db.add(person)
        db.flush()
        assert assign(db, row, person)
        assert row.phone_pause_until is None and row.mode == "human"
        db.commit()
    phone(setup, "after-assignment")
    assert client.get(f"/api/conversations/{cid}").json()["phone_pause_until"] is None


def test_phone_reply_resets_deadline_and_no_pending_customer_means_no_send(setup):
    cid = phone(setup)
    expire(cid)
    phone(setup, "phone-2")
    with TestingSession() as db:
        assert db.get(Conversation, cid).phone_pause_until > now_utc() + timedelta(minutes=9)
    expire(cid)
    sweep()
    setup[3].assert_not_awaited()
    setup[4].assert_not_awaited()


def test_business_first_contact_respects_blocking(setup):
    cid = phone(setup)
    with TestingSession() as db:
        conversation = db.get(Conversation, cid)
        assert conversation.contact_id is not None
        conversation.contact.blocked_at = now_utc()
        db.commit()
    incoming(setup)
    expire(cid)
    sweep()
    setup[3].assert_not_awaited()


def test_replayed_phone_history_is_stored_without_taking_control(setup):
    cid = phone(setup, occurred_at=(now_utc() - timedelta(days=1)).isoformat())
    with TestingSession() as db:
        row = db.get(Conversation, cid)
        assert row.phone_pause_until is None and row.mode == "ai"
        assert db.scalar(select(Message).where(Message.conversation_id == cid)).created_at < now_utc() - timedelta(hours=23)


def test_concurrent_first_delivery_creates_one_contact_conversation_and_message(setup):
    channel_id = setup[1]
    def receive():
        with TestingSession() as db:
            handover.record_outgoing(db, db.get(WhatsAppChannel, channel_id),
                WhatsAppOutgoing(external_message_id="same", remote_jid=CHAT, text="Hello"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: receive(), range(2)))
    with TestingSession() as db:
        assert len(db.scalars(select(Conversation)).all()) == 1
        assert len(db.scalars(select(Contact)).all()) == 1
        assert len(db.scalars(select(Message).where(Message.kind == "message")).all()) == 1


def test_lease_prevents_second_worker_and_resolution_cancels_pause(setup):
    cid = phone(setup)
    incoming(setup)
    expire(cid)
    with TestingSession() as db:
        row = db.get(Conversation, cid)
        row.phone_resume_claimed_until = now_utc() + timedelta(minutes=3)
        db.commit()
    sweep()
    setup[3].assert_not_awaited()
    with TestingSession() as db:
        row = db.get(Conversation, cid)
        set_status(db, row, "resolved")
        assert row.phone_pause_until is None
        db.commit()


def test_bad_token_disabled_channel_and_naive_timestamp_are_rejected(setup):
    client, channel_id, headers, *_ = setup
    url = f"/api/internal/whatsapp/channels/{channel_id}/outgoing"
    payload = {"external_message_id": "one", "remote_jid": CHAT, "text": "Hello"}
    assert client.post(url, json=payload).status_code == 401
    assert client.post(url, headers=headers, json={**payload, "occurred_at": "2026-09-12T10:00:00"}).status_code == 422
    with TestingSession() as db:
        db.get(WhatsAppChannel, channel_id).is_enabled = False
        db.commit()
    assert client.post(url, headers=headers, json=payload).status_code == 409


def test_manual_takeover_during_generation_cancels_delivery(setup):
    cid = phone(setup)
    incoming(setup)
    expire(cid)
    async def takeover(*args, **kwargs):
        with TestingSession() as db:
            row = db.get(Conversation, cid)
            set_mode(db, row, "human")
            db.commit()
        return Completion(text="This stale response must not be sent.")
    setup[3].side_effect = takeover
    sweep()
    setup[4].assert_not_awaited()
    with TestingSession() as db:
        row = db.get(Conversation, cid)
        assert row.mode == "human" and row.phone_pause_until is None


def test_operator_reply_keeps_manual_control(setup):
    cid = phone(setup)
    response = setup[0].post(f"/api/conversations/{cid}/reply", json={"content": "I will help you personally."})
    assert response.status_code == 200, response.text
    assert response.json()["phone_pause_until"] is None
    assert incoming(setup)["mode"] == "human"
    setup[3].assert_not_awaited()


def test_pause_duration_is_saved_and_validated(setup):
    cid = phone(setup)
    with TestingSession() as db:
        agent_id = db.get(Conversation, cid).agent_id
    client = setup[0]
    assert client.patch(f"/api/agents/{agent_id}", json={"phone_handover_minutes": 3}).json()["phone_handover_minutes"] == 3
    for value in [0, -1, 1441]:
        assert client.patch(f"/api/agents/{agent_id}", json={"phone_handover_minutes": value}).status_code == 422
    # Changing configuration takes effect on the next newly started pause.
    client.patch(f"/api/conversations/{cid}/mode", json={"mode": "ai"})
    phone(setup, "new-pause")
    with TestingSession() as db:
        assert now_utc() + timedelta(minutes=2) < db.get(Conversation, cid).phone_pause_until < now_utc() + timedelta(minutes=4)

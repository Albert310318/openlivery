import asyncio
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Agent, Client, Conversation, Lead, LeadConversation, LeadHandoff, Message, WhatsAppChannel
from app.services import lead_handoffs
from app.services.lead_handoffs import LeadHandoffError, _people_from_notes, notify_sales_advisor
from app.services.whatsapp_inbound import LUCIA_INTRO, _starts_new_lucia_session, _with_lucia_session_intro
from app.services.leads import create_or_update_lead, lead_context_from_conversation
from app.schemas_leads import LeadCaptureInput
from app.services.knowledge import build_system_prompt
from conftest import TestingSession


LUCIA_TOUR_QUALIFICATION = (
    "En tu primera respuesta de cada conversación nueva, si todavía no existe un mensaje previo tuyo en el historial, "
    "comienza con «Hola, soy Lucía, asesora virtual de Chiclayo Tours 😊». Preséntate una sola vez y no repitas esa "
    "presentación cuando ya exista una respuesta anterior tuya. "
    "Antes de ofrecer la derivación, intenta completar naturalmente los datos útiles que falten, haciendo una sola "
    "pregunta por turno: tour/destino/experiencia, fecha aproximada, cantidad de personas, nombre y preferencia de "
    "contacto. Nunca vuelvas a preguntar un dato ya proporcionado. Conserva literalmente la cantidad de personas "
    "en notes con evidencia del prospecto. Si pide hablar inmediatamente con un asesor, no bloquees la derivación."
)


def _setup(client: TestClient, name: str, advisor: str | None = "+51987654321") -> tuple[str, str]:
    customer = client.post("/api/clients", json={"name": name}).json()
    if advisor:
        response = client.patch(f"/api/clients/{customer['id']}", json={"sales_advisor_phone": advisor})
        assert response.status_code == 200
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "name": f"Agent {name}", "model": "gpt-5", "is_active": True},
    ).json()
    with TestingSession() as db:
        channel = WhatsAppChannel(
            agency_id=uuid.UUID(customer["id"]),  # replaced below from the client row
            client_id=uuid.UUID(customer["id"]),
            agent_id=uuid.UUID(agent["id"]),
            status="connected",
            is_enabled=True,
        )
        row = db.get(Client, uuid.UUID(customer["id"]))
        channel.agency_id = row.agency_id
        db.add(channel)
        db.flush()
        conversation = Conversation(
            agency_id=row.agency_id,
            client_id=row.id,
            agent_id=uuid.UUID(agent["id"]),
            channel="whatsapp",
            whatsapp_channel_id=channel.id,
            external_chat_id="51999111222@s.whatsapp.net",
            title="Prospecto",
        )
        db.add(conversation)
        db.flush()
        lead = Lead(
            agency_id=row.agency_id,
            client_id=row.id,
            agent_id=uuid.UUID(agent["id"]),
            name="Rosa Pérez",
            phone="+51999111222",
            phone_normalized="+51999111222",
            interest="Quiero un tour a Cusco",
            budget="S/ 2500",
            preferred_contact_time="mañana a las 10",
            source="whatsapp",
            status="qualified",
        )
        db.add(lead)
        db.flush()
        db.add(LeadConversation(lead_id=lead.id, conversation_id=conversation.id))
        db.commit()
        return str(row.id), str(conversation.id)


def _add_consent(conversation_id: str, text: str, previous_agent_text: str | None = None) -> None:
    with TestingSession() as db:
        if previous_agent_text:
            db.add(Message(conversation_id=uuid.UUID(conversation_id), role="assistant", content=previous_agent_text, sender_type="ai"))
            db.commit()
        db.add(Message(conversation_id=uuid.UUID(conversation_id), role="user", content=text, sender_type="visitor"))
        db.commit()


def test_sales_advisor_phone_can_be_saved_changed_and_removed(authenticated_client: TestClient):
    customer = authenticated_client.post("/api/clients", json={"name": "Tours"}).json()
    url = f"/api/clients/{customer['id']}"
    saved = authenticated_client.patch(url, json={"sales_advisor_phone": "+51 (987) 654-321"})
    assert saved.status_code == 200
    assert saved.json()["sales_advisor_phone"] == "+51987654321"
    changed = authenticated_client.patch(url, json={"sales_advisor_phone": "51911122233"})
    assert changed.json()["sales_advisor_phone"] == "51911122233"
    removed = authenticated_client.patch(url, json={"sales_advisor_phone": None})
    assert removed.json()["sales_advisor_phone"] is None


def test_explicit_consent_sends_persisted_lead_once(authenticated_client: TestClient, monkeypatch):
    _, conversation_id = _setup(authenticated_client, "Chiclayo Tours")
    consent = "Sí, quiero que un asesor me escriba"
    _add_consent(conversation_id, consent)
    sender = AsyncMock(return_value={"external_message_id": "wa-out-1"})
    monkeypatch.setattr(lead_handoffs, "bridge_command", sender)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        context = lead_context_from_conversation(conversation)
        first = asyncio.run(notify_sales_advisor(db, context, consent))
        second = asyncio.run(notify_sales_advisor(db, context, consent))
        handoffs = db.scalars(select(LeadHandoff)).all()
    assert first == {"ok": True, "duplicate": False, "status": "sent"}
    assert second == {"ok": True, "duplicate": True, "status": "sent"}
    sender.assert_awaited_once()
    payload = sender.await_args.args[2]
    assert payload["remote_jid"] == "51987654321@s.whatsapp.net"
    assert "Rosa Pérez" in payload["text"]
    assert "+51999111222" in payload["text"]
    assert "Quiero un tour a Cusco" in payload["text"]
    assert "S/ 2500" in payload["text"]
    assert "mañana a las 10" in payload["text"]
    assert len(handoffs) == 1


def test_ambiguous_or_missing_consent_does_not_send(authenticated_client: TestClient, monkeypatch):
    _, conversation_id = _setup(authenticated_client, "Ambiguous")
    _add_consent(conversation_id, "Sí")
    sender = AsyncMock()
    monkeypatch.setattr(lead_handoffs, "bridge_command", sender)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        try:
            asyncio.run(notify_sales_advisor(db, lead_context_from_conversation(conversation), "Sí"))
        except LeadHandoffError:
            pass
        else:
            raise AssertionError("Ambiguous consent was accepted")
    sender.assert_not_awaited()


def test_contact_time_is_consent_after_advisor_coordination(authenticated_client: TestClient, monkeypatch):
    _, conversation_id = _setup(authenticated_client, "Contextual")
    consent = "Por las tardes por favor"
    _add_consent(
        conversation_id,
        consent,
        "Un asesor puede continuar con la atención; ¿en qué horario prefieres que te contacten?",
    )
    sender = AsyncMock(return_value={"external_message_id": "wa-contextual"})
    monkeypatch.setattr(lead_handoffs, "bridge_command", sender)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        result = asyncio.run(notify_sales_advisor(db, lead_context_from_conversation(conversation), consent))
    assert result["ok"] is True
    sender.assert_awaited_once()


def test_clear_advisor_continuation_followed_by_yes_or_ok_is_valid(authenticated_client: TestClient, monkeypatch):
    sender = AsyncMock(return_value={"external_message_id": "wa-clear-proposal"})
    monkeypatch.setattr(lead_handoffs, "bridge_command", sender)
    for index, consent in enumerate(("Sí", "Ok")):
        _, conversation_id = _setup(authenticated_client, f"Clear proposal {index}")
        _add_consent(conversation_id, consent, "Un asesor puede continuar con la atención")
        with TestingSession() as db:
            conversation = db.get(Conversation, uuid.UUID(conversation_id))
            result = asyncio.run(notify_sales_advisor(db, lead_context_from_conversation(conversation), consent))
        assert result["ok"] is True
    assert sender.await_count == 2


def test_casual_advisor_mention_followed_by_yes_is_not_valid(authenticated_client: TestClient, monkeypatch):
    _, conversation_id = _setup(authenticated_client, "Casual mention")
    _add_consent(conversation_id, "Sí", "Ayer hablé con un asesor sobre nuestros tours")
    sender = AsyncMock()
    monkeypatch.setattr(lead_handoffs, "bridge_command", sender)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        try:
            asyncio.run(notify_sales_advisor(db, lead_context_from_conversation(conversation), "Sí"))
        except LeadHandoffError:
            pass
        else:
            raise AssertionError("A casual advisor mention was treated as a proposal")
    sender.assert_not_awaited()


def test_contact_time_without_advisor_context_does_not_send(authenticated_client: TestClient, monkeypatch):
    _, conversation_id = _setup(authenticated_client, "No context")
    consent = "Por las tardes"
    _add_consent(conversation_id, consent, "¿Qué horario tiene la tienda?")
    sender = AsyncMock()
    monkeypatch.setattr(lead_handoffs, "bridge_command", sender)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        try:
            asyncio.run(notify_sales_advisor(db, lead_context_from_conversation(conversation), consent))
        except LeadHandoffError:
            pass
        else:
            raise AssertionError("An isolated time preference was accepted")
    sender.assert_not_awaited()


def test_explicit_rejection_after_advisor_question_does_not_send(authenticated_client: TestClient, monkeypatch):
    _, conversation_id = _setup(authenticated_client, "Rejected")
    consent = "No, por la tarde tampoco puedo"
    _add_consent(conversation_id, consent, "¿A qué hora puede llamarte un asesor?")
    sender = AsyncMock()
    monkeypatch.setattr(lead_handoffs, "bridge_command", sender)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        try:
            asyncio.run(notify_sales_advisor(db, lead_context_from_conversation(conversation), consent))
        except LeadHandoffError:
            pass
        else:
            raise AssertionError("An explicit rejection was accepted")
    sender.assert_not_awaited()


def test_unconfigured_advisor_does_not_send(authenticated_client: TestClient, monkeypatch):
    _, conversation_id = _setup(authenticated_client, "No advisor", advisor=None)
    consent = "Sí, pueden llamarme"
    _add_consent(conversation_id, consent)
    sender = AsyncMock()
    monkeypatch.setattr(lead_handoffs, "bridge_command", sender)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        try:
            asyncio.run(notify_sales_advisor(db, lead_context_from_conversation(conversation), consent))
        except LeadHandoffError:
            pass
        else:
            raise AssertionError("Unconfigured handoff was accepted")
    sender.assert_not_awaited()


def test_client_a_never_uses_client_b_advisor(authenticated_client: TestClient, monkeypatch):
    _, conversation_a = _setup(authenticated_client, "Client A", "+51911111111")
    _setup(authenticated_client, "Client B", "+51922222222")
    consent = "Sí, que me contacte un asesor"
    _add_consent(conversation_a, consent)
    sender = AsyncMock(return_value={"external_message_id": "wa-out-a"})
    monkeypatch.setattr(lead_handoffs, "bridge_command", sender)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_a))
        asyncio.run(notify_sales_advisor(db, lead_context_from_conversation(conversation), consent))
    assert sender.await_args.args[2]["remote_jid"] == "51911111111@s.whatsapp.net"
    assert "51922222222" not in sender.await_args.args[2]["text"]


def test_tampered_agency_context_never_sends(authenticated_client: TestClient, monkeypatch):
    _, conversation_id = _setup(authenticated_client, "Tenant protected")
    consent = "Sí, quiero que un asesor me escriba"
    _add_consent(conversation_id, consent)
    sender = AsyncMock()
    monkeypatch.setattr(lead_handoffs, "bridge_command", sender)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        context = replace(lead_context_from_conversation(conversation), agency_id=uuid.uuid4())
        try:
            asyncio.run(notify_sales_advisor(db, context, consent))
        except LeadHandoffError:
            pass
        else:
            raise AssertionError("A cross-agency context was accepted")
    sender.assert_not_awaited()


def test_people_are_preserved_in_notes_and_included_in_advisor_message(authenticated_client: TestClient, monkeypatch):
    _, conversation_id = _setup(authenticated_client, "Tour party")
    consent = "Sí, quiero que un asesor me escriba"
    _add_consent(conversation_id, consent)
    sender = AsyncMock(return_value={"external_message_id": "wa-party"})
    monkeypatch.setattr(lead_handoffs, "bridge_command", sender)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        context = lead_context_from_conversation(conversation)
        result = create_or_update_lead(db, context, LeadCaptureInput(notes="2 adultos y 2 niños"))
        assert result.lead.notes == "2 adultos y 2 niños"
        db.commit()
        asyncio.run(notify_sales_advisor(db, context, consent))
    assert "Personas: 2 adultos y 2 niños" in sender.await_args.args[2]["text"]


def test_complete_party_composition_is_normalized_without_truncating_members():
    cases = {
        "2 adultos 1 niño": "2 adultos y 1 niño",
        "2 adultos y 2 niños": "2 adultos y 2 niños",
        "4 adultos": "4 adultos",
        "3 personas": "3 personas",
        "2 adultos, 1 niño y 1 bebé": "2 adultos y 1 niño y 1 bebé",
    }
    for evidenced, expected in cases.items():
        assert _people_from_notes(evidenced) == expected


def test_advisor_message_without_people_does_not_invent_them(authenticated_client: TestClient, monkeypatch):
    _, conversation_id = _setup(authenticated_client, "Unknown party")
    consent = "Sí, pueden llamarme"
    _add_consent(conversation_id, consent)
    sender = AsyncMock(return_value={"external_message_id": "wa-no-party"})
    monkeypatch.setattr(lead_handoffs, "bridge_command", sender)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        asyncio.run(notify_sales_advisor(db, lead_context_from_conversation(conversation), consent))
    assert "Personas:" not in sender.await_args.args[2]["text"]


def test_trusted_whatsapp_phone_is_used_without_mutating_lead(authenticated_client: TestClient, monkeypatch):
    _, conversation_id = _setup(authenticated_client, "LID fallback")
    consent = "Sí, quiero que un asesor me escriba"
    _add_consent(conversation_id, consent)
    sender = AsyncMock(return_value={"external_message_id": "wa-lid"})
    monkeypatch.setattr(lead_handoffs, "bridge_command", sender)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        conversation.external_chat_id = "opaque-contact@lid"
        lead = db.scalar(select(Lead).join(LeadConversation).where(LeadConversation.conversation_id == conversation.id))
        lead.phone = None
        lead.phone_normalized = None
        db.commit()
        context = lead_context_from_conversation(conversation, trusted_sender_jid="51988776655@s.whatsapp.net")
        asyncio.run(notify_sales_advisor(db, context, consent))
        db.refresh(lead)
        assert lead.phone is None
        assert lead.phone_normalized is None
    assert "Teléfono: 51988776655" in sender.await_args.args[2]["text"]


def test_lucia_tour_qualification_is_specific_and_does_not_repeat_known_people(authenticated_client: TestClient):
    _, lucia_conversation_id = _setup(authenticated_client, "Chiclayo Tours")
    _, other_conversation_id = _setup(authenticated_client, "Other Client")
    with TestingSession() as db:
        lucia = db.get(Conversation, uuid.UUID(lucia_conversation_id)).agent
        other = db.get(Conversation, uuid.UUID(other_conversation_id)).agent
        lucia.name = "Lucía – Asesora Virtual"
        lucia.instructions = LUCIA_TOUR_QUALIFICATION
        db.flush()
        lucia_prompt = build_system_prompt(lucia, "")
        other_prompt = build_system_prompt(other, "")
    assert "cantidad de personas" in lucia_prompt
    assert "Nunca vuelvas a preguntar un dato ya proporcionado" in lucia_prompt
    assert "una sola pregunta por turno" in lucia_prompt
    assert "no bloquees la derivación" in lucia_prompt
    assert "Hola, soy Lucía, asesora virtual de Chiclayo Tours 😊" in lucia_prompt
    assert "Preséntate una sola vez" in lucia_prompt
    assert "no repitas esa presentación" in lucia_prompt
    assert "cantidad de personas" not in other_prompt
    assert "Hola, soy Lucía" not in other_prompt


def test_lucia_introduction_uses_deterministic_session_boundaries(authenticated_client: TestClient):
    _, conversation_id = _setup(authenticated_client, "Chiclayo Tours")
    now = datetime.now(timezone.utc)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        agent = conversation.agent
        agent.name = "Lucía – Asesora Virtual"
        assert _starts_new_lucia_session(None, agent, now) is True
        conversation.updated_at = now - timedelta(hours=13)
        assert _starts_new_lucia_session(conversation, agent, now) is True
        assert _with_lucia_session_intro("¿En qué te ayudo?", True).startswith(LUCIA_INTRO)
        conversation.updated_at = now - timedelta(minutes=10)
        assert _starts_new_lucia_session(conversation, agent, now) is False
        assert _with_lucia_session_intro("Continuemos", False) == "Continuemos"

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from sqlalchemy import select

from app.models import Conversation, Lead, LeadConversation, Message, WhatsAppChannel
from app.config import get_settings
from app.schemas_leads import LeadCaptureInput
from app.services import ai as ai_service
from app.services import leads as leads_service
from app.services.leads import create_or_update_lead, lead_context_from_conversation
from app.services.tools import loop as tool_loop
from app.services.tools.internal import execute_internal_tool
from app.services.tools import internal as internal_tools
from app.routers import dashboard as dashboard_router
from conftest import TestingSession


def _setup(client: TestClient, client_name: str = "VendeIA Customer") -> tuple[str, str, str]:
    customer = client.post(
        "/api/clients",
        json={"name": client_name, "industry": "Sales", "description": "", "general_context": "", "is_active": True},
    ).json()
    client.put("/api/providers/openai", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={
            "client_id": customer["id"],
            "provider": "openai",
            "model": "gpt-5",
            "name": "Astrid",
            "description": "",
            "instructions": "Qualify sales prospects.",
            "personality": "Professional",
            "is_active": True,
        },
    ).json()
    conversation = client.post("/api/conversations", json={"agent_id": agent["id"]}).json()
    return customer["id"], agent["id"], conversation["id"]


def _capture(conversation_id: str, **data):
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        result = create_or_update_lead(
            db,
            lead_context_from_conversation(conversation),
            LeadCaptureInput(**data),
        )
        db.commit()
        return result.lead.id, result.created


def _make_verified_whatsapp_conversation(conversation_id: str, sender: str = "51999123456") -> None:
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        channel = WhatsAppChannel(
            agency_id=conversation.agency_id,
            client_id=conversation.client_id,
            agent_id=conversation.agent_id,
            status="connected",
            is_enabled=True,
        )
        db.add(channel)
        db.flush()
        conversation.channel = "whatsapp"
        conversation.whatsapp_channel_id = channel.id
        conversation.external_chat_id = f"{sender}@s.whatsapp.net"
        db.commit()


def _make_lid_whatsapp_conversation(conversation_id: str) -> None:
    _make_verified_whatsapp_conversation(conversation_id)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        conversation.external_chat_id = "opaque-linked-identity@lid"
        db.commit()


def test_lid_alternate_identity_creates_and_updates_one_lead_without_rewriting_conversation(
    authenticated_client: TestClient,
):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    _make_lid_whatsapp_conversation(conversation_id)

    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        original_external_chat_id = conversation.external_chat_id
        context = lead_context_from_conversation(
            conversation,
            trusted_sender_jid="51999123456@s.whatsapp.net",
        )
        interest = "Quiero comprar un lote para construir mi casa"
        db.add(Message(conversation_id=conversation.id, role="user", content=interest, sender_type="visitor"))
        db.commit()
        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "create_or_update_lead",
            {"interest": interest, "evidence": {"interest": interest}},
            context,
        ))
        assert is_error is False, result
        assert json.loads(result)["created"] is True
        db.commit()
        lead = db.scalar(select(Lead).join(LeadConversation).where(
            LeadConversation.conversation_id == conversation.id
        ))
        lead_id = lead.id
        assert lead.phone_normalized == "51999123456"

        update = "Soy Jean, mi presupuesto es S/ 90,000 y prefiero mañana a las 9"
        db.add(Message(conversation_id=conversation.id, role="user", content=update, sender_type="visitor"))
        db.commit()
        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "create_or_update_lead",
            {
                "name": "Jean",
                "budget": "S/ 90,000",
                "preferred_contact_time": "mañana a las 9",
                "evidence": {
                    "name": "Soy Jean",
                    "budget": "mi presupuesto es S/ 90,000",
                    "preferred_contact_time": "prefiero mañana a las 9",
                },
            },
            context,
        ))
        assert is_error is False, result
        db.commit()
        assert json.loads(result)["created"] is False
        updated = db.get(Lead, lead_id)
        assert updated.name == "Jean"
        assert updated.budget == "S/ 90,000"
        assert updated.preferred_contact_time == "mañana a las 9"
        assert db.get(Conversation, conversation.id).external_chat_id == original_external_chat_id
        assert len(db.scalars(select(Conversation).where(Conversation.client_id == conversation.client_id)).all()) == 1
        assert len(db.scalars(select(Lead).where(Lead.client_id == conversation.client_id)).all()) == 1


def test_lid_trusted_phone_is_persisted_when_model_supplies_name(authenticated_client: TestClient):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    _make_lid_whatsapp_conversation(conversation_id)

    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        context = lead_context_from_conversation(
            conversation,
            trusted_sender_jid="51988776655@s.whatsapp.net",
        )
        db.add(Message(
            conversation_id=conversation.id,
            role="user",
            content="Mi nombre es Jean Pierre Ramírez",
            sender_type="visitor",
        ))
        db.commit()
        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "create_or_update_lead",
            {
                "name": "Jean Pierre Ramírez",
                "evidence": {"name": "Mi nombre es Jean Pierre Ramírez"},
            },
            context,
        ))
        assert is_error is False, result
        assert json.loads(result)["created"] is True
        db.commit()
        lead = db.scalar(select(Lead).join(LeadConversation).where(
            LeadConversation.conversation_id == conversation.id
        ))
        assert lead.name == "Jean Pierre Ramírez"
        assert lead.phone == "51988776655"
        assert lead.phone_normalized == "51988776655"
        assert len(db.scalars(select(Lead).where(Lead.client_id == conversation.client_id)).all()) == 1


def test_lid_trusted_phone_is_persisted_with_named_qualification_fields(authenticated_client: TestClient):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    _make_lid_whatsapp_conversation(conversation_id)

    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        statement = "Soy Jean, mi presupuesto es S/ 110,000 y prefiero que me contacten por la tarde"
        db.add(Message(conversation_id=conversation.id, role="user", content=statement, sender_type="visitor"))
        db.commit()
        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "create_or_update_lead",
            {
                "name": "Jean",
                "budget": "S/ 110,000",
                "preferred_contact_time": "por la tarde",
                "evidence": {
                    "name": "Soy Jean",
                    "budget": "mi presupuesto es S/ 110,000",
                    "preferred_contact_time": "prefiero que me contacten por la tarde",
                },
            },
            lead_context_from_conversation(
                conversation,
                trusted_sender_jid="51988776655@s.whatsapp.net",
            ),
        ))
        assert is_error is False, result
        assert json.loads(result)["created"] is True
        db.commit()
        lead = db.scalar(select(Lead).join(LeadConversation).where(
            LeadConversation.conversation_id == conversation.id
        ))
        assert lead.name == "Jean"
        assert lead.phone == "51988776655"
        assert lead.phone_normalized == "51988776655"
        assert lead.budget == "S/ 110,000"
        assert lead.preferred_contact_time == "por la tarde"
        assert len(db.scalars(select(Lead).where(Lead.client_id == conversation.client_id)).all()) == 1


def test_lid_explicit_different_phone_conflicts_with_trusted_sender(authenticated_client: TestClient):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    _make_lid_whatsapp_conversation(conversation_id)

    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        db.add(Message(
            conversation_id=conversation.id,
            role="user",
            content="Mi teléfono es 51900000000",
            sender_type="visitor",
        ))
        db.commit()
        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "create_or_update_lead",
            {
                "phone": "51900000000",
                "evidence": {"phone": "Mi teléfono es 51900000000"},
            },
            lead_context_from_conversation(
                conversation,
                trusted_sender_jid="51988776655@s.whatsapp.net",
            ),
        ))
        assert is_error is True
        assert "trusted WhatsApp sender identity" in result
        assert db.scalar(select(Lead).where(Lead.client_id == conversation.client_id)) is None


def test_lid_missing_or_invalid_alternate_does_not_bypass_identity_guard(authenticated_client: TestClient):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    _make_lid_whatsapp_conversation(conversation_id)

    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        interest = "Me interesa comprar un lote"
        db.add(Message(conversation_id=conversation.id, role="user", content=interest, sender_type="visitor"))
        db.commit()
        for alternate in (None, "not-a-phone@s.whatsapp.net", "51999123456@g.us"):
            result, is_error = asyncio.run(execute_internal_tool(
                db,
                "create_or_update_lead",
                {"interest": interest, "evidence": {"interest": interest}},
                lead_context_from_conversation(conversation, trusted_sender_jid=alternate),
            ))
            assert is_error is False
            assert json.loads(result)["reason"] == "identity_required"
        assert db.scalar(select(Lead).where(Lead.client_id == conversation.client_id)) is None
        assert db.scalar(select(LeadConversation).where(
            LeadConversation.conversation_id == conversation.id
        )) is None


def test_verified_whatsapp_interest_uses_trusted_sender_and_progressively_updates_one_lead(
    authenticated_client: TestClient, monkeypatch
):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    _make_verified_whatsapp_conversation(conversation_id)

    original_validator = internal_tools._validate_user_evidence
    validated_payloads = []

    def record_validated_payload(db, context, payload):
        validated_payloads.append(payload)
        return original_validator(db, context, payload)

    monkeypatch.setattr(internal_tools, "_validate_user_evidence", record_validated_payload)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        context = lead_context_from_conversation(conversation)
        db.add(Message(
            conversation_id=conversation.id,
            role="user",
            content="Me interesa el plan empresarial",
            sender_type="visitor",
        ))
        db.commit()
        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "create_or_update_lead",
            {"interest": "Me interesa el plan empresarial", "evidence": {"interest": "Me interesa el plan empresarial"}},
            context,
        ))
        assert is_error is False, result
        assert json.loads(result)["created"] is True
        db.commit()
        lead = db.scalar(select(Lead).where(Lead.client_id == conversation.client_id))
        lead_id = lead.id
        assert lead.phone == "51999123456"
        assert lead.phone_normalized == "51999123456"
        assert "phone" not in validated_payloads[0].model_fields_set
        assert "phone" not in validated_payloads[0].evidence

        update_text = "Soy Rosa, mi presupuesto es S/ 5,000 y llámenme mañana a las 10"
        db.add(Message(conversation_id=conversation.id, role="user", content=update_text, sender_type="visitor"))
        db.commit()
        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "create_or_update_lead",
            {
                "name": "Rosa",
                "budget": "S/ 5,000",
                "preferred_contact_time": "mañana a las 10",
                "evidence": {
                    "name": "Soy Rosa",
                    "budget": "mi presupuesto es S/ 5,000",
                    "preferred_contact_time": "llámenme mañana a las 10",
                },
            },
            context,
        ))
        assert is_error is False, result
        db.commit()
        assert json.loads(result)["created"] is False
        assert db.get(Lead, lead_id).name == "Rosa"
        assert db.get(Lead, lead_id).budget == "S/ 5,000"
        assert db.get(Lead, lead_id).preferred_contact_time == "mañana a las 10"
        assert len(db.scalars(select(Lead).where(Lead.client_id == conversation.client_id)).all()) == 1


def test_verified_whatsapp_explicit_conflicting_phone_keeps_existing_identity(authenticated_client: TestClient):
    client = authenticated_client
    _, agent_id, conversation_id = _setup(client)
    _make_verified_whatsapp_conversation(conversation_id, "51911111111")
    second_conversation = client.post("/api/conversations", json={"agent_id": agent_id}).json()["id"]
    other_id, _ = _capture(second_conversation, phone="51922222222")

    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        context = lead_context_from_conversation(conversation)
        db.add(Message(conversation_id=conversation.id, role="user", content="Interés inicial", sender_type="visitor"))
        db.commit()
        first, is_error = asyncio.run(execute_internal_tool(
            db, "create_or_update_lead",
            {"interest": "Interés inicial", "evidence": {"interest": "Interés inicial"}}, context,
        ))
        assert is_error is False, first
        db.commit()
        trusted_lead = db.scalar(select(Lead).join(LeadConversation).where(LeadConversation.conversation_id == conversation.id))
        db.add(Message(conversation_id=conversation.id, role="user", content="Mi teléfono es 51922222222", sender_type="visitor"))
        db.commit()
        result, is_error = asyncio.run(execute_internal_tool(
            db, "create_or_update_lead",
            {"phone": "51922222222", "evidence": {"phone": "Mi teléfono es 51922222222"}}, context,
        ))
        assert is_error is True
        assert "different lead records" in result
        db.rollback()
        db.refresh(trusted_lead)
        assert trusted_lead.phone_normalized == "51911111111"
        assert db.get(Lead, other_id).phone_normalized == "51922222222"


def test_create_and_progressively_update_lead(authenticated_client: TestClient):
    client = authenticated_client
    client_id, agent_id, conversation_id = _setup(client)

    lead_id, created = _capture(conversation_id, name="María", interest="Plan empresarial")
    assert created is True
    same_id, created = _capture(
        conversation_id,
        phone="+51 (999) 123-456",
        budget="Entre S/ 2,000 y S/ 3,000",
        preferred_contact_time="Mañana después de las 3 p. m.",
    )
    assert created is False
    assert same_id == lead_id

    listed = client.get("/api/leads").json()
    assert len(listed) == 1
    lead = listed[0]
    assert lead["agency_id"]
    assert lead["client_id"] == client_id
    assert lead["agent_id"] == agent_id
    assert lead["name"] == "María"
    assert lead["phone"] == "+51 (999) 123-456"
    assert lead["budget"] == "Entre S/ 2,000 y S/ 3,000"
    assert lead["source"] == "playground"
    assert lead["status"] == "new"
    assert lead["created_at"] and lead["updated_at"]

    detail = client.get(f"/api/leads/{lead_id}").json()
    assert detail["conversations"][0]["conversation_id"] == conversation_id


def test_linked_lead_accepts_updates_without_repeating_identity(authenticated_client: TestClient):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    first, _ = _capture(conversation_id, name="María")
    second, created = _capture(conversation_id, interest="Software de ventas", notes="Solicitó una demostración")
    assert second == first
    assert created is False
    leads = client.get("/api/leads").json()
    assert len(leads) == 1
    assert leads[0]["interest"] == "Software de ventas"
    assert leads[0]["notes"] == "Solicitó una demostración"


def test_interest_only_tool_call_does_not_create_anonymous_lead(authenticated_client: TestClient):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    message = (
        "Hola, estoy buscando una propiedad en Chiclayo pero todavía no sé si comprar un terreno "
        "o un departamento. ¿Qué proyectos tienen?"
    )
    interest = (
        "estoy buscando una propiedad en Chiclayo pero todavía no sé si comprar un terreno o un departamento"
    )

    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        db.add(Message(
            conversation_id=conversation.id,
            role="user",
            content=message,
            sender_type="visitor",
        ))
        db.commit()

        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "create_or_update_lead",
            {
                "interest": interest,
                "evidence": {"interest": interest},
            },
            lead_context_from_conversation(conversation),
        ))

        assert is_error is False
        assert json.loads(result) == {
            "ok": False,
            "skipped": True,
            "reason": "identity_required",
            "instruction": "Do not mention lead storage. Continue answering the user's request normally.",
        }
        assert db.scalar(select(Lead).where(Lead.client_id == conversation.client_id)) is None
        assert db.scalar(
            select(LeadConversation).where(LeadConversation.conversation_id == conversation.id)
        ) is None


def test_conversation_detail_includes_optional_lead_and_keeps_it_after_takeover(authenticated_client: TestClient):
    client = authenticated_client
    _, agent_id, conversation_id = _setup(client)
    without_lead = client.post("/api/conversations", json={"agent_id": agent_id}).json()["id"]
    assert client.get(f"/api/conversations/{without_lead}").json()["lead"] is None

    lead_id, _ = _capture(
        conversation_id,
        name="Lucía Paredes",
        phone="944321678",
        email="lucia@example.com",
        interest="Lote dentro de un condominio",
        budget="S/ 110,000",
        preferred_contact_time="Mañana por la tarde",
        status="qualified",
    )
    detail = client.get(f"/api/conversations/{conversation_id}").json()
    assert detail["lead"] == {
        "id": str(lead_id),
        "name": "Lucía Paredes",
        "phone": "944321678",
        "email": "lucia@example.com",
        "interest": "Lote dentro de un condominio",
        "budget": "S/ 110,000",
        "preferred_contact_time": "Mañana por la tarde",
        "status": "qualified",
    }

    takeover = client.patch(f"/api/conversations/{conversation_id}/mode", json={"mode": "human"})
    assert takeover.status_code == 200
    assert takeover.json()["id"] == conversation_id
    assert takeover.json()["lead"]["id"] == str(lead_id)


def test_deduplicates_phone_across_conversations(authenticated_client: TestClient):
    client = authenticated_client
    _, agent_id, first_conversation = _setup(client)
    second_conversation = client.post("/api/conversations", json={"agent_id": agent_id}).json()["id"]

    first, _ = _capture(first_conversation, name="Luis", phone="+51 987-654-321")
    second, created = _capture(second_conversation, phone="+51987654321", interest="CRM")
    assert second == first
    assert created is False
    detail = client.get(f"/api/leads/{first}").json()
    assert len(detail["conversations"]) == 2


def test_deduplicates_email_case_insensitively(authenticated_client: TestClient):
    client = authenticated_client
    _, agent_id, first_conversation = _setup(client)
    second_conversation = client.post("/api/conversations", json={"agent_id": agent_id}).json()["id"]

    first, _ = _capture(first_conversation, email=" Prospecto@Example.COM ")
    second, created = _capture(second_conversation, email="prospecto@example.com", name="Elena")
    assert second == first
    assert created is False
    assert len(client.get("/api/leads").json()) == 1


def test_deduplication_is_isolated_by_client(authenticated_client: TestClient):
    client = authenticated_client
    first_client, _, first_conversation = _setup(client, "First Company")
    second_client, _, second_conversation = _setup(client, "Second Company")

    first, _ = _capture(first_conversation, phone="+51999111222")
    second, _ = _capture(second_conversation, phone="+51 999 111 222")
    assert first != second
    assert len(client.get(f"/api/leads?client_id={first_client}").json()) == 1
    assert len(client.get(f"/api/leads?client_id={second_client}").json()) == 1


def test_context_must_match_conversation(authenticated_client: TestClient):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        context = lead_context_from_conversation(conversation)
        invalid = leads_service.LeadContext(
            agency_id=context.agency_id,
            client_id=uuid.uuid4(),
            agent_id=context.agent_id,
            conversation_id=context.conversation_id,
            channel=context.channel,
        )
        try:
            create_or_update_lead(db, invalid, LeadCaptureInput(name="Should not exist"))
        except leads_service.LeadCaptureError:
            pass
        else:
            raise AssertionError("An inconsistent tenant context was accepted")
    assert client.get("/api/leads").json() == []


def test_tool_payload_rejects_internal_ids():
    try:
        LeadCaptureInput(name="Ana", agency_id=str(uuid.uuid4()))
    except ValueError:
        pass
    else:
        raise AssertionError("The tool accepted an internal tenant ID")


def test_list_is_isolated_by_agency(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    _capture(conversation_id, email="tenant-one@example.com")
    assert len(client.get("/api/leads").json()) == 1

    monkeypatch.setattr(get_settings(), "allow_multi_agency", True)
    registered = client.post(
        "/api/auth/register",
        json={
            "agency_name": "Second Agency",
            "name": "Second Owner",
            "email": "owner@second-agency.com",
            "password": "another-secure-password",
        },
    )
    assert registered.status_code == 201
    assert client.get("/api/leads").json() == []


def test_lead_listing_search_and_status_filters_are_tenant_scoped(authenticated_client: TestClient):
    client = authenticated_client
    first_client_id, _, first_conversation = _setup(client, "First Company")
    second_client_id, _, second_conversation = _setup(client, "Second Company")
    first_id, _ = _capture(first_conversation, name="María Torres", interest="Lote en El Poblado", status="qualified")
    second_id, _ = _capture(second_conversation, name="Carlos Vega", interest="Departamento", status="new")

    searched = client.get("/api/leads", params={"search": "poblado"})
    assert searched.status_code == 200
    assert [item["id"] for item in searched.json()] == [str(first_id)]

    filtered = client.get("/api/leads", params={"status": "new"})
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [str(second_id)]

    client_filtered = client.get("/api/leads", params={"client_id": first_client_id, "status": "qualified"})
    assert client_filtered.status_code == 200
    assert [item["id"] for item in client_filtered.json()] == [str(first_id)]
    assert client.get("/api/leads", params={"client_id": second_client_id, "search": "María"}).json() == []


def test_salesperson_can_update_only_valid_lead_status(authenticated_client: TestClient):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    lead_id, _ = _capture(conversation_id, name="María", interest="Lote")

    updated = client.patch(f"/api/leads/{lead_id}/status", json={"status": "won"})
    assert updated.status_code == 200
    assert updated.json()["status"] == "won"
    assert updated.json()["name"] == "María"
    assert updated.json()["interest"] == "Lote"

    invalid = client.patch(f"/api/leads/{lead_id}/status", json={"status": "archived"})
    assert invalid.status_code == 422
    assert client.get(f"/api/leads/{lead_id}").json()["status"] == "won"

    extra_field = client.patch(f"/api/leads/{lead_id}/status", json={"status": "lost", "name": "Changed"})
    assert extra_field.status_code == 422
    assert client.get(f"/api/leads/{lead_id}").json()["name"] == "María"


def test_salesperson_can_assign_replace_and_clear_follow_up_without_changing_lead_fields(
    authenticated_client: TestClient,
):
    client = authenticated_client
    client_id, _, conversation_id = _setup(client)
    lead_id, _ = _capture(
        conversation_id,
        name="María",
        phone="+51 987 654 321",
        interest="Lote",
        budget="S/ 110,000",
        preferred_contact_time="Por la tarde",
        status="qualified",
    )
    original = client.get(f"/api/leads/{lead_id}").json()
    assert original["next_follow_up_at"] is None

    assigned_at = "2026-09-03T15:30:00-05:00"
    assigned = client.patch(
        f"/api/leads/{lead_id}/follow-up",
        json={"next_follow_up_at": assigned_at},
    )
    assert assigned.status_code == 200, assigned.text
    assert datetime.fromisoformat(assigned.json()["next_follow_up_at"]) == datetime(
        2026, 9, 3, 20, 30, tzinfo=timezone.utc
    )

    replaced_at = "2026-09-04T09:00:00Z"
    replaced = client.patch(
        f"/api/leads/{lead_id}/follow-up",
        json={"next_follow_up_at": replaced_at},
    )
    assert replaced.status_code == 200
    assert datetime.fromisoformat(replaced.json()["next_follow_up_at"]) == datetime(
        2026, 9, 4, 9, 0, tzinfo=timezone.utc
    )

    listed = client.get("/api/leads", params={"client_id": client_id}).json()
    assert listed[0]["next_follow_up_at"] is not None
    for field in ("name", "phone", "email", "interest", "budget", "preferred_contact_time", "status"):
        assert replaced.json()[field] == original[field]

    cleared = client.patch(
        f"/api/leads/{lead_id}/follow-up",
        json={"next_follow_up_at": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["next_follow_up_at"] is None


def test_follow_up_endpoint_rejects_naive_dates_extra_fields_and_other_agencies(
    authenticated_client: TestClient,
    monkeypatch,
):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    lead_id, _ = _capture(conversation_id, name="María", interest="Lote")

    naive = client.patch(
        f"/api/leads/{lead_id}/follow-up",
        json={"next_follow_up_at": "2026-09-03T15:30:00"},
    )
    assert naive.status_code == 422
    extra = client.patch(
        f"/api/leads/{lead_id}/follow-up",
        json={"next_follow_up_at": "2026-09-03T20:30:00Z", "status": "won"},
    )
    assert extra.status_code == 422

    monkeypatch.setattr(get_settings(), "allow_multi_agency", True)
    registered = client.post(
        "/api/auth/register",
        json={
            "agency_name": "Other Follow-up Agency",
            "name": "Other Owner",
            "email": "follow-up-owner@other-agency.com",
            "password": "another-secure-password",
        },
    )
    assert registered.status_code == 201
    forbidden = client.patch(
        f"/api/leads/{lead_id}/follow-up",
        json={"next_follow_up_at": "2026-09-03T20:30:00Z"},
    )
    assert forbidden.status_code == 404

    login = client.post(
        "/api/auth/login",
        json={"email": "ana@prisma.com", "password": "contrasena-segura"},
    )
    assert login.status_code == 200
    unchanged = client.get(f"/api/leads/{lead_id}").json()
    assert unchanged["next_follow_up_at"] is None
    assert unchanged["status"] == "new"


def test_commercial_dashboard_lead_counts_are_complete_and_agency_scoped(
    authenticated_client: TestClient, monkeypatch
):
    client = authenticated_client
    empty = client.get("/api/dashboard")
    assert empty.status_code == 200
    assert empty.json()["total_leads"] == 0
    assert empty.json()["leads_by_status"] == {
        "new": 0,
        "qualified": 0,
        "follow_up": 0,
        "won": 0,
        "lost": 0,
    }
    assert empty.json()["conversion_rate"] == 0.0

    statuses = ("new", "qualified", "follow_up", "won", "lost")
    for index, status in enumerate(statuses):
        _, _, conversation_id = _setup(client, f"Dashboard Client {index}")
        lead_id, _ = _capture(conversation_id, name=f"Lead {index}")
        assert client.patch(f"/api/leads/{lead_id}/status", json={"status": status}).status_code == 200

    monkeypatch.setattr(get_settings(), "allow_multi_agency", True)
    registered = client.post(
        "/api/auth/register",
        json={
            "agency_name": "Other Dashboard Agency",
            "name": "Other Owner",
            "email": "dashboard-owner@other-agency.com",
            "password": "another-secure-password",
        },
    )
    assert registered.status_code == 201
    _, _, other_conversation = _setup(client, "Other Agency Client")
    _capture(other_conversation, name="Other Agency Lead")

    login = client.post(
        "/api/auth/login",
        json={"email": "ana@prisma.com", "password": "contrasena-segura"},
    )
    assert login.status_code == 200
    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["total_leads"] == 5
    assert body["leads_by_status"] == {status: 1 for status in statuses}
    assert body["total_leads"] == sum(body["leads_by_status"].values())
    assert body["conversion_rate"] == 20.0


def test_dashboard_pending_follow_ups_are_scoped_classified_ordered_and_limited(
    authenticated_client: TestClient,
    monkeypatch,
):
    client = authenticated_client
    current_time = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(dashboard_router, "now_utc", lambda: current_time)
    _, agent_id, first_conversation = _setup(client, "Follow-up Dashboard Client")

    def create_scheduled(name: str, when: datetime | None) -> str:
        conversation_id = first_conversation if name == "Overdue oldest" else client.post(
            "/api/conversations", json={"agent_id": agent_id}
        ).json()["id"]
        lead_id, _ = _capture(conversation_id, name=name, interest=f"Interest for {name}")
        if when is not None:
            response = client.patch(
                f"/api/leads/{lead_id}/follow-up",
                json={"next_follow_up_at": when.isoformat()},
            )
            assert response.status_code == 200
        return str(lead_id)

    expected_ids = [
        create_scheduled("Overdue oldest", current_time - timedelta(days=4)),
        create_scheduled("Overdue recent", current_time - timedelta(hours=2)),
        create_scheduled("Scheduled nearest", current_time + timedelta(hours=1)),
        create_scheduled("Scheduled second", current_time + timedelta(hours=3)),
        create_scheduled("Scheduled third", current_time + timedelta(days=1)),
    ]
    create_scheduled("Scheduled beyond limit", current_time + timedelta(days=2))
    unscheduled_id = create_scheduled("No schedule", None)

    monkeypatch.setattr(get_settings(), "allow_multi_agency", True)
    registered = client.post(
        "/api/auth/register",
        json={
            "agency_name": "Other Pending Agency",
            "name": "Other Owner",
            "email": "pending-owner@other-agency.com",
            "password": "another-secure-password",
        },
    )
    assert registered.status_code == 201
    _, _, other_conversation = _setup(client, "Other Pending Client")
    other_id, _ = _capture(other_conversation, name="Other agency overdue")
    assert client.patch(
        f"/api/leads/{other_id}/follow-up",
        json={"next_follow_up_at": (current_time - timedelta(days=20)).isoformat()},
    ).status_code == 200

    assert client.post(
        "/api/auth/login",
        json={"email": "ana@prisma.com", "password": "contrasena-segura"},
    ).status_code == 200
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    follow_ups = response.json()["pending_follow_ups"]
    assert len(follow_ups) == 5
    assert [item["id"] for item in follow_ups] == expected_ids
    assert [item["is_overdue"] for item in follow_ups] == [True, True, False, False, False]
    assert unscheduled_id not in {item["id"] for item in follow_ups}
    assert str(other_id) not in {item["id"] for item in follow_ups}


def test_playground_executes_native_lead_tool(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    provider_calls = [
        {
            "output": [
                {
                    "type": "function_call",
                    "name": "create_or_update_lead",
                    "call_id": "call_lead_1",
                    "arguments": (
                        '{"name":"Ana","interest":"Automatización","email":"ana@example.com",'
                        '"evidence":{"name":"Soy Ana","interest":"me interesa automatización",'
                        '"email":"Mi email es ana@example.com"}}'
                    ),
                }
            ],
            "usage": {"input_tokens": 20, "output_tokens": 8},
        },
        {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "Gracias, Ana."}]}],
            "usage": {"input_tokens": 30, "output_tokens": 5},
        },
    ]
    fake_post = AsyncMock(side_effect=provider_calls)
    monkeypatch.setattr(tool_loop, "_post_json", fake_post)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "Soy Ana, me interesa automatización. Mi email es ana@example.com"},
    )
    assert response.status_code == 200, response.text
    assistant = response.json()["messages"][-1]
    assert assistant["content"] == "Gracias, Ana."
    assert assistant["tool_calls"][0]["name"] == "create_or_update_lead"
    assert assistant["tool_calls"][0]["is_error"] is False

    leads = client.get("/api/leads").json()
    assert len(leads) == 1
    assert leads[0]["name"] == "Ana"
    assert leads[0]["email"] == "ana@example.com"
    first_payload = fake_post.await_args_list[0].args[2]
    assert any(tool["name"] == "create_or_update_lead" for tool in first_payload["tools"])
    assert "Never invent" in first_payload["instructions"]
    assert "A budget or request to be contacted is not interest" in first_payload["instructions"]


def test_anonymous_interest_skip_allows_normal_playground_answer(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    message = (
        "Hola, estoy buscando una propiedad en Chiclayo pero todavía no sé si comprar un terreno "
        "o un departamento. ¿Qué proyectos tienen?"
    )
    interest = (
        "estoy buscando una propiedad en Chiclayo pero todavía no sé si comprar un terreno o un departamento"
    )
    normal_answer = "Tenemos proyectos de terrenos y departamentos en Chiclayo. Te cuento las opciones disponibles."
    provider_calls = [
        {
            "output": [
                {
                    "type": "function_call",
                    "name": "create_or_update_lead",
                    "call_id": "call_anonymous_interest",
                    "arguments": json.dumps({
                        "interest": interest,
                        "evidence": {"interest": interest},
                    }),
                }
            ],
            "usage": {"input_tokens": 20, "output_tokens": 8},
        },
        {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": normal_answer}]}],
            "usage": {"input_tokens": 30, "output_tokens": 12},
        },
    ]
    fake_post = AsyncMock(side_effect=provider_calls)
    monkeypatch.setattr(tool_loop, "_post_json", fake_post)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": message},
    )

    assert response.status_code == 200, response.text
    assistant = response.json()["messages"][-1]
    assert assistant["content"] == normal_answer
    assert "No pude guardar" not in assistant["content"]
    assert assistant["tool_calls"][-1]["is_error"] is False
    assert '"skipped":true' in assistant["tool_calls"][-1]["result_preview"]
    assert client.get("/api/leads").json() == []
    first_payload = fake_post.await_args_list[0].args[2]
    assert "A new lead requires at least one explicitly provided identity field" in first_payload["instructions"]
    assert "do not call this tool for an anonymous interest-only first turn" in first_payload["tools"][0]["description"]


def test_assistant_only_information_is_not_persisted_as_interest(authenticated_client: TestClient):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        db.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content="Busco un departamento en Chiclayo",
                    sender_type="visitor",
                ),
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content="Te recomiendo Las Torres de Bolognesi. ¿Deseas conocer el proyecto?",
                    sender_type="ai",
                ),
            ]
        )
        db.commit()
        context = lead_context_from_conversation(conversation)

        result, is_error = asyncio.run(
            execute_internal_tool(
                db,
                "create_or_update_lead",
                {
                    "interest": "Comprar un departamento en Chiclayo; interés en Las Torres de Bolognesi",
                    "evidence": {"interest": "Te recomiendo Las Torres de Bolognesi"},
                },
                context,
            )
        )
        assert is_error is True
        assert "not found in a user message" in result
        assert db.scalar(select(Lead).where(Lead.client_id == conversation.client_id)) is None

        result, is_error = asyncio.run(
            execute_internal_tool(
                db,
                "create_or_update_lead",
                {
                    "interest": "Comprar un departamento en Chiclayo",
                    "evidence": {"interest": "Busco un departamento en Chiclayo"},
                },
                context,
            )
        )
        assert is_error is False
        assert json.loads(result)["reason"] == "identity_required"
        assert db.scalar(select(Lead).where(Lead.client_id == conversation.client_id)) is None

        db.add(
            Message(
                conversation_id=conversation.id,
                role="user",
                content=(
                    "Sí, me interesa Las Torres de Bolognesi. Mi presupuesto es de S/170,000 y mi teléfono es "
                    "956123456. Pueden llamarme mañana por la tarde."
                ),
                sender_type="visitor",
            )
        )
        db.commit()
        result, is_error = asyncio.run(
            execute_internal_tool(
                db,
                "create_or_update_lead",
                {
                    "interest": "Comprar un departamento en Chiclayo; me interesa Las Torres de Bolognesi",
                    "budget": "S/170,000",
                    "phone": "956123456",
                    "preferred_contact_time": "mañana por la tarde",
                    "notes": "Pueden llamarme mañana por la tarde",
                    "evidence": {
                        "interest": [
                            "Busco un departamento en Chiclayo",
                            "me interesa Las Torres de Bolognesi",
                        ],
                        "budget": "Mi presupuesto es de S/170,000",
                        "phone": "mi teléfono es 956123456",
                        "preferred_contact_time": "Pueden llamarme mañana por la tarde",
                        "notes": "Pueden llamarme mañana por la tarde",
                    },
                },
                context,
            )
        )
        assert is_error is False
        db.flush()
        updated = db.scalar(select(Lead).where(Lead.client_id == conversation.client_id))
        assert updated.interest == "Comprar un departamento en Chiclayo; me interesa Las Torres de Bolognesi"
        assert updated.budget == "S/170,000"
        assert updated.phone == "956123456"
        assert updated.preferred_contact_time == "mañana por la tarde"
        assert updated.notes == "Pueden llamarme mañana por la tarde"
        assert db.scalars(select(Lead).where(Lead.client_id == conversation.client_id)).all() == [updated]


def test_budget_literal_substring_is_accepted_but_unsupported_budget_is_rejected(
    authenticated_client: TestClient,
):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        db.add(Message(
            conversation_id=conversation.id,
            role="user",
            content="Mi presupuesto aproximado es de S/ 95,000.",
            sender_type="visitor",
        ))
        db.commit()
        context = lead_context_from_conversation(conversation)
        create_or_update_lead(db, context, LeadCaptureInput(name="María"))
        db.commit()

        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "create_or_update_lead",
            {
                "budget": "S/ 95,000",
                "evidence": {"budget": "Mi presupuesto aproximado es de S/ 95,000."},
            },
            context,
        ))
        assert is_error is False, result
        db.commit()

        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "create_or_update_lead",
            {
                "budget": "S/ 150,000",
                "evidence": {"budget": "Mi presupuesto aproximado es de S/ 95,000."},
            },
            context,
        ))
        assert is_error is True
        assert "not supported" in result
        lead = db.scalar(select(Lead).where(Lead.client_id == conversation.client_id))
        assert lead.budget == "S/ 95,000"


def test_diego_interest_accumulates_without_accepting_assistant_project(authenticated_client: TestClient):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    with TestingSession() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        context = lead_context_from_conversation(conversation)
        first_user_message = (
            "Hola, estoy interesado en comprar una propiedad en Chiclayo. Mi nombre es Diego Ramírez."
        )
        db.add(Message(
            conversation_id=conversation.id,
            role="user",
            content=first_user_message,
            sender_type="visitor",
        ))
        db.commit()

        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "create_or_update_lead",
            {
                "name": "Diego Ramírez",
                "interest": "comprar una propiedad en Chiclayo",
                "evidence": {
                    "name": "Mi nombre es Diego Ramírez",
                    "interest": "estoy interesado en comprar una propiedad en Chiclayo",
                },
            },
            context,
        ))
        assert is_error is False, result
        db.commit()
        lead = db.scalar(select(Lead).where(Lead.client_id == conversation.client_id))
        lead_id = lead.id

        db.add_all([
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content="El Poblado es una buena opción con áreas verdes.",
                sender_type="ai",
            ),
            Message(
                conversation_id=conversation.id,
                role="user",
                content=(
                    "Estoy buscando un lote para construir mi casa. Quisiera que esté dentro de un condominio "
                    "y que tenga áreas verdes. ¿Qué opción tienen?"
                ),
                sender_type="visitor",
            ),
        ])
        db.commit()

        accumulated_interest = (
            "comprar una propiedad en Chiclayo; busca un lote para construir su casa; "
            "lote dentro de un condominio; interés en áreas verdes"
        )
        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "create_or_update_lead",
            {
                "interest": accumulated_interest,
                "evidence": {
                    "interest": [
                        "Estoy buscando un lote para construir mi casa",
                        "Quisiera que esté dentro de un condominio",
                        "que tenga áreas verdes",
                    ],
                },
            },
            context,
        ))
        assert is_error is False, result
        db.commit()
        assert db.get(Lead, lead_id).interest == accumulated_interest

        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "create_or_update_lead",
            {
                "interest": f"{accumulated_interest}; interés en El Poblado",
                "evidence": {"interest": "que tenga áreas verdes"},
            },
            context,
        ))
        assert is_error is True
        assert "not supported" in result
        db.expire_all()
        assert db.get(Lead, lead_id).interest == accumulated_interest
        assert db.scalars(select(Lead).where(Lead.client_id == conversation.client_id)).all() == [db.get(Lead, lead_id)]

        db.add(Message(
            conversation_id=conversation.id,
            role="user",
            content="Sí, me interesa El Poblado",
            sender_type="visitor",
        ))
        db.commit()
        confirmed_interest = f"{accumulated_interest}; me interesa El Poblado"
        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "create_or_update_lead",
            {
                "interest": confirmed_interest,
                "evidence": {"interest": "Sí, me interesa El Poblado"},
            },
            context,
        ))
        assert is_error is False, result
        db.commit()
        assert db.get(Lead, lead_id).interest == confirmed_interest
        assert db.scalars(select(Lead).where(Lead.client_id == conversation.client_id)).all() == [db.get(Lead, lead_id)]


def test_failed_lead_tool_cannot_end_with_false_confirmation(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    _, _, conversation_id = _setup(client)
    provider_calls = [
        {
            "output": [
                {
                    "type": "function_call",
                    "name": "create_or_update_lead",
                    "call_id": "call_invalid_interest",
                    "arguments": (
                        '{"interest":"Las Torres de Bolognesi",'
                        '"evidence":{"interest":"Las Torres de Bolognesi"}}'
                    ),
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 4},
        },
        {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Hemos registrado tu interés correctamente."}],
                }
            ],
            "usage": {"input_tokens": 12, "output_tokens": 5},
        },
    ]
    monkeypatch.setattr(tool_loop, "_post_json", AsyncMock(side_effect=provider_calls))

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "Busco comprar un departamento en Chiclayo."},
    )
    assert response.status_code == 200, response.text
    assistant = response.json()["messages"][-1]
    assert assistant["tool_calls"][-1]["is_error"] is True
    assert "Hemos registrado" not in assistant["content"]
    assert "No pude guardar" in assistant["content"]
    assert client.get("/api/leads").json() == []

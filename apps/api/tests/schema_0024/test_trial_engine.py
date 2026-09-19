"""PostgreSQL tests for the Phase 1B.2 trial engine.

Run this module only with a fresh disposable database whose name ends in
``_trial_test``. The service commits by design, so each test uses a new client
and the temporary database is discarded by the test runner.
"""
import os
import uuid
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.models import (
    Agent,
    Agency,
    Client,
    ClientSubscription,
    Conversation,
    Message,
    Plan,
    WhatsAppChannel,
)
from app.models_promotions import SubscriptionCharge
from app.models_subscription_activation import SubscriptionActivationDelivery as Delivery
from app.models_subscription_activation import SubscriptionTestIdentity as Identity
from app.services.subscriptions import (
    TRIAL_DURATION,
    confirm_activation_delivery,
    ensure_trial_started,
    evaluate_trial_expiration,
    is_test_identity,
    normalize_identity,
    normalize_lid,
    normalize_phone_e164,
    prepare_activation_delivery,
    reconcile_pending_activation_evidence,
    resolve_activation_transport_failure,
    require_commercial_action,
)
from app.services import ai as ai_service
from app.services import knowledge as knowledge_service
from app.services import whatsapp_inbound as whatsapp_inbound_service
from app.services.whatsapp_inbound import InboundMessage
from unittest.mock import AsyncMock

from app.config import get_settings

NOW = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean_database():
    """Override the parent fixture; this module owns its disposable database."""
    yield


@pytest.fixture(scope="module")
def engine():
    url = os.environ.get("TRIAL_TEST_DATABASE_URL", "")
    parsed = make_url(url) if url else None
    if not parsed or parsed.get_backend_name() != "postgresql" or not parsed.database.endswith("_trial_test"):
        pytest.fail("Explicit disposable TRIAL_TEST_DATABASE_URL ending in _trial_test required")
    db_engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=2,
                              connect_args={"options": "-c timezone=UTC -c statement_timeout=10000"})
    with db_engine.connect() as connection:
        assert not connection.exec_driver_sql(
            "SELECT 1 FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r','p') LIMIT 1"
        ).first(), "Refusing to migrate a nonempty disposable database"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[2] / "migrations"))
    command.upgrade(config, "0025_activation_identity_aliases")
    yield db_engine
    db_engine.dispose()


def seed_client(engine):
    with Session(engine) as db:
        agency = Agency(name="Trial test", slug=uuid.uuid4().hex)
        db.add(agency)
        db.flush()
        client = Client(agency_id=agency.id, name="Trial client", portal_slug=uuid.uuid4().hex)
        db.add(client)
        db.flush()
        agent = Agent(agency_id=agency.id, client_id=client.id, name="Trial agent")
        db.add(agent)
        db.flush()
        channel = WhatsAppChannel(agency_id=agency.id, client_id=client.id, agent_id=agent.id)
        db.add(channel)
        db.flush()
        conversation = Conversation(
            agency_id=agency.id,
            client_id=client.id,
            agent_id=agent.id,
            channel="whatsapp",
            whatsapp_channel_id=channel.id,
            external_chat_id="51987654321@s.whatsapp.net",
        )
        db.add(conversation)
        db.flush()
        message = Message(
            conversation_id=conversation.id,
            role="assistant",
            sender_type="ai",
            content="Activation reply",
        )
        db.add(message)
        free = db.scalar(select(Plan).where(Plan.code == "free"))
        if free is None:
            free = Plan(
                code="free", name="Free", monthly_price=Decimal("0.00"), currency="PEN",
                is_active=True, available_for_new_subscriptions=False,
            )
            db.add(free)
        db.commit()
        return {"client": client.id, "channel": channel.id, "message": message.id, "conversation": conversation.id,
                "free": free.id}


def add_delivery(engine, data, *, transport_state="ACCEPTED", environment="production", test_identity_id=None,
                 accepted_at=NOW):
    values = {
        "id": uuid.uuid4(),
        "client_id": data["client"],
        "message_id": data["message"],
        "channel": "whatsapp_qr",
        "whatsapp_channel_id": data["channel"],
        "origin": "automatic_ai_reply",
        "recipient_identity": "+51987654321",
        "environment": environment,
        "test_identity_id": test_identity_id,
        "transport_state": transport_state,
        "trial_state": "PENDING",
        "source_message_timestamp": NOW,
        "created_at": NOW,
    }
    if transport_state == "ACCEPTED":
        values.update(
            external_message_id=uuid.uuid4().hex,
            confirmation_type="BAILEYS_SEND_RESULT",
            accepted_at=accepted_at,
            recorded_at=accepted_at,
        )
    with Session(engine) as db:
        db.execute(insert(Delivery).values(**values))
        db.commit()
    return values["id"]


def add_message(engine, data):
    with Session(engine) as db:
        message = Message(conversation_id=data["conversation"], role="assistant", sender_type="ai",
                          content="Second activation reply")
        db.add(message)
        db.commit()
        return message.id


def add_prepared_delivery(engine, data, *, recipient="51987654321@s.whatsapp.net"):
    with Session(engine) as db:
        delivery = prepare_activation_delivery(
            db,
            client_id=data["client"],
            message_id=data["message"],
            whatsapp_channel_id=data["channel"],
            recipient=recipient,
            source_message_timestamp=NOW,
        )
        db.add(delivery)
        db.commit()
        return delivery.id


def get_subscription(engine, client_id):
    with Session(engine) as db:
        return db.scalar(select(ClientSubscription).where(ClientSubscription.client_id == client_id))


def test_normalize_identities(engine):
    assert normalize_phone_e164(" 00 51 987 654 321 ") == "+51987654321"
    assert normalize_lid("123456789@lid") == "123456789@lid"
    assert normalize_identity("123456789@lid") == "123456789@lid"
    with pytest.raises(ValueError):
        normalize_phone_e164("51987654321")
    with pytest.raises(ValueError):
        normalize_lid("+51987654321")


def test_first_activation_creates_free_trial_for_exactly_72_hours(engine):
    data = seed_client(engine)
    delivery_id = add_delivery(engine, data, accepted_at=NOW)
    with Session(engine) as db:
        result = ensure_trial_started(db, delivery_id)
    subscription = get_subscription(engine, data["client"])
    assert result.trial_started is True
    assert subscription.plan_id == data["free"]
    assert subscription.status == "TRIAL"
    assert subscription.first_activated_at == NOW
    assert subscription.trial_started_at == NOW
    assert subscription.trial_ends_at == NOW + TRIAL_DURATION


def test_second_call_is_idempotent_and_does_not_change_dates(engine):
    data = seed_client(engine)
    delivery_id = add_delivery(engine, data, accepted_at=NOW)
    with Session(engine) as db:
        first = ensure_trial_started(db, delivery_id)
        second = ensure_trial_started(db, delivery_id)
    subscription = get_subscription(engine, data["client"])
    assert first.subscription_id == second.subscription_id == subscription.id
    assert second.trial_started is True
    assert subscription.trial_started_at == NOW
    assert subscription.trial_ends_at == NOW + TRIAL_DURATION


def test_two_concurrent_activations_create_one_subscription(engine):
    data = seed_client(engine)
    second_data = {**data, "message": add_message(engine, data)}
    deliveries = [add_delivery(engine, data, accepted_at=NOW), add_delivery(engine, second_data, accepted_at=NOW + timedelta(seconds=1))]

    def activate(delivery_id):
        with Session(engine) as db:
            return ensure_trial_started(db, delivery_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(activate, deliveries))
    subscription = get_subscription(engine, data["client"])
    assert subscription is not None
    assert sum(result.trial_started for result in results) == 1
    assert sum(result.evidence_state == "IGNORED" for result in results) == 1
    assert subscription.trial_started_at in {NOW, NOW + timedelta(seconds=1)}
    with Session(engine) as db:
        assert db.scalar(select(ClientSubscription.id).where(ClientSubscription.client_id == data["client"])) == subscription.id


def test_test_identity_does_not_activate(engine):
    data = seed_client(engine)
    with Session(engine) as db:
        identity = Identity(client_id=data["client"], identity_type="whatsapp_phone", identity_value="+51987654321",
                            label="test", is_active=True, created_at=NOW, updated_at=NOW)
        db.add(identity)
        db.commit()
        identity_id = identity.id
    delivery_id = add_delivery(engine, data, environment="test", test_identity_id=identity_id)
    with Session(engine) as db:
        result = ensure_trial_started(db, delivery_id)
        assert is_test_identity(db, data["client"], "+51987654321")
    assert result.trial_started is False
    assert result.reason == "TEST_IDENTITY"
    assert get_subscription(engine, data["client"]) is None


@pytest.mark.parametrize("transport_state", ["PREPARED", "FAILED", "UNKNOWN"])
def test_nonaccepted_evidence_does_not_activate(engine, transport_state):
    data = seed_client(engine)
    delivery_id = add_delivery(engine, data, transport_state=transport_state)
    with Session(engine) as db:
        result = ensure_trial_started(db, delivery_id)
        delivery = db.get(Delivery, delivery_id)
        assert delivery.trial_state == "PENDING"
    assert result.trial_started is False
    assert result.reason == "TRANSPORT_NOT_ACCEPTED"
    assert get_subscription(engine, data["client"]) is None


@pytest.mark.parametrize("status", ["ACTIVE", "SUSPENDED", "CANCELLED"])
def test_existing_terminal_subscription_never_gets_a_new_trial(engine, status):
    data = seed_client(engine)
    original = NOW - timedelta(days=10)
    with Session(engine) as db:
        db.add(ClientSubscription(client_id=data["client"], plan_id=data["free"], status=status,
                                  first_activated_at=original, trial_started_at=original,
                                  trial_ends_at=original + TRIAL_DURATION))
        db.commit()
    delivery_id = add_delivery(engine, data)
    with Session(engine) as db:
        result = ensure_trial_started(db, delivery_id)
    subscription = get_subscription(engine, data["client"])
    assert result.reason == "ALREADY_STARTED"
    assert result.trial_started is False
    assert subscription.status == status
    assert subscription.first_activated_at == original
    assert subscription.trial_started_at == original
    assert subscription.trial_ends_at == original + TRIAL_DURATION


def test_expiration_before_at_and_after_deadline(engine):
    data = seed_client(engine)
    with Session(engine) as db:
        db.add(ClientSubscription(client_id=data["client"], plan_id=data["free"], status="TRIAL",
                                  first_activated_at=NOW, trial_started_at=NOW,
                                  trial_ends_at=NOW + TRIAL_DURATION))
        db.commit()
        before = evaluate_trial_expiration(db, data["client"], NOW + TRIAL_DURATION - timedelta(microseconds=1))
        assert before.changed is False
        at_deadline = evaluate_trial_expiration(db, data["client"], NOW + TRIAL_DURATION)
        assert at_deadline.status_after == "SUSPENDED"
        assert at_deadline.paid_coverage is False
        after = evaluate_trial_expiration(db, data["client"], NOW + TRIAL_DURATION + timedelta(seconds=1))
        assert after.changed is False
    subscription = get_subscription(engine, data["client"])
    assert subscription.status == "SUSPENDED"
    assert subscription.trial_ends_at == NOW + TRIAL_DURATION


def test_expiration_with_current_paid_coverage_becomes_active(engine):
    data = seed_client(engine)
    end = NOW + TRIAL_DURATION
    with Session(engine) as db:
        subscription = ClientSubscription(client_id=data["client"], plan_id=data["free"], status="TRIAL",
                                           first_activated_at=NOW, trial_started_at=NOW, trial_ends_at=end)
        db.add(subscription)
        db.flush()
        db.add(SubscriptionCharge(client_id=data["client"], subscription_id=subscription.id, plan_id=data["free"],
                                  cycle_number=1, period_start=end, period_end=end + timedelta(days=31),
                                  currency="PEN", original_price=0, discount=0, total_final=0, status="PAID",
                                  issued_at=end, paid_at=end, idempotency_key=uuid.uuid4().hex))
        db.commit()
        result = evaluate_trial_expiration(db, data["client"], end)
    assert result.paid_coverage is True
    assert get_subscription(engine, data["client"]).status == "ACTIVE"


@pytest.mark.parametrize("status, allowed, reason", [
    ("ACTIVE", True, "ACTIVE"),
    ("PAYMENT_PENDING", False, "PAYMENT_PENDING_NOT_CONFIRMED"),
    ("SUSPENDED", False, "SUSPENDED"),
    ("CANCELLED", False, "CANCELLED"),
])
def test_commercial_authorization_is_centralized(engine, status, allowed, reason):
    data = seed_client(engine)
    with Session(engine) as db:
        db.add(ClientSubscription(client_id=data["client"], plan_id=data["free"], status=status))
        db.commit()
        decision = require_commercial_action(db, data["client"], now=NOW)
    assert decision.allowed is allowed
    assert decision.reason == reason


def test_company_without_subscription_remains_compatible(engine):
    data = seed_client(engine)
    with Session(engine) as db:
        decision = require_commercial_action(db, data["client"], now=NOW)
    assert decision.allowed is True
    assert decision.reason == "NO_SUBSCRIPTION"


def test_reconciliation_uses_original_acceptance_without_a_new_72_hour_gift(engine):
    data = seed_client(engine)
    accepted_at = NOW - timedelta(hours=8)
    delivery_id = add_delivery(engine, data, accepted_at=accepted_at)
    with Session(engine) as db:
        results = reconcile_pending_activation_evidence(db, client_id=data["client"])
        again = reconcile_pending_activation_evidence(db, client_id=data["client"])
    subscription = get_subscription(engine, data["client"])
    assert [result.delivery_id for result in results] == [delivery_id]
    assert again == []
    assert subscription.first_activated_at == accepted_at
    assert subscription.trial_started_at == accepted_at
    assert subscription.trial_ends_at == accepted_at + TRIAL_DURATION


def test_prepared_delivery_is_committed_before_transport_and_classified(engine):
    data = seed_client(engine)
    delivery_id = add_prepared_delivery(engine, data)
    with Session(engine) as db:
        delivery = db.get(Delivery, delivery_id)
        assert delivery.transport_state == "PREPARED"
        assert delivery.trial_state == "PENDING"
        assert delivery.recipient_identity == "+51987654321"
        assert delivery.environment == "production"
        assert db.get(Message, data["message"]) is not None


def test_qr_inbound_persists_message_and_prepared_before_simulated_socket(engine, monkeypatch):
    data = seed_client(engine)
    monkeypatch.setattr(whatsapp_inbound_service, "resolve_agent_credentials", lambda *_args: ("http://provider", "key"))
    monkeypatch.setattr(
        whatsapp_inbound_service,
        "retrieve_knowledge",
        AsyncMock(return_value=knowledge_service.KnowledgeResult(text="", sources=[])),
    )
    monkeypatch.setattr(
        whatsapp_inbound_service,
        "run_completion",
        AsyncMock(return_value=ai_service.Completion(text="Activation answer")),
    )
    monkeypatch.setattr(whatsapp_inbound_service, "record_usage", lambda *_args: None)
    with Session(engine) as db:
        channel = db.get(WhatsAppChannel, data["channel"])
        channel.agent.model = "trial-test-model"
        db.commit()
        result = asyncio.run(whatsapp_inbound_service.process_inbound(
            db,
            channel,
            InboundMessage(
                external_message_id="visitor-1",
                external_chat_id="51987654321@s.whatsapp.net",
                text="Start trial",
                message_timestamp=NOW,
            ),
            conversation_channel="whatsapp",
            channel_fk_field="whatsapp_channel_id",
            activation_channel="whatsapp_qr",
        ))
        delivery = db.get(Delivery, result.delivery_id)
        assert result.outbound_message_id is not None
        assert result.delivery_id is not None
        assert delivery.transport_state == "PREPARED"
        assert delivery.message_id == result.outbound_message_id
        assert delivery.trial_state == "PENDING"
        # A real socket call happens only after process_inbound has committed.
        assert db.get(Message, result.outbound_message_id).external_message_id is None


def _stub_inbound_ai(monkeypatch):
    monkeypatch.setattr(whatsapp_inbound_service, "resolve_agent_credentials", lambda *_args: ("http://provider", "key"))
    monkeypatch.setattr(
        whatsapp_inbound_service,
        "retrieve_knowledge",
        AsyncMock(return_value=knowledge_service.KnowledgeResult(text="", sources=[])),
    )
    completion = AsyncMock(return_value=ai_service.Completion(text="Activation answer"))
    monkeypatch.setattr(whatsapp_inbound_service, "run_completion", completion)
    monkeypatch.setattr(whatsapp_inbound_service, "record_usage", lambda *_args: None)
    return completion


def test_historical_append_is_stored_without_ai_or_activation_evidence(engine, monkeypatch):
    data = seed_client(engine)
    completion = _stub_inbound_ai(monkeypatch)
    with Session(engine) as db:
        channel = db.get(WhatsAppChannel, data["channel"])
        channel.agent.model = "trial-test-model"
        db.commit()
        result = asyncio.run(whatsapp_inbound_service.process_inbound(
            db,
            channel,
            InboundMessage(
                external_message_id="history-1",
                external_chat_id="123456789@lid",
                trusted_sender_jid="51987654321@s.whatsapp.net",
                text="Mensaje histórico",
                message_timestamp=NOW,
                is_historical=True,
            ),
            conversation_channel="whatsapp",
            channel_fk_field="whatsapp_channel_id",
            activation_channel="whatsapp_qr",
        ))
        assert result.reply is None
        assert result.outbound_message_id is None
        assert result.delivery_id is None
        assert db.scalar(select(Delivery.id).where(Delivery.client_id == data["client"])) is None
    completion.assert_not_awaited()
    assert get_subscription(engine, data["client"]) is None


def test_message_before_cutoff_is_not_automated_or_eligible_for_trial(engine, monkeypatch):
    data = seed_client(engine)
    monkeypatch.setattr(get_settings(), "trial_activation_eligible_since", "2026-09-12T00:00:00+00:00")
    completion = _stub_inbound_ai(monkeypatch)
    with Session(engine) as db:
        channel = db.get(WhatsAppChannel, data["channel"])
        channel.agent.model = "trial-test-model"
        db.commit()
        result = asyncio.run(whatsapp_inbound_service.process_inbound(
            db,
            channel,
            InboundMessage(
                external_message_id="before-cutoff",
                external_chat_id="51987654321@s.whatsapp.net",
                text="Antes del cutoff",
                message_timestamp=NOW,
            ),
            conversation_channel="whatsapp",
            channel_fk_field="whatsapp_channel_id",
            activation_channel="whatsapp_qr",
        ))
        assert result.mode == "ineligible"
        assert result.reply is None
        assert result.delivery_id is None
    completion.assert_not_awaited()
    assert get_subscription(engine, data["client"]) is None


def test_invalid_cutoff_fails_closed_without_ai_or_trial(engine, monkeypatch):
    data = seed_client(engine)
    monkeypatch.setattr(get_settings(), "trial_activation_eligible_since", "not-a-utc-timestamp")
    completion = _stub_inbound_ai(monkeypatch)
    with Session(engine) as db:
        channel = db.get(WhatsAppChannel, data["channel"])
        channel.agent.model = "trial-test-model"
        db.commit()
        result = asyncio.run(whatsapp_inbound_service.process_inbound(
            db,
            channel,
            InboundMessage(
                external_message_id="invalid-cutoff",
                external_chat_id="51987654321@s.whatsapp.net",
                text="Cutoff inválido",
                message_timestamp=NOW + timedelta(days=1),
            ),
            conversation_channel="whatsapp",
            channel_fk_field="whatsapp_channel_id",
            activation_channel="whatsapp_qr",
        ))
        assert result.mode == "ineligible"
        assert result.reply is None
        assert result.delivery_id is None
    completion.assert_not_awaited()
    assert get_subscription(engine, data["client"]) is None


def test_lid_with_trusted_phone_alternate_is_test_identity(engine, monkeypatch):
    data = seed_client(engine)
    _stub_inbound_ai(monkeypatch)
    with Session(engine) as db:
        identity = Identity(
            client_id=data["client"], identity_type="whatsapp_phone",
            identity_value="+51987654321", label="phone test", is_active=True,
            created_at=NOW, updated_at=NOW,
        )
        db.add(identity)
        db.commit()
        identity_id = identity.id
        channel = db.get(WhatsAppChannel, data["channel"])
        channel.agent.model = "trial-test-model"
        db.commit()
        result = asyncio.run(whatsapp_inbound_service.process_inbound(
            db,
            channel,
            InboundMessage(
                external_message_id="lid-with-phone",
                external_chat_id="123456789@lid",
                trusted_sender_jid="51987654321@s.whatsapp.net",
                text="Identidad enlazada",
                message_timestamp=NOW,
            ),
            conversation_channel="whatsapp",
            channel_fk_field="whatsapp_channel_id",
            activation_channel="whatsapp_qr",
        ))
        delivery = db.get(Delivery, result.delivery_id)
        assert delivery.environment == "test"
        assert delivery.test_identity_id == identity_id
        assert delivery.recipient_identity == "123456789@lid"
        assert confirm_activation_delivery(
            db, delivery_id=delivery.id, message_id=result.outbound_message_id,
            whatsapp_channel_id=data["channel"], external_message_id="sent-test",
            accepted_at=NOW,
        ) is True
        activation = ensure_trial_started(db, delivery.id)
        assert activation.reason == "TEST_IDENTITY"
    assert get_subscription(engine, data["client"]) is None


def test_lid_identity_is_classified_as_test_without_phone_conversion(engine, monkeypatch):
    data = seed_client(engine)
    _stub_inbound_ai(monkeypatch)
    with Session(engine) as db:
        identity = Identity(
            client_id=data["client"], identity_type="whatsapp_lid",
            identity_value="123456789@lid", label="lid test", is_active=True,
            created_at=NOW, updated_at=NOW,
        )
        db.add(identity)
        db.commit()
        channel = db.get(WhatsAppChannel, data["channel"])
        channel.agent.model = "trial-test-model"
        db.commit()
        result = asyncio.run(whatsapp_inbound_service.process_inbound(
            db, channel,
            InboundMessage(
                external_message_id="lid-direct",
                external_chat_id="123456789@lid",
                text="Identidad LID",
                message_timestamp=NOW,
            ),
            conversation_channel="whatsapp",
            channel_fk_field="whatsapp_channel_id",
            activation_channel="whatsapp_qr",
        ))
        delivery = db.get(Delivery, result.delivery_id)
        assert delivery.environment == "test"
        assert delivery.test_identity_id == identity.id
        assert delivery.recipient_identity == "123456789@lid"


def test_lid_without_trusted_phone_does_not_invent_phone_identity(engine, monkeypatch):
    data = seed_client(engine)
    _stub_inbound_ai(monkeypatch)
    with Session(engine) as db:
        identity = Identity(
            client_id=data["client"], identity_type="whatsapp_phone",
            identity_value="+51987654321", label="phone test", is_active=True,
            created_at=NOW, updated_at=NOW,
        )
        db.add(identity)
        db.commit()
        channel = db.get(WhatsAppChannel, data["channel"])
        channel.agent.model = "trial-test-model"
        db.commit()
        result = asyncio.run(whatsapp_inbound_service.process_inbound(
            db, channel,
            InboundMessage(
                external_message_id="lid-without-link",
                external_chat_id="123456789@lid",
                text="LID sin relación",
                message_timestamp=NOW,
            ),
            conversation_channel="whatsapp",
            channel_fk_field="whatsapp_channel_id",
            activation_channel="whatsapp_qr",
        ))
        delivery = db.get(Delivery, result.delivery_id)
        assert delivery.environment == "production"
        assert delivery.test_identity_id is None


def test_accepted_delivery_before_cutoff_remains_pending_and_does_not_start_trial(engine, monkeypatch):
    data = seed_client(engine)
    delivery_id = add_prepared_delivery(engine, data)
    accepted_at = NOW + timedelta(minutes=1)
    with Session(engine) as db:
        assert confirm_activation_delivery(
            db, delivery_id=delivery_id, message_id=data["message"],
            whatsapp_channel_id=data["channel"], external_message_id="before-cutoff-send",
            accepted_at=accepted_at,
        ) is True
        monkeypatch.setattr(get_settings(), "trial_activation_eligible_since", "2026-09-12T00:00:00+00:00")
        result = ensure_trial_started(db, delivery_id)
        delivery = db.get(Delivery, delivery_id)
        assert result.reason == "INELIGIBLE_TIMESTAMP"
        assert delivery.trial_state == "PENDING"
    assert get_subscription(engine, data["client"]) is None


def test_active_test_identity_is_preserved_without_trial(engine):
    data = seed_client(engine)
    with Session(engine) as db:
        identity = Identity(
            client_id=data["client"], identity_type="whatsapp_phone",
            identity_value="+51987654321", label="test", is_active=True,
            created_at=NOW, updated_at=NOW,
        )
        db.add(identity)
        db.commit()
    delivery_id = add_prepared_delivery(engine, data)
    with Session(engine) as db:
        delivery = db.get(Delivery, delivery_id)
        assert delivery.environment == "test"
        assert delivery.test_identity_id is not None
        accepted_at = NOW + timedelta(minutes=1)
        assert confirm_activation_delivery(
            db, delivery_id=delivery_id, message_id=data["message"],
            whatsapp_channel_id=data["channel"], external_message_id="baileys-test-1",
            accepted_at=accepted_at,
        ) is True
        result = ensure_trial_started(db, delivery_id)
    assert result.reason == "TEST_IDENTITY"
    assert get_subscription(engine, data["client"]) is None


def test_success_callback_records_bridge_and_api_times_and_starts_one_trial(engine):
    data = seed_client(engine)
    delivery_id = add_prepared_delivery(engine, data)
    accepted_at = NOW + timedelta(minutes=2)
    with Session(engine) as db:
        assert confirm_activation_delivery(
            db, delivery_id=delivery_id, message_id=data["message"],
            whatsapp_channel_id=data["channel"], external_message_id="baileys-1",
            accepted_at=accepted_at,
        ) is True
        delivery = db.get(Delivery, delivery_id)
        assert delivery.accepted_at == accepted_at
        assert delivery.recorded_at is not None
        assert delivery.recorded_at.tzinfo is not None
        result = ensure_trial_started(db, delivery_id)
    assert result.trial_started is True
    subscription = get_subscription(engine, data["client"])
    assert subscription.first_activated_at == accepted_at
    assert subscription.trial_ends_at == accepted_at + TRIAL_DURATION


def test_identical_callback_is_idempotent_and_conflicting_id_is_rejected(engine):
    data = seed_client(engine)
    delivery_id = add_prepared_delivery(engine, data)
    accepted_at = NOW + timedelta(minutes=3)
    with Session(engine) as db:
        assert confirm_activation_delivery(
            db, delivery_id=delivery_id, message_id=data["message"],
            whatsapp_channel_id=data["channel"], external_message_id="baileys-2",
            accepted_at=accepted_at,
        ) is True
        first = db.get(Delivery, delivery_id)
        first_recorded_at = first.recorded_at
        assert confirm_activation_delivery(
            db, delivery_id=delivery_id, message_id=data["message"],
            whatsapp_channel_id=data["channel"], external_message_id="baileys-2",
            accepted_at=accepted_at + timedelta(minutes=5),
        ) is False
        repeated = db.get(Delivery, delivery_id)
        assert repeated.accepted_at == accepted_at
        assert repeated.recorded_at == first_recorded_at
        with pytest.raises(ValueError, match="Conflicting ACCEPTED"):
            confirm_activation_delivery(
                db, delivery_id=delivery_id, message_id=data["message"],
                whatsapp_channel_id=data["channel"], external_message_id="baileys-other",
                accepted_at=accepted_at,
            )
    with Session(engine) as db:
        delivery = db.get(Delivery, delivery_id)
        assert delivery.external_message_id == "baileys-2"


@pytest.mark.parametrize("state", ["FAILED", "UNKNOWN"])
def test_transport_failure_never_starts_trial(engine, state):
    data = seed_client(engine)
    delivery_id = add_prepared_delivery(engine, data)
    with Session(engine) as db:
        assert resolve_activation_transport_failure(
            db, delivery_id=delivery_id, message_id=data["message"],
            whatsapp_channel_id=data["channel"], state=state,
        ) is True
        result = ensure_trial_started(db, delivery_id)
        delivery = db.get(Delivery, delivery_id)
    assert result.trial_started is False
    assert get_subscription(engine, data["client"]) is None
    assert delivery.transport_state == state
    if state == "FAILED":
        assert delivery.trial_state == "IGNORED"
        assert delivery.trial_reason == "TRANSPORT_FAILED"
    else:
        assert delivery.trial_state == "PENDING"

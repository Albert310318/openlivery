"""Real PostgreSQL DDL tests; requires an explicitly isolated *_schema_test DB.

Run only against a disposable database. Overrides the destructive parent
clean_database fixture: never uses Base.metadata.drop_all/create_all.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, create_engine, inspect, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models import Agency, Agent, Client, Conversation, Message, Plan, ClientSubscription, WhatsAppChannel, WhatsAppCloudChannel
from app.models_promotions import SubscriptionCharge
from app.models_subscription_activation import SubscriptionActivationDelivery as Delivery, SubscriptionTestIdentity as Identity

NOW = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
REVISION = "0025_activation_identity_aliases"


@pytest.fixture(autouse=True)
def clean_database():
    """Disable the inherited fixture that operates on TEST_DATABASE_URL."""
    yield


def seed(connection):
    with Session(connection) as session:
        agency = Agency(name="Schema test", slug=uuid.uuid4().hex)
        session.add(agency)
        session.flush()
        clients = [Client(agency_id=agency.id, name=f"Test {i}", portal_slug=uuid.uuid4().hex) for i in range(2)]
        session.add_all(clients)
        session.flush()
        agent = Agent(agency_id=agency.id, client_id=clients[0].id, name="Schema AI")
        session.add(agent)
        session.flush()
        qr = WhatsAppChannel(agency_id=agency.id, client_id=clients[0].id, agent_id=agent.id)
        cloud = WhatsAppCloudChannel(agency_id=agency.id, client_id=clients[0].id, agent_id=agent.id)
        session.add_all([qr, cloud])
        session.flush()
        conversations = [Conversation(agency_id=agency.id, client_id=clients[0].id, agent_id=agent.id,
            channel=channel, external_chat_id=destination, **{fk: identifier})
            for channel, destination, fk, identifier in [
                ("whatsapp", "51987654321@s.whatsapp.net", "whatsapp_channel_id", qr.id),
                ("whatsapp_cloud", "51987654321", "whatsapp_cloud_channel_id", cloud.id),
            ]]
        session.add_all(conversations)
        session.flush()
        return dict(client=clients[0].id, other=clients[1].id, qr=qr.id, cloud=cloud.id,
                    qr_conversation=conversations[0].id, cloud_conversation=conversations[1].id)


def message(connection, data, channel="qr", sender="ai"):
    return connection.scalar(insert(Message).values(conversation_id=data[f"{channel}_conversation"],
        role="assistant", sender_type=sender, content="Test reply").returning(Message.id))


def prepared(connection, data, transport="qr", **changes):
    channel = transport
    values = dict(id=uuid.uuid4(), client_id=data["client"], message_id=message(connection, data, channel),
        channel="whatsapp_qr" if channel == "qr" else "whatsapp_cloud",
        whatsapp_channel_id=data["qr"] if channel == "qr" else None,
        whatsapp_cloud_channel_id=data["cloud"] if channel == "cloud" else None,
        origin="automatic_ai_reply", recipient_identity="+51987654321", environment="production",
        transport_state="PREPARED", trial_state="PENDING", created_at=NOW)
    values.update(changes)
    return values


def accepted(channel="qr", **changes):
    values = dict(transport_state="ACCEPTED", external_message_id=uuid.uuid4().hex,
        confirmation_type="BAILEYS_SEND_RESULT" if channel == "qr" else "META_MESSAGES_ID",
        accepted_at=NOW, recorded_at=NOW)
    values.update(changes)
    return values


def add(connection, values):
    connection.execute(insert(Delivery).values(**values))
    return values["id"]


def reject(connection, statement, match=None):
    with pytest.raises(DBAPIError, match=match) as caught:
        with connection.begin_nested():
            connection.execute(statement)
    assert caught.value.orig.sqlstate in {"23502", "23503", "23505", "23514", "P0001"}


def snapshot(connection, tables):
    return {table: connection.execute(text(f'SELECT to_jsonb(t)::text FROM "{table}" t ORDER BY to_jsonb(t)::text')).scalars().all()
            for table in tables if table != "alembic_version"}


@pytest.fixture(scope="module")
def pg():
    url = os.environ.get("TEST_DATABASE_URL", "")
    parsed = make_url(url) if url else None
    if not parsed or parsed.get_backend_name() != "postgresql" or not parsed.database.endswith("_schema_test"):
        pytest.fail("Explicit disposable TEST_DATABASE_URL ending in _schema_test required")
    engine = create_engine(url, connect_args={"options": "-c timezone=UTC -c statement_timeout=10000"})
    with engine.connect() as c:
        assert not inspect(c).get_table_names(), "Refusing to migrate a nonempty database"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[2] / "migrations"))
    command.upgrade(config, "0023_subscription_promotions")
    with engine.begin() as c:
        seed(c)
        previous_tables = inspect(c).get_table_names()
        previous = snapshot(c, previous_tables)
        old_constraints = c.execute(text("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY conname, oid")).all()
    command.upgrade(config, REVISION)
    with engine.connect() as c:
        assert set(inspect(c).get_table_names()) - set(previous_tables) == {
            "subscription_activation_deliveries", "subscription_test_identities"}
        assert snapshot(c, previous_tables) == previous
        assert c.scalar(text("SELECT count(*) FROM client_subscriptions")) == 0
        assert c.scalar(text("SELECT count(*) FROM subscription_activation_deliveries")) == 0
        assert c.scalar(text("SELECT count(*) FROM subscription_test_identities")) == 0
        new_constraints = c.execute(text("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY conname, oid")).all()
        assert all(row in new_constraints for row in old_constraints)
    yield engine, config
    engine.dispose()


@pytest.fixture
def db(pg):
    with pg[0].connect() as connection:
        transaction = connection.begin()
        data = seed(connection)
        yield connection, data
        transaction.rollback()


def test_clean_upgrade_and_metadata(pg):
    with pg[0].connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == REVISION
        for cls in (Delivery, Identity):
            columns = {col["name"]: col for col in inspect(c).get_columns(cls.__tablename__)}
            assert set(columns) == set(cls.__table__.columns.keys())
            for col in cls.__table__.columns:
                assert columns[col.name]["nullable"] == col.nullable
                if col.name.endswith("_at"):
                    assert columns[col.name]["type"].timezone
            assert {item["name"] for item in inspect(c).get_check_constraints(cls.__tablename__)} == {item.name for item in cls.__table__.constraints if isinstance(item, CheckConstraint)}
            assert {i["name"] for i in inspect(c).get_indexes(cls.__tablename__) if not i.get("duplicates_constraint")} == {i.name for i in cls.__table__.indexes}
        assert columns["identity_value"]["nullable"] is False
        assert next(col for col in inspect(c).get_columns(Delivery.__tablename__) if col["name"] == "environment")["default"] is None


@pytest.mark.parametrize("channel", ["qr", "cloud"])
@pytest.mark.parametrize("state", ["PREPARED", "FAILED", "UNKNOWN", "ACCEPTED"])
def test_valid_transport_states(db, channel, state):
    c, data = db
    values = prepared(c, data, channel, transport_state=state)
    if state == "ACCEPTED":
        values.update(accepted(channel))
    add(c, values)
    assert c.scalar(select(Delivery.transport_state).where(Delivery.id == values["id"])) == state


@pytest.mark.parametrize("changes", [
    {"client_id": uuid.uuid4()}, {"message_id": uuid.uuid4()},
    {"whatsapp_channel_id": uuid.uuid4()}, {"whatsapp_channel_id": None},
    {"whatsapp_cloud_channel_id": uuid.uuid4()}, {"channel": "widget"},
    {"origin": "human"}, {"origin": "advisor_notification"},
    {"environment": None}, {"environment": "unknown"},
    {"recipient_identity": " "}, {"recipient_identity": "51987654321"},
    {"recipient_identity": "+51987654322"}, {"transport_state": "SENT"},
    {"transport_state": "ACCEPTED"}, {"external_message_id": "without acceptance"},
    {"trial_state": "INVALID"}, {"trial_state": "STARTED"},
    {"trial_state": "IGNORED", "trial_reason": "invented", "processed_at": NOW},
    {"test_identity_id": uuid.uuid4(), "environment": "test"},
])
def test_reject_invalid_evidence(db, changes):
    c, data = db
    values = prepared(c, data, **changes)
    reject(c, insert(Delivery).values(**values))


@pytest.mark.parametrize("case", ["other_client", "human", "visitor", "wrong_channel", "wrong_owner"])
def test_cross_row_coherence(db, case):
    c, data = db
    values = prepared(c, data)
    if case == "other_client":
        values["client_id"] = data["other"]
    elif case in ("human", "visitor"):
        values["message_id"] = message(c, data, sender=case)
    elif case == "wrong_channel":
        values["message_id"] = message(c, data, "cloud")
    else:
        c.execute(update(WhatsAppChannel).where(WhatsAppChannel.id == data["qr"]).values(client_id=data["other"]))
    reject(c, insert(Delivery).values(**values))


def test_message_and_external_id_uniqueness(db):
    c, data = db
    first = prepared(c, data, **accepted())
    add(c, first)
    reject(c, insert(Delivery).values(**{**first, "id": uuid.uuid4()}), "uq_sad_message")
    second = prepared(c, data, **accepted(external_message_id=first["external_message_id"]))
    reject(c, insert(Delivery).values(**second), "uq_sad_qr_external")
    # Same opaque ID on a different transport is not a collision.
    add(c, prepared(c, data, "cloud", **accepted("cloud", external_message_id=first["external_message_id"])))


def test_acceptance_idempotency_and_clock_skew(db):
    c, data = db
    identifier = add(c, prepared(c, data))
    confirmation = accepted(accepted_at=NOW - timedelta(minutes=2), recorded_at=NOW - timedelta(minutes=3))
    statement = update(Delivery).where(Delivery.id == identifier).values(**confirmation)
    c.execute(statement)
    before = c.execute(select(Delivery.__table__).where(Delivery.id == identifier)).one()
    c.execute(statement)
    assert c.execute(select(Delivery.__table__).where(Delivery.id == identifier)).one() == before
    assert c.scalar(select(Delivery.accepted_at).where(Delivery.id == identifier)) == confirmation["accepted_at"]


@pytest.mark.parametrize("changes", [
    {"external_message_id": "different"}, {"accepted_at": NOW + timedelta(seconds=1)},
    {"recorded_at": NOW + timedelta(seconds=1)}, {"confirmation_type": "META_MESSAGES_ID"},
    {"transport_state": "UNKNOWN"}, {"environment": "test"},
    {"recipient_identity": "+51987654322"}, {"created_at": NOW + timedelta(seconds=1)},
    {"id": uuid.uuid4()},
])
def test_accepted_evidence_immutable(db, changes):
    c, data = db
    identifier = add(c, prepared(c, data, **accepted()))
    reject(c, update(Delivery).where(Delivery.id == identifier).values(**changes), "immutable")


@pytest.mark.parametrize("changes", [
    {"external_message_id": " "}, {"external_message_id": "\n"}, {"external_message_id": "id\tvalue"}, {"external_message_id": " id "}, {"external_message_id": None},
    {"confirmation_type": None}, {"confirmation_type": "META_MESSAGES_ID"},
    {"accepted_at": None}, {"recorded_at": None},
])
def test_accepted_requires_complete_matching_confirmation(db, changes):
    c, data = db
    reject(c, insert(Delivery).values(**prepared(c, data, **accepted(**changes))))


def identity(c, data, **changes):
    values = dict(id=uuid.uuid4(), client_id=data["client"], identity_type="whatsapp_phone",
        identity_value="+51987654321", label="Internal test", is_active=True, created_at=NOW, updated_at=NOW)
    values.update(changes)
    c.execute(insert(Identity).values(**values))
    return values["id"]


def test_test_identity_classification_is_frozen(db):
    c, data = db
    test_id = identity(c, data)
    reject(c, insert(Delivery).values(**prepared(c, data)), "test classification")
    reject(c, insert(Delivery).values(**prepared(c, data, environment="test")), "test classification")
    identifier = add(c, prepared(c, data, environment="test", test_identity_id=test_id, **accepted()))
    c.execute(update(Identity).where(Identity.id == test_id).values(is_active=False, label="Disabled"))
    c.execute(update(Delivery).where(Delivery.id == identifier).values(trial_state="IGNORED", trial_reason="TEST_IDENTITY", processed_at=NOW))
    reject(c, update(Delivery).where(Delivery.id == identifier).values(environment="production", test_identity_id=None), "immutable")
    for changes in ({"identity_value": "+51987654322"}, {"client_id": data["other"]}, {"identity_type": "whatsapp_lid"}):
        reject(c, update(Identity).where(Identity.id == test_id).values(**changes), "immutable")


@pytest.mark.parametrize("value", ["51987654321", "+051987654321", "+51 987654321", "+1234567890123456", "", "123@lid"])
def test_phone_canonical(db, value):
    c, data = db
    with pytest.raises(IntegrityError):
        with c.begin_nested():
            identity(c, data, identity_value=value)


def test_lid_is_not_converted_to_phone(db):
    c, data = db
    lid = "123456789@lid"
    c.execute(update(Conversation).where(Conversation.id == data["qr_conversation"]).values(external_chat_id=lid))
    test_id = identity(c, data, identity_type="whatsapp_lid", identity_value=lid)
    add(c, prepared(c, data, recipient_identity=lid, environment="test", test_identity_id=test_id))
    with pytest.raises(IntegrityError):
        with c.begin_nested():
            identity(c, data, identity_type="whatsapp_lid", identity_value="+51987654321")


def test_identity_unique_ownership_and_active_reference(db):
    c, data = db
    own = identity(c, data)
    with pytest.raises(IntegrityError):
        with c.begin_nested():
            identity(c, data)
    other = identity(c, data, client_id=data["other"])
    reject(c, insert(Delivery).values(**prepared(c, data, environment="test", test_identity_id=other)))
    c.execute(update(Identity).where(Identity.id == own).values(is_active=False))
    reject(c, insert(Delivery).values(**prepared(c, data, environment="test", test_identity_id=own)))
    add(c, prepared(c, data))


def test_one_started_evidence_and_terminal_result(db):
    c, data = db
    start = dict(trial_state="STARTED", trial_reason="FIRST_ACCEPTED_REPLY", processed_at=NOW)
    identifier = add(c, prepared(c, data, **accepted(), **start))
    reject(c, insert(Delivery).values(**prepared(c, data, "cloud", **accepted("cloud"), **start)), "uq_sad_started_client")
    reject(c, update(Delivery).where(Delivery.id == identifier).values(trial_state="PENDING", trial_reason=None, processed_at=None), "immutable")
    # Evidence never creates subscriptions by itself.
    assert c.scalar(select(text("count(*)")).select_from(ClientSubscription)) == 0


@pytest.mark.parametrize("changes", [
    {"environment": "test"}, {"processed_at": None}, {"trial_reason": None},
    {"trial_reason": "ALREADY_STARTED"}, {"origin": "human"},
    {"transport_state": "PREPARED", "external_message_id": None, "confirmation_type": None, "accepted_at": None, "recorded_at": None},
])
def test_started_requires_eligible_confirmation(db, changes):
    c, data = db
    values = prepared(c, data, **accepted(), trial_state="STARTED", trial_reason="FIRST_ACCEPTED_REPLY", processed_at=NOW)
    values.update(changes)
    reject(c, insert(Delivery).values(**values))


@pytest.mark.parametrize("target", ["client", "message", "qr", "cloud", "identity", "evidence", "truncate"])
def test_history_restrict(db, target):
    c, data = db
    test_id = identity(c, data)
    row = prepared(c, data, environment="test", test_identity_id=test_id, **accepted())
    add(c, row)
    add(c, prepared(c, data, "cloud", environment="test", test_identity_id=test_id, **accepted("cloud")))
    tables = {"client": (Client, data["client"]), "message": (Message, row["message_id"]),
        "qr": (WhatsAppChannel, data["qr"]), "cloud": (WhatsAppCloudChannel, data["cloud"]),
        "identity": (Identity, test_id), "evidence": (Delivery, row["id"])}
    statement = text("TRUNCATE subscription_activation_deliveries") if target == "truncate" else tables[target][0].__table__.delete().where(tables[target][0].id == tables[target][1])
    reject(c, statement)


def test_existing_0022_and_0023_constraints(db):
    c, data = db
    plan = c.scalar(insert(Plan).values(code=uuid.uuid4().hex, name="Fixture", monthly_price=39, currency="PEN").returning(Plan.id))
    sub = c.scalar(insert(ClientSubscription).values(client_id=data["client"], plan_id=plan, status="TRIAL",
        first_activated_at=NOW, trial_started_at=NOW, trial_ends_at=NOW + timedelta(hours=72)).returning(ClientSubscription.id))
    reject(c, update(ClientSubscription).where(ClientSubscription.id == sub).values(first_activated_at=NOW + timedelta(seconds=1)), "immutable")
    reject(c, update(ClientSubscription).where(ClientSubscription.id == sub).values(trial_ends_at=NOW + timedelta(hours=73)), "72h")
    reject(c, update(ClientSubscription).where(ClientSubscription.id == sub).values(status="EXPIRED"))
    charge = dict(client_id=data["client"], subscription_id=sub, plan_id=plan, cycle_number=1,
        period_start=NOW + timedelta(hours=72), period_end=NOW + timedelta(days=33), currency="PEN",
        original_price=39, discount=0, total_final=39, status="ISSUED", issued_at=NOW,
        idempotency_key=uuid.uuid4().hex)
    reject(c, insert(SubscriptionCharge).values(**{**charge, "period_start": NOW}), "paid cycles cannot include trial")
    charge_id = c.scalar(insert(SubscriptionCharge).values(**charge).returning(SubscriptionCharge.id))
    reject(c, update(SubscriptionCharge).where(SubscriptionCharge.id == charge_id).values(status="PAID"))
    c.execute(update(SubscriptionCharge).where(SubscriptionCharge.id == charge_id).values(status="PAID", paid_at=NOW))
    reject(c, update(SubscriptionCharge).where(SubscriptionCharge.id == charge_id).values(total_final=0))


def test_timezone_aware_models():
    with pytest.raises(ValueError, match="timezone-aware"):
        Delivery(accepted_at=datetime(2026, 9, 11))
    offset = timezone(timedelta(hours=-5))
    assert Delivery(accepted_at=NOW.astimezone(offset)).accepted_at.tzinfo == timezone.utc
    with pytest.raises(ValueError, match="timezone-aware"):
        Identity(created_at=datetime(2026, 9, 11))


def test_concurrent_callbacks_and_started_uniqueness(pg):
    engine = pg[0]
    with engine.begin() as c:
        data = seed(c)
        first = add(c, prepared(c, data))
        second = add(c, prepared(c, data, "cloud"))
    confirmation = accepted()

    def confirm():
        with engine.begin() as c:
            c.execute(update(Delivery).where(Delivery.id == first).values(**confirmation))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: confirm(), range(2)))
    with engine.begin() as c:
        c.execute(update(Delivery).where(Delivery.id == second).values(**accepted("cloud")))

    def start(identifier):
        try:
            with engine.begin() as c:
                c.execute(update(Delivery).where(Delivery.id == identifier).values(
                    trial_state="STARTED", trial_reason="FIRST_ACCEPTED_REPLY", processed_at=NOW))
            return True
        except IntegrityError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(start, (first, second))) == [False, True]


def test_downgrade_refuses_history(pg):
    engine, config = pg
    with engine.begin() as c:
        data = seed(c)
        identity(c, data)
    with pytest.raises(RuntimeError, match="Cannot downgrade 0025"):
        command.downgrade(config, "0023_subscription_promotions")
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == REVISION


def test_prepared_classification_is_immutable(db):
    c, data = db
    identifier = add(c, prepared(c, data))
    reject(c, update(Delivery).where(Delivery.id == identifier).values(environment="test"), "immutable")


def test_acceptance_rollback_can_be_retried(db):
    c, data = db
    identifier = add(c, prepared(c, data))
    confirmation = accepted()
    nested = c.begin_nested()
    c.execute(update(Delivery).where(Delivery.id == identifier).values(**confirmation))
    nested.rollback()
    assert c.scalar(select(Delivery.transport_state).where(Delivery.id == identifier)) == "PREPARED"
    c.execute(update(Delivery).where(Delivery.id == identifier).values(**confirmation))
    assert c.scalar(select(Delivery.trial_state).where(Delivery.id == identifier)) == "PENDING"


@pytest.mark.parametrize("reason,environment,state", [
    ("TEST_ENVIRONMENT", "test", "PREPARED"),
    ("TRANSPORT_FAILED", "production", "FAILED"),
    ("ALREADY_STARTED", "production", "ACCEPTED"),
    ("EXISTING_SUBSCRIPTION", "production", "ACCEPTED"),
])
def test_controlled_ignored_reasons(db, reason, environment, state):
    c, data = db
    values = prepared(c, data, environment=environment, transport_state=state,
        trial_state="IGNORED", trial_reason=reason, processed_at=NOW)
    if state == "ACCEPTED":
        values.update(accepted())
    add(c, values)


def test_agency_coherence(db):
    c, data = db
    foreign_agency = c.scalar(insert(Agency).values(name="Other", slug=uuid.uuid4().hex).returning(Agency.id))
    c.execute(update(Conversation).where(Conversation.id == data["qr_conversation"]).values(agency_id=foreign_agency))
    c.execute(update(WhatsAppChannel).where(WhatsAppChannel.id == data["qr"]).values(agency_id=foreign_agency))
    reject(c, insert(Delivery).values(**prepared(c, data)), "owned by the client")

"""Phase 1A tests: private in-memory SQLite only, never an existing database.

Overrides the parent clean_database fixture. No Alembic migrations are executed.
PostgreSQL-only integrity DDL is checked by compilation, not applied here.
"""
import uuid
from datetime import timedelta, timezone
from decimal import Decimal
from pathlib import Path
import ast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, insert, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.models import Agency, Agent, Client, ClientSubscription, Conversation, Module, Plan, PlanModule, User, now_utc
from app.security import create_access_token, create_portal_token


@pytest.fixture(autouse=True)
def clean_database(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False)
    monkeypatch.setattr(get_settings(), "cookie_secure", False)

    def database():
        with factory() as db:
            yield db

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = database
    try:
        yield factory
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()


@pytest.fixture
def setup(clean_database):
    with clean_database() as db:
        agency = Agency(name="Agency A", slug="a")
        other_agency = Agency(name="Agency B", slug="b")
        db.add_all([agency, other_agency])
        db.flush()
        admin = User(agency_id=agency.id, name="Admin", email="admin@example.com", password_hash="unused")
        viewer = User(agency_id=agency.id, name="Viewer", email="viewer@example.com", password_hash="unused", role="viewer")
        customers = [Client(agency_id=a.id, name=name, portal_slug=name.lower(), portal_enabled=True,
                            portal_email_verified_at=now_utc()) for a, name in
                     [(agency, "Alpha"), (agency, "Beta"), (other_agency, "Gamma")]]
        plan = Plan(code="starter", name="Starter", currency="PEN", monthly_price=Decimal("59.90"),
                    available_for_new_subscriptions=True,
                    modules=[Module(code="ai_agent", name="Agente IA", is_available=True),
                             Module(code="orders", name="Pedidos", is_available=False)])
        other_plan = Plan(code="other", name="Other", currency="USD", monthly_price=Decimal("21.00"),
                          available_for_new_subscriptions=True)
        hidden_plan = Plan(code="hidden", name="Hidden", currency="PEN", available_for_new_subscriptions=False)
        inactive_plan = Plan(code="inactive", name="Inactive", currency="PEN", is_active=False,
                             available_for_new_subscriptions=True)
        db.add_all([admin, viewer, *customers, plan, other_plan, hidden_plan, inactive_plan])
        db.commit()
    with TestClient(app) as browser:
        browser.cookies.set("access_token", create_access_token(str(admin.id)))
        yield browser, customers, plan, other_plan, hidden_plan, inactive_plan, viewer, clean_database


def write(browser, customer, plan, status="TRIAL", **extra):
    return browser.put(f"/api/clients/{customer.id}/subscription", json={"plan_id": str(plan.id), "status": status, **extra})


def test_agency_and_company_isolation(setup):
    browser, (alpha, beta, gamma), plan, other, *rest = setup
    assert write(browser, alpha, plan).status_code == 200
    assert write(browser, beta, other, "ACTIVE").status_code == 200
    assert browser.get(f"/api/clients/{alpha.id}/subscription").json()["plan"]["id"] == str(plan.id)
    assert browser.get(f"/api/clients/{beta.id}/subscription").json()["plan"]["id"] == str(other.id)
    assert browser.get(f"/api/clients/{gamma.id}/subscription").status_code == 404
    assert write(browser, gamma, plan).status_code == 404
    assert browser.get(f"/api/clients/{uuid.uuid4()}/subscription").status_code == 404


@pytest.mark.parametrize("status", ["TRIAL", "ACTIVE", "PAYMENT_PENDING", "SUSPENDED", "CANCELLED"])
def test_explicit_statuses_do_not_start_trials(setup, status):
    browser, customers, plan, *rest = setup
    response = write(browser, customers[0], plan, status)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == status
    for field in ("first_activated_at", "trial_started_at", "trial_ends_at", "next_renewal_at"):
        assert data[field] is None


@pytest.mark.parametrize("payload", [
    {"status": "EXPIRED"}, {"status": "trial"}, {"status": None},
    {"first_activated_at": "2026-09-09T10:00:00Z"},
    {"trial_started_at": "2026-09-09T10:00:00Z"},
    {"trial_ends_at": "2026-09-12T10:00:00Z"},
    {"modules": ["orders"]}, {"next_renewal_at": "2026-10-01T10:00:00"},
])
def test_reject_invalid_states_and_read_only_dates(setup, payload):
    browser, customers, plan, *rest = setup
    data = {"plan_id": str(plan.id), "status": "TRIAL", **payload}
    assert browser.put(f"/api/clients/{customers[0].id}/subscription", json=data).status_code == 422


def test_unique_subscription_and_plan_modules(setup):
    browser, customers, plan, other, *rest = setup
    first = write(browser, customers[0], plan).json()
    assert [m["code"] for m in first["plan"]["modules"]] == ["ai_agent", "orders"]
    assert first["plan"]["modules"][1]["is_available"] is False
    assert first["plan"]["monthly_price"] == "59.90"
    second = write(browser, customers[0], other, "ACTIVE").json()
    assert second["id"] == first["id"]
    assert second["plan"]["modules"] == []
    with rest[-1]() as db:
        assert db.scalar(select(func.count()).select_from(ClientSubscription)) == 1
        db.add(ClientSubscription(client_id=customers[0].id, plan_id=plan.id, status="TRIAL"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(PlanModule(plan_id=plan.id, module_code="ai_agent"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(PlanModule(plan_id=other.id, module_code="missing_module"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_database_rejects_invalid_status(setup):
    _, customers, plan, *rest = setup
    with rest[-1]() as db:
        with pytest.raises(IntegrityError):
            db.execute(insert(ClientSubscription).values(client_id=customers[0].id, plan_id=plan.id, status="EXPIRED"))
            db.commit()


def test_existing_business_without_subscription_unchanged(setup):
    browser, customers, plan, *rest = setup
    alpha = customers[0]
    agent = browser.post("/api/agents", json={"client_id": str(alpha.id), "name": "Existing agent"})
    assert agent.status_code == 201
    conversation = browser.post("/api/conversations", json={"agent_id": agent.json()["id"]})
    assert conversation.status_code == 201
    before = browser.get(f"/api/clients/{alpha.id}").json()
    assert browser.get(f"/api/clients/{alpha.id}/subscription").json() is None
    assert browser.get(f"/api/clients/{alpha.id}").json() == before
    assert browser.get(f"/api/conversations/{conversation.json()['id']}").json()["mode"] == "ai"
    with rest[-1]() as db:
        assert db.scalar(select(func.count()).select_from(ClientSubscription)) == 0
        assert db.get(Agent, uuid.UUID(agent.json()["id"])).is_active is True
    write(browser, alpha, plan, "SUSPENDED")
    assert browser.get(f"/api/clients/{alpha.id}").json() == before
    assert browser.get(f"/api/conversations/{conversation.json()['id']}").json()["mode"] == "ai"


def test_catalog_authentication_and_availability(setup):
    browser, customers, plan, other, hidden, inactive, _, factory = setup
    assert {p["id"] for p in browser.get("/api/plans").json()} == {str(plan.id), str(other.id)}
    for unavailable in (hidden, inactive):
        assert write(browser, customers[0], unavailable).status_code == 409
    write(browser, customers[0], plan)
    with factory() as db:
        db.get(Plan, plan.id).available_for_new_subscriptions = False
        db.commit()
    assert write(browser, customers[0], plan, "CANCELLED").status_code == 200
    assert browser.get(f"/api/clients/{customers[0].id}/subscription").json()["plan"]["id"] == str(plan.id)
    browser.cookies.clear()
    for path in ("/api/plans", f"/api/clients/{customers[0].id}/subscription",
                 f"/api/portal/{customers[0].portal_slug}/plans", f"/api/portal/{customers[0].portal_slug}/subscription"):
        assert browser.get(path).status_code == 401


def test_portal_reads_only_own_subscription_and_cannot_write(setup):
    browser, (alpha, beta, gamma), plan, other, *rest = setup
    write(browser, alpha, plan)
    write(browser, beta, other, "ACTIVE")
    browser.cookies.clear()
    browser.cookies.set("portal_access_token", create_portal_token(str(alpha.id), alpha.portal_slug))
    path = f"/api/portal/{alpha.portal_slug}/subscription"
    response = browser.get(f"{path}?client_id={beta.id}")
    assert response.status_code == 200
    assert response.json()["client_id"] == str(alpha.id)
    assert browser.get(f"/api/portal/{beta.portal_slug}/subscription").status_code == 401
    assert browser.get(f"/api/portal/{gamma.portal_slug}/plans").status_code == 401
    assert browser.get(f"/api/portal/{alpha.portal_slug}/plans").status_code == 200
    assert browser.get("/api/plans").status_code == 401
    for method in ("put", "patch", "post", "delete"):
        assert browser.request(method, path, json={"status": "ACTIVE"}).status_code == 405
    assert write(browser, alpha, other, "ACTIVE").status_code == 401
    assert browser.get(path).json() == response.json()


def test_portal_verification_version_and_no_subscription(setup):
    browser, (alpha, beta, _), plan, other, hidden, inactive, viewer, factory = setup
    browser.cookies.clear()
    browser.cookies.set("portal_access_token", create_portal_token(str(alpha.id), alpha.portal_slug))
    path = f"/api/portal/{alpha.portal_slug}/subscription"
    assert browser.get(path).json() is None
    assert browser.get(f"/api/portal/{alpha.portal_slug}/summary").status_code == 200
    with factory() as db:
        db.get(Client, alpha.id).portal_email_verified_at = None
        db.commit()
    assert browser.get(path).status_code == 401
    with factory() as db:
        client = db.get(Client, alpha.id)
        client.portal_email_verified_at = now_utc()
        client.portal_credentials_version += 1
        db.commit()
    assert browser.get(path).status_code == 401


def test_non_admin_cannot_assign_subscription(setup):
    browser, customers, plan, other, hidden, inactive, viewer, factory = setup
    browser.cookies.set("access_token", create_access_token(str(viewer.id)))
    assert write(browser, customers[0], plan, "ACTIVE").status_code == 403


def test_trial_integrity_and_immutable_activation(setup):
    browser, customers, plan, *rest = setup
    factory = rest[-1]
    start = now_utc() - timedelta(days=10)
    with factory() as db:
        subscription = ClientSubscription(client_id=customers[0].id, plan_id=plan.id, status="TRIAL",
            first_activated_at=start, trial_started_at=start, trial_ends_at=start + timedelta(hours=72))
        db.add(subscription)
        db.commit()
        subscription_id = subscription.id
    # Phase 1A does not change an expired trial, including when reading it.
    assert browser.get(f"/api/clients/{customers[0].id}/subscription").json()["status"] == "TRIAL"
    assert write(browser, customers[0], plan, "PAYMENT_PENDING").status_code == 200
    with factory() as db:
        subscription = db.get(ClientSubscription, subscription_id)
        assert subscription.trial_ends_at - subscription.trial_started_at == timedelta(hours=72)
        subscription.first_activated_at += timedelta(days=1)
        subscription.trial_started_at += timedelta(days=1)
        subscription.trial_ends_at += timedelta(days=1)
        with pytest.raises(ValueError, match="immutable"):
            db.commit()
        db.rollback()
        subscription.trial_ends_at += timedelta(seconds=1)
        with pytest.raises(ValueError, match="72 hours"):
            db.commit()
        db.rollback()
        subscription.first_activated_at = None
        subscription.trial_started_at = None
        subscription.trial_ends_at = None
        with pytest.raises(ValueError, match="immutable"):
            db.commit()


def test_timezone_renewal_and_plan_price_constraints(setup):
    browser, customers, plan, *rest = setup
    response = write(browser, customers[0], plan, "ACTIVE", next_renewal_at="2026-10-01T10:00:00-05:00")
    assert response.status_code == 200
    assert response.json()["next_renewal_at"].startswith("2026-10-01T15:00:00")
    with rest[-1]() as db:
        db.get(Plan, plan.id).monthly_price = Decimal("-1")
        with pytest.raises(IntegrityError):
            db.commit()


def test_postgres_schema_and_migration_contract_without_applying():
    ddl = str(CreateTable(ClientSubscription.__table__).compile(dialect=postgresql.dialect()))
    assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert "interval '72 hours'" in ddl
    assert "UNIQUE (client_id)" in ddl
    path = Path(__file__).parents[1] / "migrations/versions/0022_client_subscriptions.py"
    tree = ast.parse(path.read_text())
    assignments = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.Assign)}
    assert assignments["down_revision"] == "0021_portal_email_verification"
    assert {row[0] for row in assignments["INITIAL_MODULES"]} == {
        "ai_agent", "leads", "advisor_handoff", "catalog", "orders", "appointments", "reservations",
        "payments", "operations", "delivery", "automations", "post_sale", "analytics",
    }
    assert "plan_id" not in Client.__table__.columns

"""Isolated SQLite tests. Never apply Alembic or use the parent PostgreSQL fixture."""
import ast
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from test_subscriptions import clean_database, setup  # noqa: F401: overrides destructive parent fixture
from app.models import Client, ClientSubscription, Plan, User, now_utc
from app.models_promotions import SubscriptionCharge as Charge, SubscriptionPromotion as Promotion, SubscriptionPromotionRedemption as Redemption
from app.schemas_promotions import PromotionWrite
from app.security import create_access_token, create_portal_token
from app.services.promotions import granted_snapshot


def payload(**changes):
    return {"code": "  ahorro  ", "discount_type": "PERCENTAGE", "value": "10", **changes}


def root_browser(setup):
    browser, customers, plan, *rest = setup
    with rest[-1]() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.com"))
        user.is_vendiq_admin = True  # Explicit test-only privilege assignment.
        db.commit()
    return browser


def create(setup, **changes):
    browser = root_browser(setup)
    response = browser.post("/api/subscription-promotions", json=payload(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def preview(setup, code="ahorro", customer=None, plan=None, **extra):
    return setup[0].post(f"/api/clients/{(customer or setup[1][0]).id}/subscription-promotions/preview",
                         json={"code": code, "plan_id": str((plan or setup[2]).id), **extra})


@pytest.mark.parametrize("changes", [
    {"code": "  "}, {"discount_type": "OTHER"}, {"value": "0"}, {"value": "100.01"},
    {"value": "NaN"}, {"value": 10.1}, {"value": "1.001"},
    {"discount_type": "FIXED"}, {"discount_type": "FIXED", "value": "-1", "currency": "PEN"},
    {"currency": "pen"}, {"plan_scope": "SELECTED"}, {"plan_scope": "OTHER"},
    {"plan_ids": [str(uuid.uuid4())]}, {"duration": "NEXT_N_RENEWALS", "cycles": 0},
    {"cycles": 2}, {"max_total_uses": 0}, {"max_uses_per_client": 0},
    {"starts_at": "2026-01-01T00:00:00"},
    {"starts_at": "2026-02-01T00:00:00Z", "ends_at": "2026-01-01T00:00:00Z"},
    {"trial_ends_at": "2026-02-01T00:00:00Z"},
])
def test_validation(changes):
    with pytest.raises(ValidationError):
        PromotionWrite(**payload(**changes))


def test_permissions_and_no_automatic_privilege(setup):
    browser, customers, plan, *rest = setup
    assert browser.post("/api/subscription-promotions", json=payload()).status_code == 403
    assert browser.get("/api/subscription-promotions").status_code == 403
    assert preview(setup).status_code == 403
    browser.cookies.set("access_token", create_access_token(str(rest[-2].id)))
    assert browser.post("/api/subscription-promotions", json=payload()).status_code == 403
    with rest[-1]() as db:
        assert not any(db.scalars(select(User.is_vendiq_admin)))


def test_normalization_update_and_decimal_preview_is_idempotent(setup):
    promotion = create(setup)
    assert promotion["code"] == "AHORRO"
    assert setup[0].post("/api/subscription-promotions", json=payload(code="aHoRrO")).status_code == 409
    first = preview(setup).json()
    assert first == preview(setup, " AHORRO ").json()
    assert (first["original_price"], first["discount"], first["total_final"]) == ("59.90", "5.99", "53.91")
    assert first["consumes_use"] is False and first["extends_trial"] is False
    with setup[-1]() as db:
        for model in (Charge, Redemption, ClientSubscription):
            assert db.scalar(select(func.count()).select_from(model)) == 0
    assert setup[0].put(f'/api/subscription-promotions/{promotion["id"]}', json=payload(value="100")).status_code == 200
    assert preview(setup).json()["total_final"] == "0.00"


@pytest.mark.parametrize("value,expected", [("1.25", "0.75"), ("100", "59.90")])
def test_rounding(setup, value, expected):
    create(setup, value=value)
    assert preview(setup).json()["discount"] == expected


def test_fixed_cap_currency_scope_restriction_dates(setup):
    create(setup, discount_type="FIXED", value="100", currency="PEN", plan_scope="SELECTED",
           plan_ids=[str(setup[2].id)], restricted_client_id=str(setup[1][0].id))
    assert preview(setup).json()["total_final"] == "0.00"
    assert preview(setup, customer=setup[1][1]).status_code == 422
    assert preview(setup, plan=setup[3]).status_code == 422
    create(setup, code="usd", discount_type="FIXED", value="1", currency="USD")
    assert preview(setup, "usd").status_code == 422
    create(setup, code="future", starts_at=(now_utc()+timedelta(days=1)).isoformat())
    create(setup, code="expired", ends_at=(now_utc()-timedelta(seconds=1)).isoformat())
    assert preview(setup, "future").status_code == 422
    assert preview(setup, "expired").status_code == 422
    assert preview(setup, "missing").status_code == 422


def test_portal_isolation_verified_and_untrusted_fields(setup):
    create(setup, restricted_client_id=str(setup[1][0].id))
    browser, (alpha, beta, gamma), plan, *rest = setup
    browser.cookies.clear()
    body = {"code": "ahorro", "plan_id": str(plan.id)}
    path = f"/api/portal/{alpha.portal_slug}/subscription-promotions/preview"
    assert browser.post(path, json=body).status_code == 401
    browser.cookies.set("portal_access_token", create_portal_token(str(alpha.id), alpha.portal_slug, alpha.portal_credentials_version))
    assert browser.post(path, json=body).status_code == 200
    assert browser.post(f"/api/portal/{beta.portal_slug}/subscription-promotions/preview", json=body).status_code in (401, 403)
    for key in ("client_id", "company_id", "value", "percentage", "price", "total_final", "status", "trial_ends_at", "restricted_client_id"):
        assert browser.post(path, json={**body, key: "untrusted"}).status_code == 422
    assert browser.get("/api/subscription-promotions").status_code == 401
    with rest[-1]() as db:
        db.get(Client, alpha.id).portal_email_verified_at = None
        db.commit()
    assert browser.post(path, json=body).status_code == 401


def history(setup, promotion, active=True, key="contract-1"):
    with setup[-1]() as db:
        subscription = db.scalar(select(ClientSubscription).where(ClientSubscription.client_id == setup[1][0].id))
        if subscription is None:
            subscription = ClientSubscription(client_id=setup[1][0].id, plan_id=setup[2].id, status="TRIAL")
            db.add(subscription)
            db.flush()
        p = db.get(Promotion, uuid.UUID(promotion["id"]))
        redemption = Redemption(client_id=subscription.client_id, subscription_id=subscription.id,
                                plan_id=subscription.plan_id, promotion_id=p.id, snapshot=granted_snapshot(db, p),
                                cycles_granted=p.cycles, is_active=active, idempotency_key=key, confirmed_at=now_utc())
        db.add(redemption)
        db.commit()
        return redemption


def test_limits_and_no_stacking(setup):
    p = create(setup, max_total_uses=1)
    r = history(setup, p)
    assert preview(setup).status_code == 409
    assert preview(setup, customer=setup[1][1]).status_code == 422
    with pytest.raises(IntegrityError):
        history(setup, p, key="another-contract")
    with setup[-1]() as db:
        db.get(Redemption, r.id).is_active = False
        db.commit()
    assert preview(setup).status_code == 422
    with pytest.raises(IntegrityError):
        history(setup, p, active=False)  # idempotency key cannot be used twice


def test_per_company_limit(setup):
    p = create(setup)
    history(setup, p, active=False)
    assert preview(setup).status_code == 422
    assert preview(setup, customer=setup[1][1]).status_code == 200


def charge_values(r, **changes):
    return dict(client_id=r.client_id, subscription_id=r.subscription_id, plan_id=r.plan_id,
                promotion_id=r.promotion_id, redemption_id=r.id, cycle_number=1,
                period_start=now_utc(), period_end=now_utc()+timedelta(days=30), currency="PEN",
                original_price=Decimal("59.90"), discount=Decimal("5.99"), total_final=Decimal("53.91"),
                status="ISSUED", issued_at=now_utc(), idempotency_key="charge-1") | changes


def test_snapshot_charge_history_deletion_and_identity(setup):
    p = create(setup)
    r = history(setup, p)
    assert setup[0].put(f'/api/subscription-promotions/{p["id"]}', json=payload(value="50")).status_code == 200
    with setup[-1]() as db:
        assert db.get(Redemption, r.id).snapshot["value"] == "10.00"
        db.get(Plan, r.plan_id).monthly_price = Decimal("99")
        c = Charge(**charge_values(r))
        db.add(c)
        db.commit()
        assert c.total_final == Decimal("53.91")
        c.total_final = Decimal("1")
        with pytest.raises(ValueError, match="immutable"):
            db.commit()
        db.rollback()
        db.get(Redemption, r.id).snapshot = {"value": "99"}
        with pytest.raises(ValueError, match="immutable"):
            db.commit()
        db.rollback()
        for model, identity in ((Client, r.client_id), (Plan, r.plan_id), (Promotion, r.promotion_id), (ClientSubscription, r.subscription_id)):
            with pytest.raises(IntegrityError):
                db.execute(delete(model).where(model.id == identity))
                db.commit()
            db.rollback()
        for changes in ({}, {"cycle_number": 2}, {"cycle_number": 2, "idempotency_key": "second", "client_id": setup[1][1].id},
                        {"cycle_number": 2, "idempotency_key": "negative", "total_final": Decimal("-1")}):
            db.add(Charge(**charge_values(r, **changes)))
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()


def test_migration_structure_only():
    path = Path(__file__).parents[1] / "migrations/versions/0023_subscription_promotions.py"
    source = path.read_text()
    ast.parse(source)
    assert 'down_revision = "0022_client_subscriptions"' in source
    assert "DEFERRABLE INITIALLY DEFERRED" in source
    assert "economic history cannot be deleted" in source
    assert "cannot downgrade with economic history" in source
    for model in (Promotion, Redemption, Charge):
        ddl = str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        assert model.__tablename__ in source
        assert "FLOAT" not in ddl
    for model in (Redemption, Charge):
        assert all(fk.ondelete == "RESTRICT" for fk in model.__table__.foreign_keys)


def test_renewals_trial_and_read_history_are_isolated(setup):
    p = create(setup, duration="NEXT_N_RENEWALS", cycles=3)
    browser, customers, plan, *rest = setup
    with setup[-1]() as db:
        activated = now_utc()
        sub = ClientSubscription(client_id=customers[0].id, plan_id=plan.id, status="TRIAL",
                                 first_activated_at=activated, trial_started_at=activated,
                                 trial_ends_at=activated+timedelta(hours=72))
        db.add(sub)
        db.commit()
        sub_id = sub.id
    for _ in range(2):
        result = preview(setup)
        assert result.status_code == 200
        assert result.json()["cycles"] == 3
    with setup[-1]() as db:
        sub = db.get(ClientSubscription, sub_id)
        assert sub.status == "TRIAL"
        assert sub.trial_ends_at-sub.trial_started_at == timedelta(hours=72)
        assert db.scalar(select(func.count()).select_from(Redemption)) == 0
    r = history(setup, p)
    response = browser.get(f"/api/clients/{customers[0].id}/subscription-promotion-redemptions")
    assert response.status_code == 200
    assert response.json()[0]["snapshot"]["cycles"] == 3
    browser.cookies.clear()
    browser.cookies.set("portal_access_token", create_portal_token(str(customers[1].id), customers[1].portal_slug))
    assert browser.get(f"/api/portal/{customers[1].portal_slug}/subscription-promotion-redemptions").json() == []
    assert browser.get(f"/api/portal/{customers[0].portal_slug}/subscription-promotion-redemptions").status_code in (401, 403)


def test_first_month_rejected_after_paid_charge(setup):
    create(setup)
    with setup[-1]() as db:
        sub = ClientSubscription(client_id=setup[1][0].id, plan_id=setup[2].id, status="ACTIVE")
        db.add(sub)
        db.flush()
        issued = now_utc()
        db.add(Charge(client_id=sub.client_id, subscription_id=sub.id, plan_id=sub.plan_id,
                      cycle_number=1, period_start=issued, period_end=issued+timedelta(days=30),
                      currency="PEN", original_price=Decimal("59.90"), discount=Decimal("0"),
                      total_final=Decimal("59.90"), status="PAID", issued_at=issued, paid_at=issued,
                      idempotency_key="already-paid"))
        db.commit()
    assert preview(setup).status_code == 422


def test_selected_update_and_catalog_permissions(setup):
    p = create(setup, plan_scope="SELECTED", plan_ids=[str(setup[2].id)])
    browser = setup[0]
    assert browser.get("/api/subscription-promotions").json()[0]["plan_ids"] == [str(setup[2].id)]
    assert browser.put(f'/api/subscription-promotions/{p["id"]}', json=payload(plan_scope="SELECTED")).status_code == 422
    assert browser.put(f'/api/subscription-promotions/{p["id"]}', json=payload()).status_code == 200
    assert browser.get("/api/subscription-promotions").json()[0]["plan_ids"] == []
    browser.cookies.set("access_token", create_access_token(str(setup[-2].id)))
    assert browser.put(f'/api/subscription-promotions/{p["id"]}', json=payload(value="100")).status_code == 403


def test_explicit_unlimited_company_uses(setup):
    p = create(setup, max_uses_per_client=None, max_total_uses=None)
    assert p["max_uses_per_client"] is None
    history(setup, p, active=False)
    assert preview(setup).status_code == 200

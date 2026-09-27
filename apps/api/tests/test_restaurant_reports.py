from datetime import datetime, timezone
from decimal import Decimal

from conftest import TestingSession
from sqlalchemy import select

from app.models import User
from app.models_orders import RestaurantOrder


def _register_restaurant(client, monkeypatch, email="reports@test.com"):
    sent = []
    monkeypatch.setattr("app.services.portal_verification.send_verification_email", lambda _email, code: sent.append(code))
    response = client.post(
        "/api/auth/register",
        json={
            "agency_name": "Agencia Reportes",
            "industry": "restaurante",
            "name": "Admin Reportes",
            "email": email,
            "password": "contrasena-segura",
        },
    )
    assert response.status_code == 201, response.text
    assert client.post("/api/portal/email-verification/confirm", json={"code": sent[-1]}).status_code == 200
    return client.get("/api/clients/current").json()["id"]


def test_sales_report_counts_only_confirmed_tenant_payments_and_pdf(client, monkeypatch):
    client_id = _register_restaurant(client, monkeypatch)
    now = datetime.now(timezone.utc)

    with TestingSession() as db:
        user = db.scalar(select(User).where(User.email == "reports@test.com"))
        confirmed = RestaurantOrder(
            client_id=client_id,
            order_number="ORD-REPORT-1",
            source="waiter",
            modality="dine_in",
            table_number="4",
            subtotal=Decimal("25.00"),
            total=Decimal("25.00"),
            order_status="closed",
            payment_status="confirmed",
            payment_method="cash",
            payment_confirmed_at=now,
            payment_confirmed_by_user_id=user.id,
        )
        pending = RestaurantOrder(
            client_id=client_id,
            order_number="ORD-REPORT-2",
            source="whatsapp",
            modality="delivery",
            customer_name="Cliente",
            subtotal=Decimal("99.00"),
            total=Decimal("99.00"),
            order_status="pending_payment",
            payment_status="pending",
        )
        db.add_all([confirmed, pending])
        db.commit()

    report = client.get(f"/api/restaurants/{client_id}/reports/sales")
    assert report.status_code == 200, report.text
    data = report.json()
    assert data["total_sales"] == "25.00"
    assert data["paid_orders"] == 1
    assert data["pending_orders"] == 1
    assert data["payment_totals"]["cash"] == "25.00"
    assert data["origin_totals"]["Mesa"] == "25.00"
    assert [row["order_number"] for row in data["movements"]] == ["ORD-REPORT-1"]

    pdf = client.get(f"/api/restaurants/{client_id}/reports/sales.pdf")
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert pdf.content.startswith(b"%PDF-1.4")

import pytest

from conftest import TestingSession
from app.models import Agent, Client, User
from app.services.knowledge import build_system_prompt
from app.services.email import EmailDeliveryError


@pytest.fixture(autouse=True)
def skip_email_delivery(monkeypatch):
    monkeypatch.setattr("app.services.portal_verification.send_verification_email", lambda *args: None)


@pytest.fixture
def restaurant_client(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.services.portal_verification.send_verification_email", lambda email, code: sent.append(code))
    response = client.post(
        "/api/auth/register",
        json={
            "agency_name": "Agencia Prisma",
            "industry": "restaurante",
            "name": "Ana Admin",
            "email": "ana@prisma.com",
            "password": "contrasena-segura",
        },
    )
    assert response.status_code == 201
    assert client.post("/api/portal/email-verification/confirm", json={"code": sent[-1]}).status_code == 200
    return client


def test_restaurant_onboarding_persists_structured_setup_and_readiness(restaurant_client):
    client = restaurant_client.get("/api/clients/current").json()
    client_id = client["id"]

    initial = restaurant_client.get(f"/api/restaurants/{client_id}/onboarding")
    assert initial.status_code == 200
    assert initial.json()["profile"]["currency"] == "PEN"

    profile = restaurant_client.patch(
        f"/api/restaurants/{client_id}/profile",
        json={
            "address": "Av. Central 123",
            "phone": "+51987654321",
            "currency": "PEN",
            "opening_hours": {"lunes": "09:00 - 22:00"},
        },
    )
    assert profile.status_code == 200

    category = restaurant_client.post(f"/api/restaurants/{client_id}/menu/categories", json={"name": "Hamburguesas"})
    assert category.status_code == 201
    category_id = category.json()["id"]
    product = restaurant_client.post(
        f"/api/restaurants/{client_id}/menu/products",
        json={
            "category_id": category_id,
            "name": "Clásica",
            "price": "18.50",
            "variants": [{"name": "Doble", "price": "5.00"}],
            "extras": [{"name": "Papas", "price": "4.00"}],
        },
    )
    assert product.status_code == 201
    assert product.json()["variants"][0]["name"] == "Doble"

    assert restaurant_client.patch(
        f"/api/restaurants/{client_id}/modalities",
        json={"dine_in_enabled": True, "pickup_enabled": True, "delivery_enabled": True},
    ).status_code == 200
    assert restaurant_client.post(
        f"/api/restaurants/{client_id}/delivery-zones",
        json={"name": "Centro", "fee": "5.00", "minimum_order": "20.00", "estimated_minutes": 45},
    ).status_code == 201
    assert restaurant_client.put(
        f"/api/restaurants/{client_id}/payment-methods/other",
        json={
            "display_name": "Datos para pagos",
            "account_number": "987654321",
            "account_name": "Titular de prueba",
            "instructions": "Envía el comprobante después de pagar.",
            "receipt_required": True,
            "is_active": True,
        },
    ).status_code == 200

    candidate = restaurant_client.get(f"/api/restaurants/{client_id}/staff/candidates").json()[0]
    assert restaurant_client.post(
        f"/api/restaurants/{client_id}/staff",
        json={"user_id": candidate["id"], "role": "admin"},
    ).status_code == 201

    agent = restaurant_client.post(
        "/api/agents",
        json={
            "client_id": client_id,
            "name": "Sofía",
            "personality": "Cercana",
            "instructions": "Ayuda con el menú.",
            "widget_greeting": "Hola, ¿qué deseas pedir?",
            "is_active": False,
        },
    )
    assert agent.status_code == 201

    ready = restaurant_client.get(f"/api/restaurants/{client_id}/onboarding")
    assert ready.status_code == 200
    assert ready.json()["readiness"]["ready"] is True
    assert ready.json()["readiness"]["status"] == "ready_to_activate"

    with TestingSession() as db:
        assert db.scalar(db.query(Client).filter(Client.id == client_id).statement) is not None
        assert db.scalar(db.query(User).filter(User.id == candidate["id"]).statement) is not None
        stored_agent = db.get(Agent, agent.json()["id"])
        prompt = build_system_prompt(stored_agent, "", db)
        assert "CONTEXTO OPERATIVO ESTRUCTURADO DEL RESTAURANTE" in prompt
        assert "Clásica" in prompt
        assert "DATOS PARA PAGOS" in prompt
        assert "número para pagos: 987654321" in prompt
        assert "Yape" not in prompt
        assert "destino o receptor" in prompt
        assert "Titular de prueba" in prompt
        assert "confirmación depende de la revisión manual del administrador" in prompt


def test_restaurant_onboarding_does_not_expose_another_client(restaurant_client):
    response = restaurant_client.get("/api/restaurants/00000000-0000-0000-0000-000000000000/onboarding")
    assert response.status_code == 404


def test_delivery_whatsapp_is_configuration_not_a_duplicate_staff_record(restaurant_client):
    client_id = restaurant_client.get("/api/clients/current").json()["id"]
    initial_modalities = restaurant_client.get(f"/api/restaurants/{client_id}/modalities").json()
    before = restaurant_client.get(f"/api/restaurants/{client_id}/staff").json()

    enabled = {**initial_modalities, "delivery_enabled": True, "delivery_whatsapp": "965 396 982"}
    updated = restaurant_client.patch(f"/api/restaurants/{client_id}/modalities", json=enabled)
    assert updated.status_code == 200
    assert updated.json()["delivery_whatsapp"] == "51965396982"

    after_enabled = restaurant_client.get(f"/api/restaurants/{client_id}/staff").json()
    assert [row["id"] for row in after_enabled] == [row["id"] for row in before]
    assert not any(row["role"] == "delivery" and row["user"]["phone"] == "51965396982" for row in after_enabled)

    disabled = {**updated.json(), "delivery_enabled": False, "delivery_whatsapp": None}
    cleared = restaurant_client.patch(f"/api/restaurants/{client_id}/modalities", json=disabled)
    assert cleared.status_code == 200
    assert cleared.json()["delivery_whatsapp"] is None
    assert [row["id"] for row in restaurant_client.get(f"/api/restaurants/{client_id}/staff").json()] == [row["id"] for row in before]


def test_restaurant_staff_can_create_and_update_separate_roles(restaurant_client):
    client_id = restaurant_client.get("/api/clients/current").json()["id"]

    created = restaurant_client.post(
        f"/api/restaurants/{client_id}/staff",
        json={
            "name": "Lucía Caja",
            "email": "lucia.caja@prisma.com",
            "password": "clave-segura-123",
            "role": "cashier",
            "is_active": False,
        },
    )
    assert created.status_code == 201
    assert created.json()["role"] == "cashier"
    assert created.json()["is_active"] is False
    assert created.json()["user"]["email"] == "lucia.caja@prisma.com"

    duplicate = restaurant_client.post(
        f"/api/restaurants/{client_id}/staff",
        json={
            "name": "Otra Caja",
            "email": "lucia.caja@prisma.com",
            "password": "clave-segura-123",
            "role": "cashier",
        },
    )
    assert duplicate.status_code == 409

    staff_id = created.json()["id"]
    updated = restaurant_client.patch(
        f"/api/restaurants/{client_id}/staff/{staff_id}",
        json={"role": "waiter", "is_active": True},
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == "waiter"
    assert updated.json()["is_active"] is True


def test_staff_creation_allows_email_failure_only_in_local_development(restaurant_client, monkeypatch):
    client_id = restaurant_client.get("/api/clients/current").json()["id"]

    def fail_delivery(*args):
        raise EmailDeliveryError("SMTP unavailable")

    monkeypatch.setattr("app.services.portal_verification.send_verification_email", fail_delivery)
    created = restaurant_client.post(
        f"/api/restaurants/{client_id}/staff",
        json={"name": "Mesero Local", "email": "mesero.local@prisma.com", "password": "clave-segura-123", "role": "waiter"},
    )
    assert created.status_code == 201
    assert created.json()["development_verification_bypassed"] is True
    assert restaurant_client.post("/api/auth/login", json={"email": "mesero.local@prisma.com", "password": "clave-segura-123"}).status_code == 200


def test_staff_creation_keeps_verification_required_outside_local_development(restaurant_client, monkeypatch):
    client_id = restaurant_client.get("/api/clients/current").json()["id"]

    def fail_delivery(*args):
        raise EmailDeliveryError("SMTP unavailable")

    monkeypatch.setattr("app.services.portal_verification.send_verification_email", fail_delivery)
    monkeypatch.setattr("app.routers.restaurants.is_local_development", lambda: False)
    response = restaurant_client.post(
        f"/api/restaurants/{client_id}/staff",
        json={"name": "Mesero Producción", "email": "mesero.prod@prisma.com", "password": "clave-segura-123", "role": "waiter"},
    )
    assert response.status_code == 503

import uuid
import pytest

from sqlalchemy import select

from conftest import TestingSession
from app.models import Agent, Client, Conversation, Message
from app.models_restaurant import RestaurantWelcomeFlyer
from app.services.welcome_flyer import create_welcome_flyer_message


PNG = b"\x89PNG\r\n\x1a\n" + b"flyer"


@pytest.fixture
def restaurant_client(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.services.portal_verification.send_verification_email", lambda email, code: sent.append(code))
    response = client.post(
        "/api/auth/register",
        json={
            "agency_name": "Agencia Flyer",
            "industry": "restaurante",
            "name": "Restaurante Flyer",
            "email": "flyer@example.com",
            "password": "contrasena-segura",
        },
    )
    assert response.status_code == 201
    assert client.post("/api/portal/email-verification/confirm", json={"code": sent[-1]}).status_code == 200
    return client


def test_welcome_flyer_is_tenant_scoped_and_persisted(restaurant_client):
    current = restaurant_client.get("/api/clients/current").json()
    client_id = current["id"]
    response = restaurant_client.put(
        f"/api/restaurants/{client_id}/welcome-flyer",
        files={"file": ("promo.png", PNG, "image/png")},
        data={"enabled": "true", "message": "Bienvenido a nuestro restaurante"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["mime_type"] == "image/png"
    assert body["image_url"].endswith("/welcome-flyer/image")
    image = restaurant_client.get(body["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")
    assert image.content == PNG
    onboarding = restaurant_client.get(f"/api/restaurants/{client_id}/onboarding").json()
    assert onboarding["welcome_flyer"]["filename"] == "promo.png"


def test_welcome_flyer_rejects_invalid_format_and_can_be_deleted(restaurant_client):
    client_id = restaurant_client.get("/api/clients/current").json()["id"]
    invalid = restaurant_client.put(
        f"/api/restaurants/{client_id}/welcome-flyer",
        files={"file": ("promo.jpg", b"not-a-jpeg", "image/jpeg")},
        data={"enabled": "true", "message": ""},
    )
    assert invalid.status_code == 422
    assert restaurant_client.put(
        f"/api/restaurants/{client_id}/welcome-flyer",
        files={"file": ("promo.png", PNG, "image/png")},
        data={"enabled": "false", "message": ""},
    ).status_code == 200
    assert restaurant_client.delete(f"/api/restaurants/{client_id}/welcome-flyer").status_code == 204
    assert restaurant_client.get(f"/api/restaurants/{client_id}/onboarding").json()["welcome_flyer"] is None


def test_welcome_flyer_message_is_created_once_per_new_conversation(restaurant_client):
    client_id = uuid.UUID(restaurant_client.get("/api/clients/current").json()["id"])
    with TestingSession() as db:
        client = db.get(Client, client_id)
        agent = Agent(agency_id=client.agency_id, client_id=client.id, name="Sofía", provider="openai", model="test")
        db.add(agent)
        db.flush()
        flyer = RestaurantWelcomeFlyer(client_id=client.id, image_data=PNG, mime_type="image/png", filename="promo.png", enabled=True)
        conversation = Conversation(agency_id=client.agency_id, client_id=client.id, agent_id=agent.id, channel="playground")
        db.add_all([flyer, conversation])
        db.flush()
        first = create_welcome_flyer_message(db, conversation)
        db.commit()
        second = create_welcome_flyer_message(db, conversation)
        assert first is not None
        assert first.media_kind == "welcome_flyer"
        assert first.content == ""
        assert second is None
        assert db.scalar(select(Message.id).where(Message.conversation_id == conversation.id, Message.media_kind == "welcome_flyer")) is not None

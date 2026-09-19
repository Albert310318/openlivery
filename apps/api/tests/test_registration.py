from sqlalchemy import select

from app.models import Agency, Agent, Client, ClientSubscription, User
from conftest import TestingSession


def test_registration_creates_company_user_session_without_agent_or_plan(client):
    response = client.post(
        "/api/auth/register",
        json={
            "agency_name": "Empresa Demo",
            "industry": "Turismo",
            "whatsapp": "+51 (987) 654-321",
            "name": "Ana Responsable",
            "email": "ana@empresa-demo.pe",
            "password": "contrasena-segura",
        },
    )

    assert response.status_code == 201
    assert client.cookies.get("access_token")
    assert client.get("/api/auth/me").status_code == 200

    with TestingSession() as db:
        user = db.scalar(select(User).where(User.email == "ana@empresa-demo.pe"))
        company = db.scalar(select(Client).where(Client.name == "Empresa Demo"))
        assert user is not None
        assert company is not None
        assert company.agency_id == user.agency_id
        assert company.industry == "Turismo"
        assert company.sales_advisor_phone == "+51987654321"
        assert user.role == "admin"
        assert user.is_vendiq_admin is False
        assert db.scalar(select(Agent).where(Agent.client_id == company.id)) is None
        assert db.scalar(select(ClientSubscription).where(ClientSubscription.client_id == company.id)) is None


def test_registration_rejects_invalid_whatsapp_without_creating_company(client):
    response = client.post(
        "/api/auth/register",
        json={
            "agency_name": "Empresa Inválida",
            "industry": "Servicios",
            "whatsapp": "not-a-phone",
            "name": "Responsable",
            "email": "invalid@empresa.pe",
            "password": "contrasena-segura",
        },
    )

    assert response.status_code == 422
    with TestingSession() as db:
        assert db.scalar(select(Agency).where(Agency.name == "Empresa Inválida")) is None

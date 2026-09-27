import pytest


@pytest.fixture
def restaurant_owner(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.services.portal_verification.send_verification_email", lambda email, code: sent.append(code))
    assert client.post(
        "/api/auth/register",
        json={"agency_name": "Agencia Roles", "industry": "restaurante", "name": "Dueño", "email": "owner@roles.com", "password": "contrasena-segura"},
    ).status_code == 201
    assert client.post("/api/portal/email-verification/confirm", json={"code": sent[-1]}).status_code == 200
    owner_login = client.post("/api/auth/unified-login", json={"email": "owner@roles.com", "password": "contrasena-segura"})
    assert owner_login.status_code == 200
    assert owner_login.json()["redirect_to"].startswith("/?client_id=")
    client.verification_codes = sent
    return client


@pytest.mark.parametrize("role", ["cashier", "waiter", "kitchen", "delivery"])
def test_operational_staff_is_limited_to_its_restaurant_panel(restaurant_owner, role):
    client = restaurant_owner
    client_id = client.get("/api/clients/current").json()["id"]
    staff_email = f"{role}@roles.com"
    created = client.post(
        f"/api/restaurants/{client_id}/staff",
        json={"name": role, "email": staff_email, "password": "clave-segura-1", "role": role},
    )
    assert created.status_code == 201

    client.post("/api/auth/logout")
    login = client.post("/api/auth/login", json={"email": staff_email, "password": "clave-segura-1"})
    assert login.status_code == 200
    assert "verification_type" not in login.json()
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["restaurant_role"] == role
    assert me.json()["restaurant_client_id"] == client_id

    client.post("/api/auth/logout")
    unified_login = client.post("/api/auth/unified-login", json={"email": staff_email, "password": "clave-segura-1"})
    assert unified_login.status_code == 200
    assert unified_login.json()["redirect_to"] == f"/orders?client_id={client_id}"

    assert client.get("/api/restaurants/{}/orders".format(client_id)).status_code == 200
    assert client.get("/api/restaurants/{}/menu".format(client_id)).status_code == 200
    assert client.get("/api/dashboard").status_code == 403
    assert client.get("/api/agents").status_code == 403
    assert client.get("/api/clients/{}".format(client_id)).status_code == 403


def test_local_unverified_waiter_can_login_without_sending_code(restaurant_owner, monkeypatch):
    from app.models import User
    from conftest import TestingSession

    client = restaurant_owner
    client_id = client.get("/api/clients/current").json()["id"]
    created = client.post(
        f"/api/restaurants/{client_id}/staff",
        json={"name": "Mesero sin verificar", "email": "waiter-unverified@roles.com", "password": "clave-segura-1", "role": "waiter"},
    )
    assert created.status_code == 201
    user_id = created.json()["user_id"]
    with TestingSession() as db:
        assert db.get(User, user_id).email_verification_pending is True

    attempted_codes = []

    def fail_if_email_is_attempted(*args):
        attempted_codes.append(True)
        raise AssertionError("login should not send an email in local development")

    monkeypatch.setattr("app.services.portal_verification.send_verification_email", fail_if_email_is_attempted)
    client.post("/api/auth/logout")
    login = client.post("/api/auth/unified-login", json={"email": "waiter-unverified@roles.com", "password": "clave-segura-1"})
    assert login.status_code == 200
    assert login.json()["redirect_to"] == f"/orders?client_id={client_id}"
    assert attempted_codes == []
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["restaurant_role"] == "waiter"
    assert me.json()["restaurant_client_id"] == client_id
    assert client.get("/api/agents").status_code == 403


def test_restaurant_admin_navigation_and_tenant_isolation(restaurant_owner):
    from app.models import Agent, Client, Conversation, Lead
    from conftest import TestingSession

    client = restaurant_owner
    own_client_id = client.get("/api/clients/current").json()["id"]
    with TestingSession() as db:
        own_client = db.get(Client, own_client_id)
        other_client = Client(
            agency_id=own_client.agency_id,
            name="Otra Pyme",
            industry="restaurante",
            portal_slug="otra-pyme",
        )
        db.add(other_client)
        db.flush()
        other_agent = Agent(agency_id=own_client.agency_id, client_id=other_client.id, name="Agente ajeno")
        db.add(other_agent)
        db.flush()
        db.add(Conversation(agency_id=own_client.agency_id, client_id=other_client.id, agent_id=other_agent.id))
        db.add(Lead(agency_id=own_client.agency_id, client_id=other_client.id, agent_id=other_agent.id, name="Lead ajeno"))
        db.commit()
        other_client_id = str(other_client.id)
        other_agent_id = str(other_agent.id)

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["restaurant_role"] == "admin"
    assert client.get("/api/agents").status_code == 200
    assert all(row["client_id"] == own_client_id for row in client.get("/api/agents").json())
    assert client.get(f"/api/agents/{other_agent_id}").status_code == 404
    assert client.get(f"/api/clients/{other_client_id}").status_code in {403, 404}
    assert client.get(f"/api/conversations?client_id={other_client_id}").json() == []
    assert client.get(f"/api/leads?client_id={other_client_id}").json() == []
    assert client.get(f"/api/whatsapp/channels/{other_client_id}").status_code in {404, 403}
    assert client.get("/api/agency").status_code == 403
    assert client.get("/api/providers").status_code == 403


def test_restaurant_admin_can_view_an_active_staff_panel_without_impersonating(restaurant_owner):
    client = restaurant_owner
    client_id = client.get("/api/clients/current").json()["id"]
    created = client.post(
        f"/api/restaurants/{client_id}/staff",
        json={"name": "Mesero visible", "email": "mesero-visible@roles.com", "password": "clave-segura-1", "role": "waiter"},
    )
    assert created.status_code == 201
    staff_id = created.json()["id"]

    panel = client.get(
        f"/api/restaurants/{client_id}/orders",
        params={"view_as_role": "waiter", "view_as_staff_id": staff_id},
    )
    assert panel.status_code == 200

    invalid_role = client.get(
        f"/api/restaurants/{client_id}/orders",
        params={"view_as_role": "kitchen", "view_as_staff_id": staff_id},
    )
    assert invalid_role.status_code == 404

    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "mesero-visible@roles.com", "password": "clave-segura-1"}).status_code == 200
    denied = client.get(
        f"/api/restaurants/{client_id}/orders",
        params={"view_as_role": "waiter", "view_as_staff_id": staff_id},
    )
    assert denied.status_code == 403


def test_vendiq_admin_keeps_platform_access(client):
    from app.models import Agency, Client, User
    from app.security import hash_password
    from conftest import TestingSession

    with TestingSession() as db:
        agency = Agency(name="AYV", slug="ayv")
        db.add(agency)
        db.flush()
        db.add(User(agency_id=agency.id, name="AYV", email="root@ayv.pe", password_hash=hash_password("clave-segura-1"), role="admin", is_vendiq_admin=True))
        db.add(Client(agency_id=agency.id, name="Pollería Campos", industry="restaurante", portal_slug="polleria-campos"))
        db.commit()
    assert client.post("/api/auth/login", json={"email": "root@ayv.pe", "password": "clave-segura-1"}).status_code == 200
    me = client.get("/api/auth/me")
    assert me.json()["is_vendiq_admin"] is True
    assert me.json()["restaurant_role"] is None
    assert me.json()["restaurant_client_id"] is None
    client.post("/api/auth/logout")
    unified_login = client.post("/api/auth/unified-login", json={"email": "root@ayv.pe", "password": "clave-segura-1"})
    assert unified_login.status_code == 200
    assert unified_login.json()["redirect_to"] == "/"
    assert client.get("/api/dashboard").status_code == 200
    assert client.get("/api/agents").status_code == 200

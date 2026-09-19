from sqlalchemy import select

from app.models import Agency, Client, User
from app.security import hash_password
from conftest import TestingSession


def seed_account(*, agency_name: str, agency_slug: str, email: str, is_vendiq_admin: bool):
    with TestingSession() as db:
        agency = Agency(name=agency_name, slug=agency_slug)
        db.add(agency)
        db.flush()
        user = User(
            agency_id=agency.id,
            name=agency_name,
            email=email,
            password_hash=hash_password("contrasena-segura"),
            role="admin",
            is_vendiq_admin=is_vendiq_admin,
        )
        company = Client(
            agency_id=agency.id,
            name=agency_name,
            portal_slug=f"{agency_slug}-company",
            portal_email=None,
        )
        db.add_all([user, company])
        db.commit()
        db.refresh(company)
        return company.id


def login(client, email: str):
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "contrasena-segura"},
    )
    assert response.status_code == 200


def test_global_admin_can_see_and_create_clients(client):
    seed_account(
        agency_name="AYV Administración",
        agency_slug="ayv-administracion",
        email="admin@ayv.pe",
        is_vendiq_admin=True,
    )
    login(client, "admin@ayv.pe")

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["is_vendiq_admin"] is True

    listed = client.get("/api/clients")
    assert listed.status_code == 200
    assert [row["name"] for row in listed.json()] == ["AYV Administración"]

    created = client.post("/api/clients", json={"name": "Garante"})
    assert created.status_code == 201
    assert created.json()["name"] == "Garante"
    assert {row["name"] for row in client.get("/api/clients").json()} == {"AYV Administración", "Garante"}


def test_pyme_is_limited_to_current_company_and_cannot_create_clients(client):
    foreign_id = seed_account(
        agency_name="Empresa Global",
        agency_slug="empresa-global",
        email="global@ayv.pe",
        is_vendiq_admin=True,
    )
    own_id = seed_account(
        agency_name="Garante",
        agency_slug="garante",
        email="owner@garante.pe",
        is_vendiq_admin=False,
    )
    with TestingSession() as db:
        own = db.get(Client, own_id)
        sibling = Client(
            agency_id=own.agency_id,
            name="Empresa hermana",
            portal_slug="empresa-hermana",
            portal_email=None,
        )
        db.add(sibling)
        db.commit()
        db.refresh(sibling)
        sibling_id = sibling.id
    login(client, "owner@garante.pe")

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["is_vendiq_admin"] is False

    current = client.get("/api/clients/current")
    assert current.status_code == 200
    assert current.json()["id"] == str(own_id)
    assert current.json()["name"] == "Garante"

    listed = client.get("/api/clients")
    assert listed.status_code == 200
    assert [row["name"] for row in listed.json()] == ["Garante"]
    assert client.get(f"/api/clients/{sibling_id}").status_code == 404
    assert client.get(f"/api/clients/{foreign_id}").status_code == 404

    forbidden = client.post("/api/clients", json={"name": "Otra empresa"})
    assert forbidden.status_code == 403

    client.post("/api/auth/logout")
    login(client, "owner@garante.pe")

    with TestingSession() as db:
        assert db.scalar(select(Client).where(Client.name == "Otra empresa")) is None
        user = db.scalar(select(User).where(User.email == "owner@garante.pe"))
        assert user is not None and user.is_vendiq_admin is False

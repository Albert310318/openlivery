from datetime import timedelta

from sqlalchemy import select

from app.models import Agency, Client, User, now_utc
from app.security import hash_password, verify_password
from app.services import portal_verification
from app.services.email import EmailDeliveryError
from conftest import TestingSession


GENERIC_RECOVERY_MESSAGE = "Si el correo está registrado, recibirás un código de recuperación."


def create_vendiq_admin(email="root@ayv.pe"):
    with TestingSession() as db:
        agency = Agency(name="AYV Administración", slug="ayv-administracion")
        db.add(agency)
        db.flush()
        user = User(
            agency_id=agency.id,
            name="Admin AYV",
            email=email,
            password_hash=hash_password("contrasena-anterior"),
            role="admin",
            is_vendiq_admin=True,
        )
        db.add(user)
        db.commit()
        return user.id


def test_verified_account_can_recover_password_without_account_enumeration(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        portal_verification,
        "send_verification_email",
        lambda email, code: sent.append((email, code)),
    )
    payload = {
        "agency_name": "Empresa Demo",
        "name": "Ana Responsable",
        "email": "ana@empresa-demo.pe",
        "password": "contrasena-anterior",
    }
    assert client.post("/api/auth/register", json=payload).status_code == 201
    registration_code = sent[-1][1]
    assert client.post(
        "/api/portal/email-verification/confirm", json={"code": registration_code}
    ).status_code == 200
    client.post("/api/auth/logout")

    unknown = client.post(
        "/api/auth/password-recovery/request",
        json={"email": "no-existe@example.com"},
    )
    known = client.post(
        "/api/auth/password-recovery/request",
        json={"email": payload["email"]},
    )
    assert unknown.status_code == known.status_code == 200
    assert unknown.json() == known.json()
    recovery_code = sent[-1][1]
    with TestingSession() as db:
        user = db.scalar(select(User).where(User.email == payload["email"]))
        company = db.scalar(select(Client).where(Client.portal_email == payload["email"]))
        assert user is not None and company is not None
        assert verify_password(recovery_code, company.portal_verification_code_hash)

    assert client.post(
        "/api/auth/password-recovery/confirm", json={"code": recovery_code}
    ).status_code == 200
    reset = client.post(
        "/api/auth/password-recovery/reset",
        json={"new_password": "contrasena-nueva", "confirm_password": "contrasena-nueva"},
    )
    assert reset.status_code == 200
    assert reset.json()["message"] == "Contraseña actualizada correctamente."
    assert not client.cookies.get(portal_verification.COOKIE)

    assert client.post(
        "/api/auth/login",
        json={"email": payload["email"], "password": "contrasena-anterior"},
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": payload["email"], "password": "contrasena-nueva"},
    ).status_code == 200


def test_vendiq_admin_without_client_receives_recovery_email(client, monkeypatch):
    email = "root@ayv.pe"
    user_id = create_vendiq_admin(email)
    sent = []
    monkeypatch.setattr(
        portal_verification,
        "send_verification_email",
        lambda recipient, code: sent.append((recipient, code)),
    )

    response = client.post("/api/auth/password-recovery/request", json={"email": email})

    assert response.status_code == 200
    assert response.json() == {"message": GENERIC_RECOVERY_MESSAGE}
    assert response.cookies.get(portal_verification.COOKIE)
    assert sent and sent[0][0] == email
    with TestingSession() as db:
        user = db.get(User, user_id)
        assert user is not None and user.is_vendiq_admin is True
        assert db.scalar(select(Client.id).where(Client.agency_id == user.agency_id)) is None
        assert verify_password(sent[0][1], user.password_recovery_code_hash)
        assert user.password_recovery_expires_at is not None
        assert user.password_recovery_attempts == 0


def test_vendiq_admin_code_confirms_resets_password_and_cannot_be_reused(client, monkeypatch):
    email = "root@ayv.pe"
    user_id = create_vendiq_admin(email)
    sent = []
    monkeypatch.setattr(
        portal_verification,
        "send_verification_email",
        lambda recipient, code: sent.append((recipient, code)),
    )
    assert client.post("/api/auth/password-recovery/request", json={"email": email}).status_code == 200
    reset_token = client.cookies.get(portal_verification.COOKIE)
    code = sent[-1][1]

    confirmed = client.post("/api/auth/password-recovery/confirm", json={"code": code})
    assert confirmed.status_code == 200
    assert confirmed.json() == {"status": "code_verified"}
    verified_token = client.cookies.get(portal_verification.COOKIE)
    with TestingSession() as db:
        user = db.get(User, user_id)
        assert user.password_recovery_code_hash is None
        assert user.password_recovery_expires_at is None

    client.cookies.set(portal_verification.COOKIE, reset_token, path="/api")
    replay = client.post("/api/auth/password-recovery/confirm", json={"code": code})
    assert replay.status_code == 401
    client.cookies.set(portal_verification.COOKIE, verified_token, path="/api")

    reset = client.post(
        "/api/auth/password-recovery/reset",
        json={"new_password": "contrasena-nueva", "confirm_password": "contrasena-nueva"},
    )
    assert reset.status_code == 200
    assert reset.json()["message"] == "Contraseña actualizada correctamente."
    assert client.post(
        "/api/auth/password-recovery/reset",
        json={"new_password": "otra-contrasena", "confirm_password": "otra-contrasena"},
        cookies={portal_verification.COOKIE: verified_token},
    ).status_code == 401
    with TestingSession() as db:
        user = db.get(User, user_id)
        assert verify_password("contrasena-nueva", user.password_hash)
        assert user.password_recovery_code_hash is None
    assert client.post(
        "/api/auth/login",
        json={"email": email, "password": "contrasena-nueva"},
    ).status_code == 200


def test_vendiq_admin_recovery_code_expires(client, monkeypatch):
    email = "root@ayv.pe"
    user_id = create_vendiq_admin(email)
    sent = []
    monkeypatch.setattr(
        portal_verification,
        "send_verification_email",
        lambda recipient, code: sent.append((recipient, code)),
    )
    assert client.post("/api/auth/password-recovery/request", json={"email": email}).status_code == 200
    with TestingSession() as db:
        user = db.get(User, user_id)
        user.password_recovery_expires_at = now_utc() - timedelta(seconds=1)
        db.commit()

    expired = client.post("/api/auth/password-recovery/confirm", json={"code": sent[-1][1]})
    assert expired.status_code == 400
    assert "vencido" in expired.json()["detail"].lower()


def test_vendiq_admin_recovery_code_attempt_limit(client, monkeypatch):
    email = "root@ayv.pe"
    user_id = create_vendiq_admin(email)
    sent = []
    monkeypatch.setattr(
        portal_verification,
        "send_verification_email",
        lambda recipient, code: sent.append((recipient, code)),
    )
    assert client.post("/api/auth/password-recovery/request", json={"email": email}).status_code == 200
    wrong_code = "000000" if sent[-1][1] != "000000" else "000001"

    for _ in range(4):
        assert client.post("/api/auth/password-recovery/confirm", json={"code": wrong_code}).status_code == 400
    locked = client.post("/api/auth/password-recovery/confirm", json={"code": wrong_code})
    assert locked.status_code == 429
    with TestingSession() as db:
        assert db.get(User, user_id).password_recovery_attempts == 5
    assert client.post("/api/auth/password-recovery/confirm", json={"code": sent[-1][1]}).status_code == 429


def test_unknown_recovery_email_returns_generic_response_without_sending(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        portal_verification,
        "send_verification_email",
        lambda recipient, code: sent.append((recipient, code)),
    )

    response = client.post(
        "/api/auth/password-recovery/request",
        json={"email": "no-existe@example.com"},
    )

    assert response.status_code == 200
    assert response.json() == {"message": GENERIC_RECOVERY_MESSAGE}
    assert sent == []
    assert not response.cookies.get(portal_verification.COOKIE)


def test_vendiq_admin_smtp_failure_is_indistinguishable_from_unknown_email(client, monkeypatch):
    email = "root@ayv.pe"
    user_id = create_vendiq_admin(email)
    sent = []

    def fail_send(recipient, code):
        sent.append(recipient)
        raise EmailDeliveryError("SMTP unavailable")

    monkeypatch.setattr(portal_verification, "send_verification_email", fail_send)
    known = client.post("/api/auth/password-recovery/request", json={"email": email})
    unknown = client.post("/api/auth/password-recovery/request", json={"email": "unknown@example.com"})

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == {"message": GENERIC_RECOVERY_MESSAGE}
    assert sent == [email]
    assert not client.cookies.get(portal_verification.COOKIE)
    with TestingSession() as db:
        user = db.get(User, user_id)
        assert user.password_recovery_code_hash is None
        assert user.password_recovery_expires_at is None


def test_pending_account_cannot_start_password_recovery(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        portal_verification,
        "send_verification_email",
        lambda email, code: sent.append((email, code)),
    )
    email = "pendiente@empresa.pe"
    assert client.post(
        "/api/auth/register",
        json={
            "agency_name": "Empresa Pendiente",
            "name": "Responsable",
            "email": email,
            "password": "contrasena-segura",
        },
    ).status_code == 201
    assert len(sent) == 1
    response = client.post("/api/auth/password-recovery/request", json={"email": email})
    assert response.status_code == 200
    assert response.json() == {"message": "Si el correo está registrado, recibirás un código de recuperación."}
    assert len(sent) == 1

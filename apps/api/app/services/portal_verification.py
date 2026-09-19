"""Email verification and password recovery challenges for Clients and Users."""
import math
import secrets
import uuid
from datetime import timedelta

import jwt
from fastapi import HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Client, User, now_utc
from ..security import hash_password, verify_password
from .email import EmailDeliveryError, send_verification_email

COOKIE = "portal_verification_token"
PORTAL_TOKEN_TYPE = "portal_verification"
REGISTRATION_TOKEN_TYPE = "registration_verification"
PASSWORD_RESET_TOKEN_TYPE = "password_reset"
PASSWORD_RESET_VERIFIED_TOKEN_TYPE = "password_reset_verified"
USER_PASSWORD_RESET_TOKEN_TYPE = "user_password_reset"
USER_PASSWORD_RESET_VERIFIED_TOKEN_TYPE = "user_password_reset_verified"


def _retry_after(last_sent_at, window_started_at, send_count: int) -> int:
    now = now_utc()
    waits = [0]
    if last_sent_at:
        waits.append(math.ceil((last_sent_at + timedelta(seconds=60) - now).total_seconds()))
    if window_started_at and send_count >= 5:
        waits.append(math.ceil((window_started_at + timedelta(hours=1) - now).total_seconds()))
    return max(waits)


def retry_after(client: Client) -> int:
    return _retry_after(
        client.portal_verification_last_sent_at,
        client.portal_verification_send_window_started_at,
        client.portal_verification_send_count,
    )


def retry_after_user(user: User) -> int:
    return _retry_after(
        user.password_recovery_last_sent_at,
        user.password_recovery_send_window_started_at,
        user.password_recovery_send_count,
    )


def issue_code(db: Session, client: Client) -> None:
    wait = retry_after(client)
    if wait:
        raise HTTPException(429, "Límite de envíos alcanzado. Espera antes de reenviar.", headers={"Retry-After": str(wait)})
    now = now_utc()
    if not client.portal_verification_send_window_started_at or now >= client.portal_verification_send_window_started_at + timedelta(hours=1):
        client.portal_verification_send_window_started_at = now
        client.portal_verification_send_count = 0
    old_hash = client.portal_verification_code_hash
    code = f"{secrets.randbelow(1_000_000):06d}"
    while old_hash and verify_password(code, old_hash):
        code = f"{secrets.randbelow(1_000_000):06d}"
    client.portal_verification_code_hash = hash_password(code)
    client.portal_verification_expires_at = now + timedelta(minutes=10)
    client.portal_verification_attempts = 0
    client.portal_verification_last_sent_at = now
    client.portal_verification_send_count += 1
    # Flush before SMTP; the row lock serializes resends and confirmation.
    db.flush()
    try:
        send_verification_email(client.portal_email, code)
    except EmailDeliveryError:
        client.portal_verification_code_hash = None
        client.portal_verification_expires_at = None
        db.commit()
        raise HTTPException(503, "Correo guardado, pendiente de verificación. No se pudo enviar el código; vuelve a intentar el reenvío más tarde.") from None
    db.commit()


def recovery_user(db: Session, email: str) -> User | None:
    # A User may not have a corresponding Client row (for example the global
    # AYV administrator). Regular agency users with a verified owner Client
    # continue to use the existing Client-backed recovery path.
    user = db.scalar(
        select(User)
        .where(func.lower(User.email) == email)
        .with_for_update()
    )
    if not user:
        return None
    # If a Client row owns this email, the Client flow is authoritative. This
    # also preserves the existing behavior for pending registrations.
    client_collision = db.scalar(
        select(Client.id).where(
            Client.agency_id == user.agency_id,
            func.lower(Client.portal_email) == email,
        ).limit(1)
    )
    if client_collision:
        return None
    return user


def issue_user_code(db: Session, user: User) -> None:
    wait = retry_after_user(user)
    if wait:
        raise HTTPException(429, "Límite de envíos alcanzado. Espera antes de reenviar.", headers={"Retry-After": str(wait)})
    now = now_utc()
    if (
        not user.password_recovery_send_window_started_at
        or now >= user.password_recovery_send_window_started_at + timedelta(hours=1)
    ):
        user.password_recovery_send_window_started_at = now
        user.password_recovery_send_count = 0
    code = f"{secrets.randbelow(1_000_000):06d}"
    while user.password_recovery_code_hash and verify_password(code, user.password_recovery_code_hash):
        code = f"{secrets.randbelow(1_000_000):06d}"
    user.password_recovery_code_hash = hash_password(code)
    user.password_recovery_expires_at = now + timedelta(minutes=10)
    user.password_recovery_attempts = 0
    user.password_recovery_last_sent_at = now
    user.password_recovery_send_count += 1
    db.flush()
    try:
        send_verification_email(user.email, code)
    except EmailDeliveryError:
        user.password_recovery_code_hash = None
        user.password_recovery_expires_at = None
        db.commit()
        raise HTTPException(503, "No se pudo enviar el código; vuelve a intentar más tarde.") from None
    db.commit()


def recovery_client(db: Session, email: str) -> Client | None:
    return db.scalar(
        select(Client)
        .join(User, User.agency_id == Client.agency_id, isouter=False)
        .where(
            Client.portal_email == email,
            Client.portal_enabled.is_(False),
            Client.portal_email_verified_at.is_not(None),
            User.email == email,
            User.is_vendiq_admin.is_(False),
        )
        .with_for_update()
    )


def pending_session(response: Response, client: Client, token_type: str = PORTAL_TOKEN_TYPE) -> dict:
    settings = get_settings()
    token = jwt.encode({"sub": str(client.id), "agency_id": str(client.agency_id),
                        "version": client.portal_credentials_version, "type": token_type,
                        "exp": now_utc() + timedelta(minutes=30)}, settings.secret_key, algorithm="HS256")
    response.set_cookie(COOKIE, token, httponly=True, secure=settings.cookie_secure,
                        samesite=settings.cookie_samesite, max_age=1800, path="/api")
    response.delete_cookie("portal_access_token", path="/")
    if token_type == REGISTRATION_TOKEN_TYPE:
        response.delete_cookie("access_token", path="/")
    local, domain = client.portal_email.rsplit("@", 1)
    return {"status": "verification_required", "masked_email": f"{local[0]}***@{domain}", "retry_after": retry_after(client)}


def pending_user_session(response: Response, user: User, token_type: str = USER_PASSWORD_RESET_TOKEN_TYPE) -> dict:
    settings = get_settings()
    token = jwt.encode(
        {
            "sub": str(user.id),
            "agency_id": str(user.agency_id),
            "version": user.password_recovery_credentials_version,
            "type": token_type,
            "exp": now_utc() + timedelta(minutes=30),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=1800,
        path="/api",
    )
    response.delete_cookie("portal_access_token", path="/")
    return {"message": "Si el correo está registrado, recibirás un código de recuperación."}


def verification_client(db: Session, token: str | None) -> Client:
    try:
        payload = jwt.decode(token or "", get_settings().secret_key, algorithms=["HS256"], options={"require": ["exp", "sub"]})
        token_type = payload.get("type")
        if token_type not in {
            PORTAL_TOKEN_TYPE,
            REGISTRATION_TOKEN_TYPE,
            PASSWORD_RESET_TOKEN_TYPE,
            PASSWORD_RESET_VERIFIED_TOKEN_TYPE,
        }:
            raise ValueError()
        client_id, agency_id = uuid.UUID(payload["sub"]), uuid.UUID(payload["agency_id"])
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise HTTPException(401, "Vuelve a iniciar sesión para verificar el correo.") from None
    client = db.scalar(select(Client).where(Client.id == client_id, Client.agency_id == agency_id).with_for_update())
    is_registration = payload.get("type") == REGISTRATION_TOKEN_TYPE
    is_password_reset = payload.get("type") in {PASSWORD_RESET_TOKEN_TYPE, PASSWORD_RESET_VERIFIED_TOKEN_TYPE}
    registration_user = db.scalar(
        select(User).where(User.agency_id == client.agency_id, User.email == client.portal_email)
    ) if client and client.portal_email and is_registration else None
    recovery_user = db.scalar(
        select(User).where(
            User.agency_id == client.agency_id,
            User.email == client.portal_email,
            User.is_vendiq_admin.is_(False),
        )
    ) if client and client.portal_email and is_password_reset else None
    if (
        not client
        or not client.portal_email
        or ((is_registration or not is_password_reset) and client.portal_email_verified_at)
        or payload.get("version") != client.portal_credentials_version
        or (is_registration and (client.portal_enabled or not registration_user))
        or (not is_registration and not is_password_reset and not client.portal_enabled)
        or (is_password_reset and (client.portal_enabled or not client.portal_email_verified_at or not recovery_user))
        or (is_password_reset and payload.get("type") == PASSWORD_RESET_TOKEN_TYPE and not client.portal_verification_code_hash)
    ):
        raise HTTPException(401, "Vuelve a iniciar sesión para verificar el correo.")
    return client


def verification_user(db: Session, token: str | None) -> User:
    try:
        payload = jwt.decode(
            token or "",
            get_settings().secret_key,
            algorithms=["HS256"],
            options={"require": ["exp", "sub", "agency_id", "version", "type"]},
        )
        token_type = payload["type"]
        if token_type not in {USER_PASSWORD_RESET_TOKEN_TYPE, USER_PASSWORD_RESET_VERIFIED_TOKEN_TYPE}:
            raise ValueError()
        user_id = uuid.UUID(payload["sub"])
        agency_id = uuid.UUID(payload["agency_id"])
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise HTTPException(401, "La sesión de recuperación no es válida.") from None

    user = db.scalar(
        select(User).where(User.id == user_id, User.agency_id == agency_id).with_for_update()
    )
    is_unverified_reset = token_type == USER_PASSWORD_RESET_TOKEN_TYPE
    if (
        not user
        or payload.get("version") != user.password_recovery_credentials_version
        or (
            is_unverified_reset
            and (not user.password_recovery_code_hash or not user.password_recovery_expires_at)
        )
    ):
        raise HTTPException(401, "La sesión de recuperación no es válida.")
    return user


def is_registration_token(token: str | None) -> bool:
    try:
        payload = jwt.decode(token or "", get_settings().secret_key, algorithms=["HS256"])
        return payload.get("type") == REGISTRATION_TOKEN_TYPE
    except jwt.PyJWTError:
        return False


def is_password_reset_token(token: str | None) -> bool:
    try:
        payload = jwt.decode(token or "", get_settings().secret_key, algorithms=["HS256"])
        return payload.get("type") in {PASSWORD_RESET_TOKEN_TYPE, PASSWORD_RESET_VERIFIED_TOKEN_TYPE}
    except jwt.PyJWTError:
        return False


def is_password_reset_verified_token(token: str | None) -> bool:
    try:
        payload = jwt.decode(token or "", get_settings().secret_key, algorithms=["HS256"])
        return payload.get("type") == PASSWORD_RESET_VERIFIED_TOKEN_TYPE
    except jwt.PyJWTError:
        return False


def is_user_password_reset_token(token: str | None) -> bool:
    try:
        payload = jwt.decode(token or "", get_settings().secret_key, algorithms=["HS256"])
        return payload.get("type") in {USER_PASSWORD_RESET_TOKEN_TYPE, USER_PASSWORD_RESET_VERIFIED_TOKEN_TYPE}
    except jwt.PyJWTError:
        return False


def is_user_password_reset_verified_token(token: str | None) -> bool:
    try:
        payload = jwt.decode(token or "", get_settings().secret_key, algorithms=["HS256"])
        return payload.get("type") == USER_PASSWORD_RESET_VERIFIED_TOKEN_TYPE
    except jwt.PyJWTError:
        return False


def confirm_code(db: Session, client: Client, code: str, *, mark_verified: bool = True) -> None:
    if client.portal_verification_attempts >= 5:
        raise HTTPException(429, "Límite de intentos alcanzado. Solicita un nuevo código.")
    if not client.portal_verification_code_hash or not client.portal_verification_expires_at or now_utc() >= client.portal_verification_expires_at:
        raise HTTPException(400, "Código vencido o no disponible. Solicita uno nuevo.")
    if not verify_password(code, client.portal_verification_code_hash):
        client.portal_verification_attempts += 1
        db.commit()
        if client.portal_verification_attempts >= 5:
            raise HTTPException(429, "Límite de intentos alcanzado. Solicita un nuevo código.")
        raise HTTPException(400, "Código incorrecto.")
    if mark_verified:
        client.portal_email_verified_at = now_utc()
    client.portal_verification_code_hash = None
    client.portal_verification_expires_at = None
    client.portal_verification_attempts = 0
    db.commit()


def confirm_user_code(db: Session, user: User, code: str) -> None:
    if user.password_recovery_attempts >= 5:
        raise HTTPException(429, "Límite de intentos alcanzado. Solicita un nuevo código.")
    if (
        not user.password_recovery_code_hash
        or not user.password_recovery_expires_at
        or now_utc() >= user.password_recovery_expires_at
    ):
        raise HTTPException(400, "Código vencido o no disponible. Solicita uno nuevo.")
    if not verify_password(code, user.password_recovery_code_hash):
        user.password_recovery_attempts += 1
        db.commit()
        if user.password_recovery_attempts >= 5:
            raise HTTPException(429, "Límite de intentos alcanzado. Solicita un nuevo código.")
        raise HTTPException(400, "Código incorrecto.")
    user.password_recovery_code_hash = None
    user.password_recovery_expires_at = None
    user.password_recovery_attempts = 0
    user.password_recovery_credentials_version += 1
    db.commit()

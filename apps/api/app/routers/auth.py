from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Agency, Client, User
from ..ratelimit import login_rate_limit, portal_verification_rate_limit
from ..schemas import (
    LoginRequest,
    PasswordRecoveryRequest,
    PasswordRecoveryReset,
    RegisterRequest,
    UnifiedLoginOut,
    UserOut,
    PortalVerificationPending,
    PortalVerificationConfirm,
)
from ..security import create_access_token, create_portal_token, hash_password, verify_password
from ..slugs import unique_slug
from ..services.leads import LeadCaptureError, normalize_phone
from ..services.portal_verification import (
    COOKIE,
    PASSWORD_RESET_TOKEN_TYPE,
    PASSWORD_RESET_VERIFIED_TOKEN_TYPE,
    REGISTRATION_TOKEN_TYPE,
    USER_PASSWORD_RESET_VERIFIED_TOKEN_TYPE,
    confirm_code,
    confirm_user_code,
    issue_code,
    issue_user_code,
    is_password_reset_token,
    is_password_reset_verified_token,
    is_user_password_reset_token,
    is_user_password_reset_verified_token,
    pending_session,
    pending_user_session,
    recovery_client,
    recovery_user,
    retry_after,
    retry_after_user,
    verification_client,
    verification_user,
)


router = APIRouter(prefix="/auth", tags=["Authentication"])


def _set_session_cookie(response: Response, user: User) -> None:
    settings = get_settings()
    response.set_cookie(
        key="access_token",
        value=create_access_token(str(user.id)),
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.access_token_minutes * 60,
        path="/",
    )
    response.delete_cookie("portal_access_token", path="/")


def _set_portal_session_cookie(response: Response, client: Client) -> None:
    settings = get_settings()
    response.set_cookie(
        key="portal_access_token",
        value=create_portal_token(str(client.id), client.portal_slug, client.portal_credentials_version),
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.access_token_minutes * 60,
        path="/",
    )
    response.delete_cookie("access_token", path="/")


def _registration_open(db: Session) -> bool:
    if get_settings().allow_multi_agency:
        return True
    return db.scalar(select(Agency.id).limit(1)) is None


@router.get("/status", dependencies=[Depends(login_rate_limit)])
def auth_status(db: Session = Depends(get_db)):
    # Public: lets the login page decide between first-run setup (no agency
    # yet) and sign-in only (single-agency instance already configured).
    has_agency = db.scalar(select(Agency.id).limit(1)) is not None
    return {
        "needs_setup": not has_agency,
        "registration_open": get_settings().allow_multi_agency or not has_agency,
    }


@router.post(
    "/register",
    response_model=UserOut | PortalVerificationPending,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(login_rate_limit)],
)
def register(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    if not _registration_open(db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is closed on this instance",
        )
    email = payload.email.lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(status_code=409, detail="A user with that email already exists")
    if db.scalar(select(Client.id).where(func.lower(Client.portal_email) == email)):
        raise HTTPException(status_code=409, detail="That email is already assigned to a client portal")
    try:
        whatsapp = normalize_phone(payload.whatsapp)
    except LeadCaptureError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    agency_name = payload.agency_name.strip()
    agency = Agency(name=agency_name, slug=unique_slug(db, Agency, "slug", agency_name))
    db.add(agency)
    db.flush()
    user = User(
        agency_id=agency.id,
        name=payload.name.strip(),
        email=email,
        password_hash=hash_password(payload.password),
        role="admin",
        is_vendiq_admin=False,
    )
    client = Client(
            agency_id=agency.id,
            name=agency_name,
            industry=payload.industry.strip(),
            sales_advisor_phone=whatsapp,
            portal_slug=unique_slug(db, Client, "portal_slug", agency_name),
            # The existing client verification fields are reused for the
            # owner registration challenge. This is not a portal: it stays
            # disabled until the owner verifies the address.
            portal_email=email,
            portal_enabled=False,
        )
    db.add_all([user, client])
    db.commit()
    db.refresh(user)
    issue_code(db, client)
    return pending_session(response, client, REGISTRATION_TOKEN_TYPE)


@router.post("/login", response_model=UserOut | PortalVerificationPending, dependencies=[Depends(login_rate_limit)])
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    pending_client = db.scalar(select(Client).where(
        Client.agency_id == user.agency_id,
        func.lower(Client.portal_email) == user.email,
        Client.portal_enabled.is_(False),
        Client.portal_email_verified_at.is_(None),
    ))
    if pending_client:
        return pending_session(response, pending_client, REGISTRATION_TOKEN_TYPE)
    _set_session_cookie(response, user)
    return user


@router.post("/unified-login", response_model=UnifiedLoginOut | PortalVerificationPending, dependencies=[Depends(login_rate_limit)])
def unified_login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    email = payload.email.lower()
    user = db.scalar(select(User).where(User.email == email))
    portal_clients = db.scalars(select(Client).where(
        func.lower(Client.portal_email) == email,
        Client.portal_enabled.is_(True),
    ).with_for_update()).all()

    if len(portal_clients) > 1 or (user and portal_clients):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    if user and verify_password(payload.password, user.password_hash):
        pending_client = db.scalar(select(Client).where(
            Client.agency_id == user.agency_id,
            func.lower(Client.portal_email) == email,
            Client.portal_enabled.is_(False),
            Client.portal_email_verified_at.is_(None),
        ))
        if pending_client:
            return pending_session(response, pending_client, REGISTRATION_TOKEN_TYPE)
        _set_session_cookie(response, user)
        return {"principal_type": "admin", "redirect_to": "/"}

    if len(portal_clients) == 1:
        client = portal_clients[0]
        if client.portal_enabled and client.portal_password_hash and verify_password(payload.password, client.portal_password_hash):
            if not client.portal_email_verified_at:
                return pending_session(response, client)
            _set_portal_session_cookie(response, client)
            return {"principal_type": "portal", "redirect_to": f"/portal/{client.portal_slug}"}

    raise HTTPException(status_code=401, detail="Incorrect email or password")


_PASSWORD_RECOVERY_MESSAGE = "Si el correo está registrado, recibirás un código de recuperación."


@router.post("/password-recovery/request", dependencies=[Depends(portal_verification_rate_limit)])
def request_password_recovery(
    payload: PasswordRecoveryRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    response.delete_cookie(COOKIE, path="/api")
    client = recovery_client(db, payload.email.lower())
    if client:
        # Registration/portal verification uses the same challenge columns.
        # Once that challenge has been consumed, permit the first password
        # recovery request immediately; active recovery challenges remain
        # subject to the normal resend cooldown.
        if client.portal_verification_code_hash is None:
            client.portal_verification_last_sent_at = None
        client.portal_credentials_version += 1
        try:
            issue_code(db, client)
            pending_session(response, client, PASSWORD_RESET_TOKEN_TYPE)
        except HTTPException:
            # Keep the response identical for unknown, throttled and delivery
            # failure cases so the endpoint cannot enumerate accounts.
            db.rollback()
    else:
        user = recovery_user(db, payload.email.lower())
        if user:
            user.password_recovery_credentials_version += 1
            try:
                issue_user_code(db, user)
                pending_user_session(response, user)
            except HTTPException:
                # Preserve the same response for unknown accounts, throttling,
                # and delivery failures.
                db.rollback()
    return {"message": _PASSWORD_RECOVERY_MESSAGE}


@router.post("/password-recovery/resend", dependencies=[Depends(portal_verification_rate_limit)])
def resend_password_recovery(
    response: Response,
    portal_verification_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if is_user_password_reset_token(portal_verification_token):
        if is_user_password_reset_verified_token(portal_verification_token):
            raise HTTPException(status_code=401, detail="La sesión de recuperación no es válida.")
        user = verification_user(db, portal_verification_token)
        user.password_recovery_credentials_version += 1
        issue_user_code(db, user)
        pending_user_session(response, user)
        return {"status": "sent", "retry_after": retry_after_user(user)}

    client = verification_client(db, portal_verification_token)
    if not is_password_reset_token(portal_verification_token) or is_password_reset_verified_token(portal_verification_token):
        raise HTTPException(status_code=401, detail="La sesión de recuperación no es válida.")
    issue_code(db, client)
    pending_session(response, client, PASSWORD_RESET_TOKEN_TYPE)
    return {"status": "sent", "retry_after": retry_after(client)}


@router.post("/password-recovery/confirm", dependencies=[Depends(portal_verification_rate_limit)])
def confirm_password_recovery(
    payload: PortalVerificationConfirm,
    response: Response,
    portal_verification_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if is_user_password_reset_token(portal_verification_token):
        if is_user_password_reset_verified_token(portal_verification_token):
            raise HTTPException(status_code=401, detail="La sesión de recuperación no es válida.")
        user = verification_user(db, portal_verification_token)
        confirm_user_code(db, user, payload.code)
        pending_user_session(response, user, USER_PASSWORD_RESET_VERIFIED_TOKEN_TYPE)
        return {"status": "code_verified"}

    if not is_password_reset_token(portal_verification_token) or is_password_reset_verified_token(portal_verification_token):
        raise HTTPException(status_code=401, detail="La sesión de recuperación no es válida.")
    client = verification_client(db, portal_verification_token)
    confirm_code(db, client, payload.code, mark_verified=False)
    pending_session(response, client, PASSWORD_RESET_VERIFIED_TOKEN_TYPE)
    return {"status": "code_verified"}


@router.post("/password-recovery/reset", dependencies=[Depends(portal_verification_rate_limit)])
def reset_password(
    payload: PasswordRecoveryReset,
    response: Response,
    portal_verification_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if is_user_password_reset_verified_token(portal_verification_token):
        user = verification_user(db, portal_verification_token)
        if payload.new_password != payload.confirm_password:
            raise HTTPException(status_code=422, detail="Las contraseñas no coinciden.")
        user.password_hash = hash_password(payload.new_password)
        user.password_recovery_code_hash = None
        user.password_recovery_expires_at = None
        user.password_recovery_attempts = 0
        user.password_recovery_credentials_version += 1
        db.commit()
        response.delete_cookie(COOKIE, path="/api")
        return {"status": "password_updated", "message": "Contraseña actualizada correctamente."}

    if not is_password_reset_verified_token(portal_verification_token):
        raise HTTPException(status_code=401, detail="La sesión de recuperación no es válida.")
    client = verification_client(db, portal_verification_token)
    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=422, detail="Las contraseñas no coinciden.")
    user = db.scalar(select(User).where(
        User.agency_id == client.agency_id,
        User.email == client.portal_email,
        User.is_vendiq_admin.is_(False),
    ))
    if not user:
        raise HTTPException(status_code=401, detail="La sesión de recuperación no es válida.")
    user.password_hash = hash_password(payload.new_password)
    client.portal_credentials_version += 1
    client.portal_verification_code_hash = None
    client.portal_verification_expires_at = None
    client.portal_verification_attempts = 0
    db.commit()
    response.delete_cookie(COOKIE, path="/api")
    return {"status": "password_updated", "message": "Contraseña actualizada correctamente."}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response):
    response.delete_cookie("access_token", path="/")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import get_current_user
from ..models import Client, User, new_domain_token
from ..schemas import ClientCreate, ClientDomainOut, ClientDomainSet, ClientOut, ClientPortalUpdate, ClientUpdate
from ..security import hash_password
from ..services import dns as dns_service
from ..services.portal_verification import issue_code
from ..services.leads import LeadCaptureError, normalize_phone
from ..slugs import slugify, unique_slug


router = APIRouter(prefix="/clients", tags=["Clients"])


def _domain_out(client: Client) -> ClientDomainOut:
    if not client.portal_domain:
        return ClientDomainOut(domain=None, verified=False, txt_host=None, txt_value=None)
    return ClientDomainOut(
        domain=client.portal_domain,
        verified=client.portal_domain_verified,
        txt_host=dns_service.challenge_host(client.portal_domain),
        txt_value=client.portal_domain_token,
    )


def _client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    if not user.is_vendiq_admin:
        current = _current_client(db, user)
        if not current or current.id != client_id:
            raise HTTPException(status_code=404, detail="Client not found")
    client = db.scalar(
        select(Client)
        .options(selectinload(Client.agents))
        .where(Client.id == client_id, Client.agency_id == user.agency_id)
    )
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def _require_vendiq_admin(user: User) -> None:
    if not user.is_vendiq_admin:
        raise HTTPException(status_code=403, detail="AYV global administrator required")


def _current_client(db: Session, user: User) -> Client | None:
    return db.scalar(
        select(Client)
        .options(selectinload(Client.agents))
        .where(Client.agency_id == user.agency_id)
        .order_by(Client.created_at.asc())
        .limit(1)
    )


@router.get("/current", response_model=ClientOut)
def current_client(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _current_client(db, user)
    if not client:
        raise HTTPException(status_code=404, detail="Current company not found")
    return client


@router.get("", response_model=list[ClientOut])
def list_clients(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # A Pyme is scoped to its single company. Keep the endpoint compatible for
    # existing callers, but never expose sibling companies in the same agency.
    if not user.is_vendiq_admin:
        client = _current_client(db, user)
        return [client] if client else []
    return db.scalars(
        select(Client)
        .options(selectinload(Client.agents))
        .where(Client.agency_id == user.agency_id)
        .order_by(Client.created_at.desc())
    ).all()


@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
def create_client(payload: ClientCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _require_vendiq_admin(user)
    client = Client(
        agency_id=user.agency_id,
        portal_slug=unique_slug(db, Client, "portal_slug", payload.name),
        **payload.model_dump(),
    )
    db.add(client)
    db.commit()
    return _client(db, user, client.id)


@router.get("/{client_id}", response_model=ClientOut)
def get_client(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _client(db, user, client_id)


@router.patch("/{client_id}", response_model=ClientOut)
def update_client(client_id: uuid.UUID, payload: ClientUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    values = payload.model_dump(exclude_unset=True)
    if "sales_advisor_phone" in values:
        try:
            values["sales_advisor_phone"] = normalize_phone(values["sales_advisor_phone"])
        except LeadCaptureError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    for key, value in values.items():
        setattr(client, key, value)
    db.commit()
    return _client(db, user, client_id)


@router.patch("/{client_id}/portal", response_model=ClientOut)
def update_client_portal(
    client_id: uuid.UUID,
    payload: ClientPortalUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    client = db.scalar(select(Client).where(Client.id == client.id, Client.agency_id == user.agency_id).with_for_update().execution_options(populate_existing=True))
    old_email = client.portal_email
    values = payload.model_dump(exclude_unset=True)
    password = values.pop("portal_password", None)
    if "portal_email" in values and values["portal_email"]:
        portal_email = str(values["portal_email"]).lower()
        if db.scalar(select(User.id).where(func.lower(User.email) == portal_email)):
            raise HTTPException(status_code=409, detail="That email is unavailable")
        if db.scalar(select(Client.id).where(func.lower(Client.portal_email) == portal_email, Client.id != client.id)):
            raise HTTPException(status_code=409, detail="That email is unavailable")
        values["portal_email"] = portal_email
    if password:
        client.portal_password_hash = hash_password(password)
    if "portal_slug" in values and values["portal_slug"]:
        candidate = slugify(values["portal_slug"])
        existing = db.scalar(select(Client).where(Client.portal_slug == candidate, Client.id != client.id))
        if existing:
            raise HTTPException(status_code=409, detail="That portal URL is already in use")
        values["portal_slug"] = candidate
    for key, value in values.items():
        setattr(client, key, value)
    if client.portal_enabled and (not client.portal_email or not client.portal_password_hash):
        raise HTTPException(status_code=400, detail="Set an email and a password before enabling the portal")
    email_changed = client.portal_email != old_email
    if email_changed or password:
        client.portal_credentials_version += 1
    if email_changed:
        client.portal_email_verified_at = None
        client.portal_verification_code_hash = None
        client.portal_verification_expires_at = None
        client.portal_verification_attempts = 0
        # Save the new pending address even when sending is throttled or fails.
        if client.portal_email:
            try:
                issue_code(db, client)
            except HTTPException:
                db.commit()
                raise
    db.commit()
    return _client(db, user, client_id)


@router.get("/{client_id}/domain", response_model=ClientDomainOut)
def get_client_domain(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _domain_out(_client(db, user, client_id))


@router.put("/{client_id}/domain", response_model=ClientDomainOut)
def set_client_domain(
    client_id: uuid.UUID,
    payload: ClientDomainSet,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    domain = payload.domain.strip().lower()
    taken = db.scalar(select(Client).where(Client.portal_domain == domain, Client.id != client.id))
    if taken:
        raise HTTPException(status_code=409, detail="That domain is already in use")
    # Re-assigning resets verification and issues a fresh challenge token.
    if client.portal_domain != domain or not client.portal_domain_token:
        client.portal_domain_token = new_domain_token()
    client.portal_domain = domain
    client.portal_domain_verified = False
    db.commit()
    return _domain_out(_client(db, user, client_id))


@router.post("/{client_id}/domain/verify", response_model=ClientDomainOut)
def verify_client_domain(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    if not client.portal_domain:
        raise HTTPException(status_code=400, detail="Add a domain before verifying it")
    if not dns_service.txt_contains(client.portal_domain, client.portal_domain_token):
        raise HTTPException(status_code=400, detail="The verification TXT record was not found yet. DNS can take a while to propagate.")
    client.portal_domain_verified = True
    db.commit()
    return _domain_out(_client(db, user, client_id))


@router.delete("/{client_id}/domain", response_model=ClientDomainOut)
def delete_client_domain(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    client.portal_domain = None
    client.portal_domain_verified = False
    client.portal_domain_token = ""
    db.commit()
    return _domain_out(_client(db, user, client_id))


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    db.delete(client)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

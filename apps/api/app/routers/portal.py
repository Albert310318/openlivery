import uuid

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from ..config import get_settings
from ..database import get_db
from ..models import Agency, Agent, Client, Conversation, Lead, Message, User, WhatsAppChannel, WhatsAppCloudChannel, now_utc
from ..ratelimit import login_rate_limit, portal_verification_rate_limit
from ..schemas import (
    PortalVerificationPending,
    PortalVerificationConfirm,
    UnifiedLoginOut,
    AgentSummary,
    ConversationDetail,
    ConversationModeUpdate,
    ConversationOut,
    PortalLoginRequest,
    PortalPublicOut,
    PortalChannelOut,
    PortalSessionOut,
    PortalSummaryOut,
    SendMessageRequest,
)
from ..security import create_portal_token, decode_portal_token, verify_password
from ..services.whatsapp import send_channel_message
from ..schemas_leads import LeadOut
from ..services.portal_verification import pending_session, verification_client, issue_code, confirm_code, is_registration_token, COOKIE
from .auth import _set_portal_session_cookie, _set_session_cookie


router = APIRouter(prefix="/portal", tags=["Client portal"])


def _public_client(db: Session, slug: str) -> Client:
    client = db.scalar(select(Client).where(Client.portal_slug == slug, Client.portal_enabled.is_(True)))
    if not client:
        raise HTTPException(status_code=404, detail="Portal not found or disabled")
    return client


def _portal_client(
    slug: str,
    portal_access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Client:
    if not portal_access_token:
        raise HTTPException(status_code=401, detail="Sign in to the portal")
    payload = decode_portal_token(portal_access_token)
    if not payload or payload.get("portal_slug") != slug:
        raise HTTPException(status_code=401, detail="The portal session expired")
    try:
        client_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid portal session") from exc
    client = db.scalar(select(Client).where(Client.id == client_id, Client.portal_slug == slug, Client.portal_enabled.is_(True)))
    if not client or not client.portal_email_verified_at or payload.get("credentials_version") != client.portal_credentials_version:
        raise HTTPException(status_code=401, detail="The portal is no longer available")
    return client


def _detail(db: Session, client: Client, conversation_id: uuid.UUID) -> Conversation:
    conversation = db.scalar(
        select(Conversation)
        .options(selectinload(Conversation.messages), joinedload(Conversation.agent))
        .execution_options(populate_existing=True)
        .where(Conversation.id == conversation_id, Conversation.client_id == client.id)
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.post("/email-verification/confirm", response_model=UnifiedLoginOut, dependencies=[Depends(portal_verification_rate_limit)])
def verify_portal_email(payload: PortalVerificationConfirm, response: Response,
                        portal_verification_token: str | None = Cookie(default=None), db: Session = Depends(get_db)):
    client = verification_client(db, portal_verification_token)
    confirm_code(db, client, payload.code)
    if is_registration_token(portal_verification_token):
        user = db.scalar(select(User).where(User.agency_id == client.agency_id, User.email == client.portal_email))
        if not user:
            raise HTTPException(401, "Vuelve a iniciar sesión para verificar el correo.")
        _set_session_cookie(response, user)
        response.delete_cookie(COOKIE, path="/api")
        return {"principal_type": "admin", "redirect_to": "/onboarding"}
    _set_portal_session_cookie(response, client)
    response.delete_cookie(COOKIE, path="/api")
    return {"principal_type": "portal", "redirect_to": f"/portal/{client.portal_slug}"}


@router.post("/email-verification/resend", dependencies=[Depends(portal_verification_rate_limit)])
def resend_portal_email(portal_verification_token: str | None = Cookie(default=None), db: Session = Depends(get_db)):
    client = verification_client(db, portal_verification_token)
    issue_code(db, client)
    return {"status": "sent", "retry_after": 60}


@router.get("/{slug}", response_model=PortalPublicOut)
def public_portal(slug: str, db: Session = Depends(get_db)):
    client = _public_client(db, slug)
    agency = db.get(Agency, client.agency_id)
    return {
        "client_name": client.name,
        "portal_title": client.portal_title or f"{client.name} Inbox",
        "portal_slug": client.portal_slug,
        "agency_name": agency.name,
        "agency_brand_color": agency.brand_color,
        "agency_logo_url": f"/api/portal/{slug}/logo" if agency.logo_data else None,
    }


@router.get("/{slug}/logo")
def public_logo(slug: str, db: Session = Depends(get_db)):
    client = _public_client(db, slug)
    agency = db.get(Agency, client.agency_id)
    if not agency.logo_data or not agency.logo_mime:
        raise HTTPException(status_code=404, detail="Logo not found")
    return Response(content=agency.logo_data, media_type=agency.logo_mime, headers={"Cache-Control": "no-store"})


@router.post("/{slug}/login", response_model=PortalSessionOut | PortalVerificationPending, dependencies=[Depends(login_rate_limit)])
def portal_login(slug: str, payload: PortalLoginRequest, response: Response, db: Session = Depends(get_db)):
    client = _public_client(db, slug)
    if (
        not client.portal_email
        or payload.email.lower() != client.portal_email.lower()
        or not client.portal_password_hash
        or not verify_password(payload.password, client.portal_password_hash)
    ):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not client.portal_email_verified_at:
        return pending_session(response, client)
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
    agency = db.get(Agency, client.agency_id)
    return {"client_id": client.id, "client_name": client.name, "portal_slug": client.portal_slug, "agency_name": agency.name}


@router.post("/{slug}/logout", status_code=status.HTTP_204_NO_CONTENT)
def portal_logout(response: Response):
    response.delete_cookie("portal_access_token", path="/")


@router.get("/{slug}/me", response_model=PortalSessionOut)
def portal_me(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    agency = db.get(Agency, client.agency_id)
    return {"client_id": client.id, "client_name": client.name, "portal_slug": client.portal_slug, "agency_name": agency.name}


@router.get("/{slug}/summary", response_model=PortalSummaryOut)
def portal_summary(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    agents = db.scalar(select(func.count(Agent.id)).where(Agent.client_id == client.id)) or 0
    active_agents = db.scalar(select(func.count(Agent.id)).where(Agent.client_id == client.id, Agent.is_active.is_(True))) or 0
    conversations = db.scalar(select(func.count(Conversation.id)).where(Conversation.client_id == client.id)) or 0
    leads = db.scalar(select(func.count(Lead.id)).where(Lead.client_id == client.id)) or 0
    channel_rows = db.execute(
        select(WhatsAppChannel.status).where(WhatsAppChannel.client_id == client.id)
    ).all() + db.execute(
        select(WhatsAppCloudChannel.status).where(WhatsAppCloudChannel.client_id == client.id)
    ).all()
    return {
        "client_name": client.name,
        "industry": client.industry,
        "description": client.description,
        "is_active": client.is_active,
        "agents": agents,
        "active_agents": active_agents,
        "conversations": conversations,
        "leads": leads,
        "channels": len(channel_rows),
        "connected_channels": sum(status == "connected" for status, in channel_rows),
    }


@router.get("/{slug}/leads", response_model=list[LeadOut])
def portal_leads(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return db.scalars(select(Lead).where(Lead.client_id == client.id).order_by(Lead.updated_at.desc()).limit(100)).all()


@router.get("/{slug}/channels", response_model=list[PortalChannelOut])
def portal_channels(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    baileys = db.scalars(select(WhatsAppChannel).where(WhatsAppChannel.client_id == client.id)).all()
    cloud = db.scalars(select(WhatsAppCloudChannel).where(WhatsAppCloudChannel.client_id == client.id)).all()
    return [
        {"type": channel_type, "status": channel.status, "display_name": channel.display_name,
         "phone_number": channel.phone_number, "is_enabled": channel.is_enabled}
        for channel_type, channels in (("whatsapp", baileys), ("whatsapp_cloud", cloud))
        for channel in channels
    ]


@router.get("/{slug}/agents", response_model=list[AgentSummary])
def portal_agents(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return db.scalars(select(Agent).where(Agent.client_id == client.id).order_by(Agent.name)).all()


@router.get("/{slug}/conversations", response_model=list[ConversationOut])
def portal_conversations(slug: str, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    ranked = select(
        Message.conversation_id.label("cid"),
        Message.content.label("content"),
        func.row_number().over(partition_by=Message.conversation_id, order_by=Message.created_at.desc()).label("rn"),
    ).subquery()
    last = select(ranked).where(ranked.c.rn == 1).subquery()
    rows = db.execute(
        select(Conversation, last.c.content)
        .outerjoin(last, last.c.cid == Conversation.id)
        .where(Conversation.client_id == client.id)
        .order_by(Conversation.updated_at.desc())
    ).all()
    return [
        ConversationOut.model_validate(conv).model_copy(update={"preview": (content or "")[:140].strip()})
        for conv, content in rows
    ]


@router.get("/{slug}/conversations/{conversation_id}", response_model=ConversationDetail)
def portal_conversation(slug: str, conversation_id: uuid.UUID, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return _detail(db, client, conversation_id)


@router.patch("/{slug}/conversations/{conversation_id}/mode", response_model=ConversationDetail)
def portal_mode(
    slug: str,
    conversation_id: uuid.UUID,
    payload: ConversationModeUpdate,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    conversation = _detail(db, client, conversation_id)
    conversation.mode = payload.mode
    conversation.updated_at = now_utc()
    db.commit()
    return _detail(db, client, conversation_id)


@router.post("/{slug}/conversations/{conversation_id}/reply", response_model=ConversationDetail)
async def portal_reply(
    slug: str,
    conversation_id: uuid.UUID,
    payload: SendMessageRequest,
    client: Client = Depends(_portal_client),
    db: Session = Depends(get_db),
):
    conversation = _detail(db, client, conversation_id)
    if conversation.mode != "human":
        raise HTTPException(status_code=409, detail="Take control of the conversation before replying")
    external_message_id = await send_channel_message(db, conversation, payload.content.strip())
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=payload.content.strip(),
            sender_type="human",
            sender_name=client.name,
            external_message_id=external_message_id,
        )
    )
    conversation.updated_at = now_utc()
    db.commit()
    return _detail(db, client, conversation_id)

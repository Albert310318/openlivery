import base64
import binascii
import hmac
import json
import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Agent, Client, Conversation, Message, User, WhatsAppChannel, now_utc
from ..schemas import (
    WhatsAppChannelOut,
    WhatsAppChannelUpdate,
    WhatsAppActivationFailure,
    WhatsAppInbound,
    WhatsAppInboundResult,
    WhatsAppInternalAuth,
    WhatsAppInternalStatus,
    WhatsAppOutboundConfirm,
)
from ..security import decrypt_secret, encrypt_secret
from ..services.subscriptions import (
    TrialActivationError,
    confirm_activation_delivery,
    ensure_trial_started,
    resolve_activation_transport_failure,
)
from ..services.whatsapp import bridge_command
from ..services.whatsapp_inbound import InboundMessage, process_inbound


router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])
internal_router = APIRouter(prefix="/internal/whatsapp", tags=["WhatsApp internal"])


def _channel_for_user(db: Session, user: User, client_id: uuid.UUID) -> WhatsAppChannel:
    channel = db.scalar(
        select(WhatsAppChannel).where(
            WhatsAppChannel.client_id == client_id,
            WhatsAppChannel.agency_id == user.agency_id,
        )
    )
    if not channel:
        raise HTTPException(status_code=404, detail="This client does not have WhatsApp configured yet")
    return channel


def _public_channel(channel: WhatsAppChannel) -> dict:
    qr_code = decrypt_secret(channel.encrypted_qr) if channel.encrypted_qr else None
    return {
        "id": channel.id,
        "client_id": channel.client_id,
        "agent_id": channel.agent_id,
        "status": channel.status,
        "phone_number": channel.phone_number,
        "display_name": channel.display_name,
        "qr_code": qr_code,
        "last_error": channel.last_error,
        "is_enabled": channel.is_enabled,
        "has_session": bool(channel.encrypted_auth_state),
        "last_connected_at": channel.last_connected_at,
        "created_at": channel.created_at,
        "updated_at": channel.updated_at,
    }


def _require_bridge(x_bridge_token: str | None = Header(default=None)) -> None:
    expected = get_settings().whatsapp_bridge_token
    if not x_bridge_token or not hmac.compare_digest(x_bridge_token, expected):
        raise HTTPException(status_code=401, detail="Invalid internal token")


def _internal_channel(db: Session, channel_id: uuid.UUID) -> WhatsAppChannel:
    channel = db.scalar(
        select(WhatsAppChannel)
        .options(joinedload(WhatsAppChannel.agent).joinedload(Agent.client))
        .where(WhatsAppChannel.id == channel_id)
    )
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    return channel


@router.get("/channels/{client_id}", response_model=WhatsAppChannelOut)
def get_channel(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _public_channel(_channel_for_user(db, user, client_id))


@router.put("/channels/{client_id}", response_model=WhatsAppChannelOut)
def configure_channel(
    client_id: uuid.UUID,
    payload: WhatsAppChannelUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = db.scalar(select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id))
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    agent = db.scalar(
        select(Agent).where(
            Agent.id == payload.agent_id,
            Agent.client_id == client.id,
            Agent.agency_id == user.agency_id,
        )
    )
    if not agent:
        raise HTTPException(status_code=400, detail="Select an agent that belongs to this client")
    channel = db.scalar(select(WhatsAppChannel).where(WhatsAppChannel.client_id == client.id))
    if channel:
        channel.agent_id = agent.id
        channel.is_enabled = True
    else:
        channel = WhatsAppChannel(agency_id=user.agency_id, client_id=client.id, agent_id=agent.id)
        db.add(channel)
    db.commit()
    db.refresh(channel)
    return _public_channel(channel)


@router.post("/channels/{client_id}/connect", response_model=WhatsAppChannelOut)
async def connect_channel(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    channel = _channel_for_user(db, user, client_id)
    channel.status = "connecting"
    channel.last_error = None
    channel.is_enabled = True
    db.commit()
    try:
        await bridge_command("POST", f"/channels/{channel.id}/connect")
    except HTTPException as exc:
        channel.status = "error"
        channel.last_error = exc.detail
        db.commit()
        raise
    db.refresh(channel)
    return _public_channel(channel)


@router.post("/channels/{client_id}/disconnect", response_model=WhatsAppChannelOut)
async def disconnect_channel(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    channel = _channel_for_user(db, user, client_id)
    await bridge_command("POST", f"/channels/{channel.id}/disconnect")
    db.refresh(channel)
    return _public_channel(channel)


@internal_router.get("/channels", dependencies=[Depends(_require_bridge)])
def restorable_channels(db: Session = Depends(get_db)):
    channels = db.scalars(
        select(WhatsAppChannel).where(
            WhatsAppChannel.is_enabled.is_(True),
            WhatsAppChannel.encrypted_auth_state.is_not(None),
        )
    ).all()
    return [{"id": str(item.id)} for item in channels]


@internal_router.get("/channels/{channel_id}", dependencies=[Depends(_require_bridge)])
def internal_channel(channel_id: uuid.UUID, db: Session = Depends(get_db)):
    channel = _internal_channel(db, channel_id)
    auth_state = json.loads(decrypt_secret(channel.encrypted_auth_state)) if channel.encrypted_auth_state else None
    return {
        "id": str(channel.id),
        "client_id": str(channel.client_id),
        "agent_id": str(channel.agent_id),
        "enabled": channel.is_enabled,
        "auth_state": auth_state,
    }


@internal_router.put("/channels/{channel_id}/auth", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(_require_bridge)])
def save_auth(channel_id: uuid.UUID, payload: WhatsAppInternalAuth, db: Session = Depends(get_db)):
    channel = _internal_channel(db, channel_id)
    channel.encrypted_auth_state = encrypt_secret(json.dumps(payload.auth_state, separators=(",", ":")))
    channel.updated_at = now_utc()
    db.commit()


@internal_router.delete("/channels/{channel_id}/auth", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(_require_bridge)])
def clear_auth(channel_id: uuid.UUID, db: Session = Depends(get_db)):
    channel = _internal_channel(db, channel_id)
    channel.encrypted_auth_state = None
    channel.encrypted_qr = None
    channel.phone_number = None
    channel.display_name = None
    channel.status = "disconnected"
    channel.last_error = None
    channel.is_enabled = False
    channel.updated_at = now_utc()
    db.commit()


@internal_router.put("/channels/{channel_id}/status", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(_require_bridge)])
def update_status(channel_id: uuid.UUID, payload: WhatsAppInternalStatus, db: Session = Depends(get_db)):
    channel = _internal_channel(db, channel_id)
    channel.status = payload.status
    if payload.phone_number is not None:
        channel.phone_number = payload.phone_number
    if payload.display_name is not None:
        channel.display_name = payload.display_name
    if payload.qr_code:
        channel.encrypted_qr = encrypt_secret(payload.qr_code)
    elif payload.status in {"connected", "disconnected", "error"}:
        channel.encrypted_qr = None
    channel.last_error = payload.error
    if payload.status == "connected":
        channel.last_connected_at = now_utc()
        channel.is_enabled = True
    channel.updated_at = now_utc()
    db.commit()


@internal_router.post(
    "/channels/{channel_id}/inbound",
    response_model=WhatsAppInboundResult,
    dependencies=[Depends(_require_bridge)],
)
async def inbound_message(channel_id: uuid.UUID, payload: WhatsAppInbound, db: Session = Depends(get_db)):
    channel = _internal_channel(db, channel_id)
    if not channel.is_enabled:
        raise HTTPException(status_code=409, detail="The channel is disconnected")

    media_bytes = None
    if payload.media_base64:
        try:
            media_bytes = base64.b64decode(payload.media_base64)
        except (binascii.Error, ValueError):
            media_bytes = None
    result = await process_inbound(
        db,
        channel,
        InboundMessage(
            external_message_id=payload.external_message_id,
            external_chat_id=payload.remote_jid,
            trusted_sender_jid=payload.trusted_sender_jid,
            message_timestamp=payload.message_timestamp,
            is_historical=payload.is_historical,
            sender_name=payload.sender_name,
            text=payload.text,
            media_kind=payload.media_kind,
            media_bytes=media_bytes,
            media_mime=payload.media_mime,
        ),
        conversation_channel="whatsapp",
        channel_fk_field="whatsapp_channel_id",
        activation_channel="whatsapp_qr",
    )
    return asdict(result)


@internal_router.post("/channels/{channel_id}/outbound-confirm", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(_require_bridge)])
def confirm_outbound(channel_id: uuid.UUID, payload: WhatsAppOutboundConfirm, db: Session = Depends(get_db)):
    if payload.delivery_id is not None:
        if payload.accepted_at is None:
            raise HTTPException(status_code=409, detail="Activation callback requires accepted_at")
        try:
            confirm_activation_delivery(
                db,
                delivery_id=payload.delivery_id,
                message_id=payload.message_id,
                whatsapp_channel_id=channel_id,
                external_message_id=payload.external_message_id,
                accepted_at=payload.accepted_at,
            )
            ensure_trial_started(db, payload.delivery_id)
        except TrialActivationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return
    message = db.scalar(
        select(Message)
        .join(Conversation)
        .where(Message.id == payload.message_id, Conversation.whatsapp_channel_id == channel_id)
    )
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    message.external_message_id = payload.external_message_id
    db.commit()


@internal_router.post("/channels/{channel_id}/activation-failure", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(_require_bridge)])
def activation_failure(channel_id: uuid.UUID, payload: WhatsAppActivationFailure, db: Session = Depends(get_db)):
    try:
        resolve_activation_transport_failure(
            db,
            delivery_id=payload.delivery_id,
            message_id=payload.message_id,
            whatsapp_channel_id=channel_id,
            state=payload.state,
        )
    except TrialActivationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

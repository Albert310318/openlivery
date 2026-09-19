"""Channel-agnostic inbound WhatsApp pipeline.

Shared by the Baileys bridge endpoint and the Cloud API webhook: dedupe by
external message id, find or create the conversation, resolve media into text,
store the visitor message, and produce the AI reply unless a human operator has
taken over. The caller is responsible for actually delivering the reply.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import trial_activation_eligible_since
from ..models import Agent, Conversation, Message, now_utc
from .knowledge import build_system_prompt, retrieve_knowledge
from .leads import ensure_whatsapp_contact_lead, lead_context_from_conversation
from .media import describe_image, transcribe_audio
from .providers import resolve_agent_credentials, resolve_provider_credentials
from .subscriptions import prepare_activation_delivery
from .tools import run_completion
from .usage import record_usage

logger = logging.getLogger(__name__)


@dataclass
class InboundMessage:
    external_message_id: str
    external_chat_id: str
    trusted_sender_jid: str | None = None
    sender_name: str | None = None
    text: str = ""
    message_timestamp: datetime | None = None
    is_historical: bool = False
    media_kind: str | None = None
    media_bytes: bytes | None = None
    media_mime: str | None = None


@dataclass
class InboundResult:
    accepted: bool
    reply: str | None = None
    conversation_id: uuid.UUID | None = None
    mode: str | None = None
    outbound_message_id: uuid.UUID | None = None
    delivery_id: uuid.UUID | None = None


LUCIA_NEW_SESSION_AFTER = timedelta(hours=12)
LUCIA_INTRO = "Hola, soy Lucía, asesora virtual de Chiclayo Tours 😊"


def _automation_block_reason(inbound: InboundMessage) -> str | None:
    if inbound.is_historical:
        return "historical_event"
    cutoff = trial_activation_eligible_since()
    if cutoff is None:
        return "missing_or_invalid_cutoff"
    if inbound.message_timestamp is None:
        return "missing_message_timestamp"
    if inbound.message_timestamp.tzinfo is None or inbound.message_timestamp.utcoffset() is None:
        return "invalid_message_timestamp"
    if inbound.message_timestamp.astimezone(cutoff.tzinfo) < cutoff:
        return "message_before_cutoff"
    return None


def _starts_new_lucia_session(conversation: Conversation | None, agent: Agent, received_at: datetime) -> bool:
    if agent.client.name != "Chiclayo Tours" or not agent.name.startswith("Lucía"):
        return False
    return conversation is None or received_at - conversation.updated_at >= LUCIA_NEW_SESSION_AFTER


def _with_lucia_session_intro(text: str, should_introduce: bool) -> str:
    if not should_introduce or "soy lucía" in text.casefold():
        return text
    return f"{LUCIA_INTRO}\n\n{text}"


def _media_placeholder(kind: str) -> str:
    return "[El cliente envió una imagen]" if kind == "image" else "[El cliente envió una nota de voz]"


async def _inbound_content(db: Session, agent: Agent, inbound: InboundMessage) -> str:
    """Resolve the effective user text, transcribing/describing media when the
    agent's capabilities allow it. Best-effort: falls back to a placeholder."""
    text = (inbound.text or "").strip()
    if not inbound.media_kind:
        return text
    if not inbound.media_bytes:
        return text or _media_placeholder(inbound.media_kind)
    enabled = (inbound.media_kind == "image" and agent.image_enabled) or (
        inbound.media_kind == "audio" and agent.audio_enabled
    )
    credentials = resolve_provider_credentials(db, agent.agency_id, "openai")
    if not enabled or not credentials:
        return text or _media_placeholder(inbound.media_kind)
    try:
        data = inbound.media_bytes
        base_url, api_key = credentials
        if inbound.media_kind == "image":
            model = agent.image_model.strip() or agent.model.strip()
            instruction = (
                "Describe con detalle el contenido de esta imagen para que un asistente pueda responder al cliente."
                + (f" El cliente escribió: {text}" if text else "")
            )
            description = await describe_image(base_url, api_key, model, data, inbound.media_mime or "image/jpeg", instruction)
            return (f"{text}\n\n" if text else "") + f"[Imagen recibida] {description}"
        model = agent.audio_model.strip() or "whisper-1"
        transcript = await transcribe_audio(base_url, api_key, model, data, "audio.ogg", inbound.media_mime or "audio/ogg")
        return (f"{text}\n\n" if text else "") + (transcript or _media_placeholder("audio"))
    except (HTTPException, ValueError):
        return text or _media_placeholder(inbound.media_kind)


async def process_inbound(
    db: Session,
    channel,
    inbound: InboundMessage,
    *,
    conversation_channel: str,
    channel_fk_field: str,
    activation_channel: str | None = None,
) -> InboundResult:
    """Run the shared pipeline for one inbound message.

    ``channel`` is a WhatsAppChannel or WhatsAppCloudChannel; both expose the
    same fields used here. ``conversation_channel`` and ``channel_fk_field``
    select the Conversation channel label and FK column for the caller.
    """
    fk_column = getattr(Conversation, channel_fk_field)
    received_at = now_utc()

    existing = db.scalar(
        select(Message)
        .join(Conversation)
        .where(
            fk_column == channel.id,
            Message.external_message_id == inbound.external_message_id,
        )
    )
    if existing:
        return InboundResult(accepted=False, conversation_id=existing.conversation_id)

    conversation = db.scalar(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(
            fk_column == channel.id,
            Conversation.external_chat_id == inbound.external_chat_id,
        )
    )
    new_lucia_session = _starts_new_lucia_session(conversation, channel.agent, received_at)
    if not conversation:
        title = (inbound.sender_name or inbound.external_chat_id.split("@")[0])[:240]
        conversation = Conversation(
            agency_id=channel.agency_id,
            client_id=channel.client_id,
            agent_id=channel.agent_id,
            external_chat_id=inbound.external_chat_id,
            contact_name=inbound.sender_name,
            title=title,
            channel=conversation_channel,
            **{channel_fk_field: channel.id},
        )
        db.add(conversation)
        db.flush()
    elif inbound.sender_name:
        conversation.contact_name = inbound.sender_name

    content = await _inbound_content(db, channel.agent, inbound)
    visitor_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=content,
        sender_type="visitor",
        sender_name=inbound.sender_name or "WhatsApp contact",
        external_message_id=inbound.external_message_id,
    )
    conversation.updated_at = now_utc()
    db.add(visitor_message)
    db.commit()
    blocked_reason = _automation_block_reason(inbound)
    if blocked_reason:
        logger.warning(
            "Inbound automation skipped: reason=%s external_message_id=%s",
            blocked_reason,
            inbound.external_message_id,
        )
        return InboundResult(
            accepted=True,
            conversation_id=conversation.id,
            mode="historical" if inbound.is_historical else "ineligible",
        )
    lead_context = lead_context_from_conversation(
        conversation,
        trusted_sender_jid=inbound.trusted_sender_jid,
    )
    try:
        ensure_whatsapp_contact_lead(db, lead_context)
        db.commit()
    except Exception:
        logger.exception("Could not ensure WhatsApp lead for conversation_id=%s", conversation.id)
        db.rollback()

    if conversation.mode == "human":
        return InboundResult(accepted=True, conversation_id=conversation.id, mode="human")

    agent = channel.agent
    credentials = resolve_agent_credentials(db, agent)
    if not agent.is_active or not credentials or not agent.model.strip():
        channel.last_error = "A message was received, but the assigned agent is not ready (model or provider key missing)."
        channel.updated_at = now_utc()
        db.commit()
        return InboundResult(accepted=True, conversation_id=conversation.id, mode="ai")

    knowledge = await retrieve_knowledge(db, agent, content)
    db.refresh(conversation)
    history = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(agent.memory_limit)
    ).all()
    history = list(reversed(history))
    messages = [
        {"role": "system", "content": build_system_prompt(agent, knowledge.text)},
        *[{"role": item.role, "content": item.content} for item in history],
    ]
    base_url, api_key = credentials
    try:
        completion = await run_completion(
            db,
            agent,
            base_url,
            api_key,
            messages,
            tool_context=lead_context,
            temperature=agent.temperature,
            max_tokens=agent.max_tokens,
        )
    except Exception as exc:
        channel.last_error = f"Message received, but the agent could not reply: {str(exc)[:400]}"
        channel.updated_at = now_utc()
        db.commit()
        return InboundResult(accepted=True, conversation_id=conversation.id, mode="ai")

    db.refresh(conversation)
    if conversation.mode == "human":
        return InboundResult(accepted=True, conversation_id=conversation.id, mode="human")

    reply_text = _with_lucia_session_intro(completion.text, new_lucia_session)
    outbound = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=reply_text,
        sources=knowledge.sources,
        tool_calls=completion.tool_calls,
        sender_type="ai",
        sender_name=agent.name,
    )
    record_usage(db, agent.agency_id, agent.id, agent.provider, agent.model.strip(), completion)
    conversation.updated_at = now_utc()
    channel.last_error = None
    db.add(outbound)
    delivery_id = None
    if activation_channel == "whatsapp_qr":
        # The assistant message and its PREPARED evidence must become visible
        # together, before the caller starts the network send.
        db.flush()
        delivery = prepare_activation_delivery(
            db,
            client_id=channel.client_id,
            message_id=outbound.id,
            whatsapp_channel_id=channel.id,
            recipient=inbound.external_chat_id,
            trusted_recipient=inbound.trusted_sender_jid,
            source_message_timestamp=inbound.message_timestamp,
        )
        db.add(delivery)
        db.flush()
        delivery_id = delivery.id
    db.commit()
    return InboundResult(
        accepted=True,
        reply=reply_text,
        conversation_id=conversation.id,
        mode="ai",
        outbound_message_id=outbound.id,
        delivery_id=delivery_id,
    )

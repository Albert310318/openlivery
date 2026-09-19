import re
import uuid
from dataclasses import dataclass

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Conversation, Lead, LeadConversation, WhatsAppChannel, WhatsAppCloudChannel, now_utc
from ..schemas_leads import LeadCaptureInput


_EMAIL_ADAPTER = TypeAdapter(EmailStr)
_LEAD_FIELDS = ("name", "phone", "email", "interest", "budget", "preferred_contact_time", "notes", "status")


class LeadCaptureError(ValueError):
    pass


class LeadIdentityConflict(LeadCaptureError):
    pass


class LeadIdentityRequired(LeadCaptureError):
    pass


@dataclass(frozen=True)
class LeadContext:
    agency_id: uuid.UUID
    client_id: uuid.UUID
    agent_id: uuid.UUID
    conversation_id: uuid.UUID
    channel: str
    trusted_sender_jid: str | None = None


@dataclass
class LeadCaptureResult:
    lead: Lead
    created: bool
    updated_fields: list[str]


def lead_context_from_conversation(
    conversation: Conversation,
    *,
    trusted_sender_jid: str | None = None,
) -> LeadContext:
    return LeadContext(
        agency_id=conversation.agency_id,
        client_id=conversation.client_id,
        agent_id=conversation.agent_id,
        conversation_id=conversation.id,
        channel=conversation.channel,
        trusted_sender_jid=trusted_sender_jid,
    )


def normalize_email(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    candidate = value.strip().lower()
    try:
        return str(_EMAIL_ADAPTER.validate_python(candidate)).lower()
    except ValidationError as exc:
        raise LeadCaptureError("The email address is not valid") from exc


def normalize_phone(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    candidate = value.strip()
    if not re.fullmatch(r"\+?[0-9().\s-]+", candidate):
        raise LeadCaptureError("The phone number is not valid")
    has_plus = candidate.startswith("+")
    digits = re.sub(r"\D", "", candidate)
    if not 7 <= len(digits) <= 15:
        raise LeadCaptureError("The phone number must contain between 7 and 15 digits")
    return f"+{digits}" if has_plus else digits


def _validated_conversation(db: Session, context: LeadContext) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == context.conversation_id,
            Conversation.agency_id == context.agency_id,
            Conversation.client_id == context.client_id,
            Conversation.agent_id == context.agent_id,
            Conversation.channel == context.channel,
        )
    )
    if not conversation:
        raise LeadCaptureError("The lead context does not match the conversation")
    return conversation


def trusted_whatsapp_phone(db: Session, context: LeadContext) -> str | None:
    """Return the authenticated channel sender without treating it as model evidence."""
    conversation = _validated_conversation(db, context)
    external_chat_id = (conversation.external_chat_id or "").strip()
    if conversation.channel == "whatsapp" and conversation.whatsapp_channel_id:
        channel_model = WhatsAppChannel
        channel_id = conversation.whatsapp_channel_id
        suffix = "@s.whatsapp.net"
        if external_chat_id.endswith(suffix):
            sender = external_chat_id.removesuffix(suffix)
        elif external_chat_id.endswith("@lid") and context.trusted_sender_jid:
            alternate = context.trusted_sender_jid.strip()
            if not re.fullmatch(r"[0-9]{7,15}@s\.whatsapp\.net", alternate):
                return None
            sender = alternate.removesuffix(suffix)
        else:
            return None
    elif conversation.channel == "whatsapp_cloud" and conversation.whatsapp_cloud_channel_id:
        channel_model = WhatsAppCloudChannel
        channel_id = conversation.whatsapp_cloud_channel_id
        sender = external_chat_id
    else:
        return None

    channel = db.scalar(
        select(channel_model).where(
            channel_model.id == channel_id,
            channel_model.agency_id == context.agency_id,
            channel_model.client_id == context.client_id,
            channel_model.agent_id == context.agent_id,
        )
    )
    if not channel:
        return None
    try:
        return normalize_phone(sender)
    except LeadCaptureError:
        return None


def ensure_whatsapp_contact_lead(db: Session, context: LeadContext) -> tuple[Lead | None, bool]:
    """Create or reuse a lead for a trusted WhatsApp contact and link the conversation.

    Returns (lead, created) so callers can trigger one-time actions only for a
    genuinely new prospect record.
    """
    _validated_conversation(db, context)
    trusted_phone = trusted_whatsapp_phone(db, context)
    if not trusted_phone:
        return None, False

    linked = db.scalar(
        select(Lead)
        .join(LeadConversation)
        .where(
            LeadConversation.conversation_id == context.conversation_id,
            Lead.agency_id == context.agency_id,
            Lead.client_id == context.client_id,
        )
    )
    if linked:
        return linked, False

    lead = db.scalar(
        select(Lead).where(
            Lead.agency_id == context.agency_id,
            Lead.client_id == context.client_id,
            Lead.phone_normalized == trusted_phone,
        )
    )
    created = lead is None
    if created:
        lead = Lead(
            agency_id=context.agency_id,
            client_id=context.client_id,
            agent_id=context.agent_id,
            source=context.channel,
            phone=trusted_phone,
            phone_normalized=trusted_phone,
        )
        db.add(lead)
        db.flush()

    link = db.scalar(
        select(LeadConversation).where(LeadConversation.conversation_id == context.conversation_id)
    )
    if link is None:
        db.add(LeadConversation(lead_id=lead.id, conversation_id=context.conversation_id))
        db.flush()
    elif link.lead_id != lead.id:
        raise LeadIdentityConflict("The conversation is already associated with another lead")

    return lead, created

def create_or_update_lead(
    db: Session,
    context: LeadContext,
    payload: LeadCaptureInput,
) -> LeadCaptureResult:
    _validated_conversation(db, context)
    supplied = {name for name in _LEAD_FIELDS if name in payload.model_fields_set and getattr(payload, name) is not None}
    if not supplied:
        raise LeadCaptureError("At least one explicitly provided lead field is required")

    phone_normalized = normalize_phone(payload.phone) if "phone" in supplied else None
    email_normalized = normalize_email(payload.email) if "email" in supplied else None

    linked = db.scalar(
        select(Lead)
        .join(LeadConversation)
        .where(
            LeadConversation.conversation_id == context.conversation_id,
            Lead.agency_id == context.agency_id,
            Lead.client_id == context.client_id,
        )
    )

    trusted_phone = trusted_whatsapp_phone(db, context)
    if phone_normalized and trusted_phone and phone_normalized != trusted_phone:
        raise LeadIdentityConflict(
            "The provided phone and trusted WhatsApp sender identity point to different lead records"
        )
    if not phone_normalized and trusted_phone:
        phone_normalized = trusted_phone

    matches: list[Lead] = []
    identity_filters = []
    if phone_normalized:
        identity_filters.append(Lead.phone_normalized == phone_normalized)
    if email_normalized:
        identity_filters.append(Lead.email_normalized == email_normalized)
    if identity_filters:
        matches = list(
            db.scalars(
                select(Lead).where(
                    Lead.agency_id == context.agency_id,
                    Lead.client_id == context.client_id,
                    or_(*identity_filters),
                )
            ).all()
        )
    matched_ids = {item.id for item in matches}
    if len(matched_ids) > 1:
        raise LeadIdentityConflict("The provided phone and email belong to different lead records")
    identity_lead = matches[0] if matches else None
    if linked and identity_lead and linked.id != identity_lead.id:
        raise LeadIdentityConflict("The conversation and contact details point to different lead records")

    lead = linked or identity_lead
    created = lead is None
    if created:
        if not supplied.intersection({"name", "phone", "email"}) and not trusted_phone:
            raise LeadIdentityRequired("A new lead requires a name, phone number, or email address")
        lead = Lead(
            agency_id=context.agency_id,
            client_id=context.client_id,
            agent_id=context.agent_id,
            source=context.channel,
            phone=trusted_phone,
            phone_normalized=trusted_phone,
        )
        db.add(lead)
        db.flush()

    updated_fields: list[str] = []
    for field in supplied:
        value = getattr(payload, field)
        if getattr(lead, field) != value:
            setattr(lead, field, value)
            updated_fields.append(field)
    if "phone" in supplied and lead.phone_normalized != phone_normalized:
        lead.phone_normalized = phone_normalized
    if "email" in supplied and lead.email_normalized != email_normalized:
        lead.email_normalized = email_normalized

    link = db.scalar(select(LeadConversation).where(LeadConversation.conversation_id == context.conversation_id))
    if link and link.lead_id != lead.id:
        raise LeadIdentityConflict("The conversation is already associated with another lead")
    if not link:
        db.add(LeadConversation(lead_id=lead.id, conversation_id=context.conversation_id))

    lead.updated_at = now_utc()
    db.flush()
    return LeadCaptureResult(lead=lead, created=created, updated_fields=sorted(updated_fields))

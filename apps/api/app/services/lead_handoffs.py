import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Client, Conversation, Lead, LeadConversation, LeadHandoff, Message, WhatsAppChannel, now_utc
from .leads import LeadCaptureError, LeadContext, normalize_phone, trusted_whatsapp_phone
from .whatsapp import bridge_command


class LeadHandoffError(ValueError):
    pass


def _plain(value: str) -> str:
    import unicodedata

    value = "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _explicit_consent(text: str) -> bool:
    plain = _plain(text)
    if re.search(r"\b(no|nunca|tampoco|todavia no|aun no)\b", plain):
        return False
    positive = re.search(r"\b(si|claro|de acuerdo|acepto|quiero|pueden|puede)\b", plain)
    contact = re.search(r"\b(asesor|asesora|contacte|contacten|contactarme|contactenme|llame|llamen|llamarme|escriba|escriban|escribirme)\b", plain)
    return bool(positive and contact)


def _rejects_contact(text: str) -> bool:
    return bool(re.search(r"\b(no|nunca|tampoco|todavia no|aun no|no quiero|no puedo)\b", _plain(text)))


def _coordinates_advisor_contact(text: str) -> bool:
    plain = _plain(text)
    advisor = re.search(r"\b(asesor|asesora|comercial|vendedor|vendedora)\b", plain)
    contact = re.search(r"\b(contacte|contacten|contactarte|contactarlo|contactarla|llame|llamen|llamarte|escriba|escriban|escribirte)\b", plain)
    scheduling = re.search(r"\b(cuando|que dia|cual dia|en que horario|que horario|a que hora|por que medio|como prefieres)\b", plain)
    clear_continuation = re.search(
        r"\b(puede|pueden|podra|podran|continuar|continua|seguira|atendera|atenderte|ayudara|ayudarte)\b",
        plain,
    )
    attention = re.search(r"\b(atencion|contacto|contigo|tu caso|consulta|solicitud|detalles)\b", plain)
    return bool((contact and (advisor or scheduling)) or (advisor and clear_continuation and attention))


def _contextual_consent(text: str, previous_agent_text: str | None) -> bool:
    if not previous_agent_text or _rejects_contact(text) or not _coordinates_advisor_contact(previous_agent_text):
        return False
    plain = _plain(text)
    acceptance = re.search(r"\b(si|claro|de acuerdo|esta bien|ok|perfecto|pueden|puede)\b", plain)
    operational = re.search(
        r"\b(hoy|manana|tarde|tardes|noche|noches|mediodia|lunes|martes|miercoles|jueves|viernes|sabado|domingo|despues|partir|whatsapp|telefono|llamada|correo)\b|\b[0-9]{1,2}(?::[0-9]{2})?\b",
        plain,
    )
    return bool(acceptance or operational)


def handoff_available(db: Session, context: LeadContext) -> bool:
    if context.channel != "whatsapp":
        return False
    return db.scalar(
        select(Client.id)
        .join(WhatsAppChannel, WhatsAppChannel.client_id == Client.id)
        .join(Conversation, Conversation.whatsapp_channel_id == WhatsAppChannel.id)
        .where(
            Conversation.id == context.conversation_id,
            Conversation.agency_id == context.agency_id,
            Conversation.client_id == context.client_id,
            Conversation.agent_id == context.agent_id,
            Client.id == context.client_id,
            Client.agency_id == context.agency_id,
            Client.sales_advisor_phone.is_not(None),
            WhatsAppChannel.agency_id == context.agency_id,
            WhatsAppChannel.agent_id == context.agent_id,
            WhatsAppChannel.is_enabled.is_(True),
            WhatsAppChannel.status == "connected",
        )
    ) is not None


def _people_from_notes(notes: str | None) -> str | None:
    if not notes:
        return None
    category = r"(?:adultos?|adultas?|niños?|niñas?|bebés?|bebes?|menores?|infantes?|adolescentes?|jóvenes?|jovenes?|personas?)"
    member = rf"\d+\s+{category}"
    match = re.search(rf"\b({member}(?:\s*(?:,|y)?\s*{member})*)\b", notes, flags=re.IGNORECASE)
    if not match:
        return None
    members = re.findall(member, match.group(1), flags=re.IGNORECASE)
    return " y ".join(members)


def _valid_lead_phone(lead: Lead) -> str | None:
    for candidate in (lead.phone_normalized, lead.phone):
        try:
            normalized = normalize_phone(candidate)
        except LeadCaptureError:
            continue
        if normalized:
            return normalized
    return None


def _new_lead_message_text(client: Client, lead: Lead, initial_message: str) -> str:
    phone = _valid_lead_phone(lead) or "Por confirmar"
    name = (lead.name or "").strip() or "Por confirmar"
    inquiry = " ".join((initial_message or "").split())
    if len(inquiry) > 700:
        inquiry = inquiry[:697] + "..."
    lines = [
        f"🔔 Nuevo lead – {client.name}",
        "",
        f"Nombre: {name}",
        f"WhatsApp: {phone}",
    ]
    if inquiry:
        lines.append(f"Consulta inicial: {inquiry}")
    if lead.interest:
        lines.append(f"Interés: {lead.interest.strip()}")
    lines.extend([
        f"Estado: {lead.status}",
        "",
        "⏳ El agente IA continúa calificando al prospecto.",
        "Aún no implica autorización para que el asesor contacte al prospecto.",
    ])
    return "\n".join(lines)


async def notify_new_lead(
    db: Session,
    context: LeadContext,
    lead: Lead,
    initial_message: str,
) -> dict:
    """Notify the configured advisor once, immediately after a new WhatsApp lead is registered."""
    if lead.advisor_notified_at:
        return {"ok": True, "duplicate": True, "status": "sent"}

    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == context.conversation_id,
            Conversation.agency_id == context.agency_id,
            Conversation.client_id == context.client_id,
            Conversation.agent_id == context.agent_id,
            Conversation.channel == "whatsapp",
        )
    )
    client = db.scalar(
        select(Client).where(
            Client.id == context.client_id,
            Client.agency_id == context.agency_id,
        )
    )
    channel = None
    if conversation and conversation.whatsapp_channel_id:
        channel = db.scalar(
            select(WhatsAppChannel).where(
                WhatsAppChannel.id == conversation.whatsapp_channel_id,
                WhatsAppChannel.agency_id == context.agency_id,
                WhatsAppChannel.client_id == context.client_id,
                WhatsAppChannel.agent_id == context.agent_id,
                WhatsAppChannel.is_enabled.is_(True),
                WhatsAppChannel.status == "connected",
            )
        )

    if not client or not client.sales_advisor_phone or not channel:
        return {"ok": False, "skipped": True, "reason": "advisor_not_configured_or_channel_unavailable"}

    try:
        remote_jid = f"{client.sales_advisor_phone.lstrip('+')}@s.whatsapp.net"
        result = await bridge_command(
            "POST",
            f"/channels/{channel.id}/send",
            {
                "remote_jid": remote_jid,
                "text": _new_lead_message_text(client, lead, initial_message),
            },
        )
        lead.advisor_notified_at = now_utc()
        lead.advisor_notification_external_message_id = result.get("external_message_id")
        lead.advisor_notification_error = None
        db.commit()
        return {"ok": True, "duplicate": False, "status": "sent"}
    except HTTPException as exc:
        lead.advisor_notification_error = str(exc.detail)[:1000]
        db.commit()
        return {"ok": False, "duplicate": False, "status": "failed"}


def _message_text(lead: Lead, trusted_phone: str | None = None) -> str:
    lines = ["✅ Lead listo para contacto", ""]
    fields = (
        ("Nombre", lead.name),
        ("Teléfono", _valid_lead_phone(lead) or trusted_phone),
        ("Interés", lead.interest),
        ("Presupuesto", lead.budget),
        ("Horario preferido", lead.preferred_contact_time),
    )
    present = [(label, value.strip()) for label, value in fields if value and value.strip()]
    people = _people_from_notes(lead.notes)
    if people:
        present.append(("Personas", people))
    lines.extend(f"{label}: {value}" for label, value in present)
    summary_parts = [f"{label.lower()}: {value}" for label, value in present if label in {"Interés", "Presupuesto", "Horario preferido"}]
    if summary_parts:
        lines.extend(["", f"Resumen: {'; '.join(summary_parts)}."])
    lines.extend(["", "Consentimiento: el prospecto confirmó explícitamente que desea ser contactado."])
    return "\n".join(lines)


async def notify_sales_advisor(db: Session, context: LeadContext, consent_evidence: str) -> dict:
    if not handoff_available(db, context):
        raise LeadHandoffError("Sales advisor handoff is not configured or the WhatsApp QR channel is unavailable")
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == context.conversation_id,
            Conversation.agency_id == context.agency_id,
            Conversation.client_id == context.client_id,
            Conversation.agent_id == context.agent_id,
            Conversation.channel == "whatsapp",
        )
    )
    recent_messages = list(db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(2)
    ).all())
    last_user = recent_messages[0] if recent_messages and recent_messages[0].role == "user" else None
    previous_agent = recent_messages[1] if len(recent_messages) > 1 and recent_messages[1].role == "assistant" else None
    evidence = consent_evidence.strip()
    valid_consent = _explicit_consent(evidence) or _contextual_consent(
        evidence, previous_agent.content if previous_agent else None
    )
    if not last_user or not evidence or evidence not in last_user.content or not valid_consent:
        raise LeadHandoffError("Explicit or unequivocal contextual consent must be quoted from the prospect's latest message")
    lead = db.scalar(
        select(Lead).join(LeadConversation).where(
            LeadConversation.conversation_id == conversation.id,
            Lead.agency_id == context.agency_id,
            Lead.client_id == context.client_id,
            Lead.agent_id == context.agent_id,
        )
    )
    if not lead:
        raise LeadHandoffError("The conversation does not have a verified lead to hand off")
    client = db.scalar(select(Client).where(Client.id == context.client_id, Client.agency_id == context.agency_id))
    channel = db.scalar(
        select(WhatsAppChannel).where(
            WhatsAppChannel.id == conversation.whatsapp_channel_id,
            WhatsAppChannel.agency_id == context.agency_id,
            WhatsAppChannel.client_id == context.client_id,
            WhatsAppChannel.agent_id == context.agent_id,
        )
    )
    if not client or not client.sales_advisor_phone or not channel:
        raise LeadHandoffError("Sales advisor handoff configuration is invalid")
    existing = db.scalar(select(LeadHandoff).where(LeadHandoff.consent_message_id == last_user.id))
    if existing:
        return {"ok": existing.status == "sent", "duplicate": True, "status": existing.status}
    handoff = LeadHandoff(
        agency_id=context.agency_id, client_id=context.client_id, lead_id=lead.id,
        conversation_id=conversation.id, consent_message_id=last_user.id,
        advisor_phone=client.sales_advisor_phone,
    )
    db.add(handoff)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(LeadHandoff).where(LeadHandoff.consent_message_id == last_user.id))
        return {"ok": bool(existing and existing.status == "sent"), "duplicate": True, "status": existing.status if existing else "sending"}
    try:
        remote_jid = f"{client.sales_advisor_phone.lstrip('+')}@s.whatsapp.net"
        result = await bridge_command(
            "POST",
            f"/channels/{channel.id}/send",
            {"remote_jid": remote_jid, "text": _message_text(lead, trusted_whatsapp_phone(db, context))},
        )
        handoff.status = "sent"
        handoff.external_message_id = result.get("external_message_id")
        handoff.sent_at = now_utc()
        db.commit()
    except HTTPException as exc:
        handoff.status = "failed"
        handoff.error_message = str(exc.detail)[:1000]
        db.commit()
        raise LeadHandoffError("The advisor notification could not be delivered") from exc
    return {"ok": True, "duplicate": False, "status": "sent"}

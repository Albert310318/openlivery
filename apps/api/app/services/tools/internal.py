import json
import hashlib
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import Client, Lead, LeadConversation, Message, now_utc
from ...models_orders import PaymentNotification, RestaurantOrder
from ...models_restaurant import RestaurantPaymentMethod
from ...schemas_leads import LeadCaptureInput, LeadToolInput
from ..leads import (
    LeadCaptureError,
    LeadContext,
    LeadIdentityRequired,
    create_or_update_lead,
    trusted_whatsapp_phone,
    validate_lead_context,
)
from ..lead_handoffs import LeadHandoffError, notify_sales_advisor
from ..restaurant_orders import create_order, resolve_whatsapp_items
from ..calendar import CalendarError, availability, cancel_appointment, create_appointment, reschedule_appointment
from ..leads import budget_needs_clarification, notes_need_clarification


LEAD_CAPTURE_RULES = (
    "Lead capture rules: persisted lead data must come exclusively from facts explicitly stated by the prospect "
    "in user messages. Never use the assistant's recommendations, business knowledge, questions, inferences, or "
    "own statements as lead data. Make one consolidated create_or_update_lead call per user turn whenever possible. "
    "For every supplied data field, include in evidence one exact user quote or a list of exact user quotes that "
    "together support the full value. "
    "For interest and notes, use the supporting user quote verbatim as the field value; do not summarize, paraphrase, change grammatical person, or convert \"estoy/mi\" into \"está/su\". "
    "Only supply a lead field when the current user turn explicitly provides or corrects that field; omit previously known fields that the current turn does not change. A budget or request to be contacted is not interest. "
    "Keep notes literal or very close to the prospect's wording; never add labels "
    "or interpretations such as 'the prospect requests' or 'customer interested in'. Never invent or "
    "infer a name, phone, email, interest, budget, preferred contact time, or notes. Call create_or_update_lead "
    "again whenever the prospect provides new or corrected information. "
    "A new lead requires at least one explicitly provided identity field: name, phone, or email. Do not call "
    "create_or_update_lead for an anonymous prospect who provides only interest, budget, preferred contact time, "
    "or notes. Those fields may be sent without repeating identity only when a lead is already linked to the "
    "conversation. If the tool returns skipped=true, do not mention lead capture or storage; continue answering "
    "the prospect's original request normally. "
    "Never claim that data was registered, saved, or noted unless create_or_update_lead returned ok=true. If the "
    "tool returns an error, do not claim persistence. "
    "Never ask for, accept, expose, or provide internal agency, client, agent, conversation, or lead IDs."
)

TRUSTED_WHATSAPP_LEAD_RULE = (
    "This is a validated WhatsApp conversation and the server has the sender's trusted WhatsApp phone. "
    "It can satisfy identity when creating a new lead, so call create_or_update_lead for explicit interest, "
    "budget, preferred contact time, or notes even when the prospect did not state a name, phone, or email. "
    "Do not put that channel phone in the tool arguments or evidence, and do not ask the prospect to provide "
    "the same WhatsApp/phone number merely to create the lead. Ask for another contact number only when the "
    "prospect explicitly wants to provide one or business logic genuinely requires it."
)


LEAD_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Name exactly as explicitly provided by the prospect."},
        "phone": {"type": "string", "description": "Phone explicitly provided by the prospect."},
        "email": {"type": "string", "description": "Email explicitly provided by the prospect."},
        "interest": {"type": "string", "description": "Interest or need explicitly stated by the prospect."},
        "budget": {"type": "string", "description": "Budget exactly as stated, preserving range and currency."},
        "preferred_contact_time": {
            "type": "string",
            "description": "Preferred contact time exactly as stated by the prospect.",
        },
        "notes": {"type": "string", "description": "Relevant factual notes explicitly provided by the prospect."},
        "status": {"type": "string", "enum": ["new", "qualified", "follow_up"]},
        "evidence": {
            "type": "object",
            "description": (
                "Exact verbatim quotes from user/prospect messages supporting each supplied data field. "
                "Assistant messages are never valid evidence."
            ),
            "properties": {
                field: {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    ]
                }
                for field in ("name", "phone", "email", "interest", "budget", "preferred_contact_time", "notes")
            },
            "additionalProperties": False,
        },
    },
    "required": ["evidence"],
    "additionalProperties": False,
}

SALES_ADVISOR_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "consent_evidence": {
            "type": "string",
            "description": "Exact literal quote from the prospect's latest message explicitly or contextually accepting advisor contact.",
        },
    },
    "required": ["consent_evidence"],
    "additionalProperties": False,
}

CALENDAR_AVAILABILITY_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {"type": "string", "description": "Local date in YYYY-MM-DD."},
        "part_of_day": {
            "type": "string",
            "enum": ["morning", "afternoon", "evening"],
            "description": "Optional part of day requested by the prospect.",
        },
    },
    "required": ["date"],
    "additionalProperties": False,
}

CALENDAR_CREATE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "start_iso": {
            "type": "string",
            "description": "Exact confirmed appointment start in ISO 8601 including timezone offset.",
        },
        "confirmation_evidence": {
            "type": "string",
            "description": "Exact quote from the prospect's latest message confirming this appointment time.",
        },
    },
    "required": ["start_iso", "confirmation_evidence"],
    "additionalProperties": False,
}

CALENDAR_RESCHEDULE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "start_iso": {
            "type": "string",
            "description": "Exact new appointment start in ISO 8601 including timezone offset.",
        },
        "confirmation_evidence": {
            "type": "string",
            "description": "Exact quote from the prospect's latest message confirming the new appointment time.",
        },
    },
    "required": ["start_iso", "confirmation_evidence"],
    "additionalProperties": False,
}

CALENDAR_CANCEL_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "confirmation_evidence": {
            "type": "string",
            "description": "Exact quote from the prospect's latest message requesting appointment cancellation.",
        },
    },
    "required": ["confirmation_evidence"],
    "additionalProperties": False,
}

CALENDAR_RULES = (
    "Calendar rules: never invent availability. Use consultar_disponibilidad before offering appointment times. "
    "Offer only slots returned by the tool. Before crear_cita or reprogramar_cita, require an explicit prospect "
    "confirmation of one exact offered date and time; pass an exact quote from the latest prospect message as "
    "confirmation_evidence. Never create or move a booking from an ambiguous answer. Use cancelar_cita only when "
    "the prospect explicitly asks to cancel. After any successful calendar action, state the exact confirmed local "
    "date and time returned by the tool. If availability changed, apologize briefly and offer fresh slots."
)


def _latest_user_message(db: Session, context: LeadContext) -> Message | None:
    return db.scalar(
        select(Message)
        .where(Message.conversation_id == context.conversation_id, Message.role == "user")
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )


def _require_latest_user_evidence(db: Session, context: LeadContext, evidence: object) -> str:
    if not isinstance(evidence, str) or not evidence.strip():
        raise CalendarError("Exact confirmation evidence from the latest prospect message is required")
    latest = _latest_user_message(db, context)
    value = evidence.strip()
    if not latest or value not in latest.content:
        raise CalendarError("Calendar confirmation evidence must be quoted from the latest prospect message")
    return value


RESTAURANT_MENU_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Optional product/category search text."},
    },
    "additionalProperties": False,
}

RESTAURANT_ADD_ITEM_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "item_name": {"type": "string", "description": "Menu item name as requested by the customer."},
        "quantity": {"type": "integer", "minimum": 1, "maximum": 99},
        "notes": {"type": "string", "description": "Only explicit preparation notes from the customer."},
        "evidence": {"type": "string", "description": "Exact quote from the latest customer message supporting this addition."},
    },
    "required": ["item_name", "quantity", "evidence"],
    "additionalProperties": False,
}

RESTAURANT_REMOVE_ITEM_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "item_name": {"type": "string"},
        "quantity": {"type": "integer", "minimum": 1, "maximum": 99},
        "evidence": {"type": "string", "description": "Exact quote from the latest customer message supporting this change."},
    },
    "required": ["item_name", "evidence"],
    "additionalProperties": False,
}

RESTAURANT_DETAILS_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "customer_name": {"type": "string"},
        "fulfillment_type": {"type": "string", "enum": ["delivery", "table", "pickup"]},
        "table_label": {"type": "string"},
        "delivery_address": {"type": "string"},
        "evidence": {"type": "string", "description": "Exact quote from the latest customer message supporting the supplied details."},
    },
    "required": ["evidence"],
    "additionalProperties": False,
}

RESTAURANT_CONFIRM_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "confirmation_evidence": {
            "type": "string",
            "description": "Exact quote from the latest customer message explicitly confirming the complete order.",
        },
    },
    "required": ["confirmation_evidence"],
    "additionalProperties": False,
}

RESTAURANT_PAYMENT_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "method": {"type": "string", "description": "Payment method explicitly stated by the customer."},
        "reference": {"type": "string", "description": "Optional operation/reference explicitly provided by the customer."},
        "evidence": {
            "type": "string",
            "description": "Exact quote from the latest customer message saying payment was made/reported.",
        },
    },
    "required": ["method", "evidence"],
    "additionalProperties": False,
}

RESTAURANT_ORDER_RULES = (
    "Restaurant order rules: act as a waiter, not as a sales-lead qualifier. Never invent menu items, prices, "
    "discounts or totals. Use consultar_menu for menu/price questions and agregar_item_pedido for each customer "
    "addition; the server is the only authority for arithmetic and totals. After changes, use ver_pedido when needed "
    "and present a concise itemized summary. Before confirming the order, make sure delivery mode is known: delivery "
    "requires an address; table requires a table identifier. Ask only for missing order information. "
    "Do not mark an order paid merely because the customer says they paid. registrar_pago_reportado means payment "
    "is awaiting human verification. Never say payment is confirmed or that the order went to kitchen until the "
    "system status explicitly says so. A restaurant customer is an order/customer, not a CRM sales lead, so do not "
    "ask for budget or preferred advisor-contact time and do not offer sales-advisor handoff. "
)


SALES_ADVISOR_RULES = (
    "Sales advisor handoff rules: you may offer advisor contact when the prospect shows sufficient commercial "
    "intent. Call notify_sales_advisor after the prospect explicitly accepts being contacted, or when the immediately "
    "previous assistant message explicitly coordinates advisor contact and the prospect replies with an unequivocal "
    "contact time, day, or method. The same operational phrase without that immediately preceding context is not "
    "consent. A bare or ambiguous yes is otherwise insufficient. Pass only an exact literal quote from that latest prospect "
    "message as consent_evidence. Never provide or request internal IDs, destination numbers, lead fields, or a "
    "summary. If the current turn also contains new lead data, call create_or_update_lead first. Never claim that "
    "the advisor was notified unless the tool returns ok=true."
)

RESTAURANT_ORDER_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "modality": {"type": "string", "enum": ["dine_in", "pickup", "delivery"]},
        "customer_name": {"type": "string"},
        "customer_phone": {"type": "string"},
        "address": {"type": "string"},
        "address_reference": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_name": {"type": "string"},
                    "quantity": {"type": "integer", "minimum": 1},
                    "variant_names": {"type": "array", "items": {"type": "string"}},
                    "extra_names": {"type": "array", "items": {"type": "string"}},
                    "observations": {"type": "string"},
                },
                "required": ["product_name", "quantity"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["modality", "items"],
    "additionalProperties": False,
}

RESTAURANT_ORDER_RULES = (
    "Restaurant order rules: only call create_restaurant_order after the customer explicitly confirms the complete order. "
    "Use only product, variant, extra, modality and configured payment data present in the structured restaurant context. The configured payment number is the destination/receptor; the customer's origin bank or wallet is irrelevant and must not cause rejection. "
    "Never invent prices or availability. For delivery first collect name, phone, exact address, reference and any pending variants. "
    "For dine_in do not ask for an address. A digital payment or receipt never confirms payment automatically; the tool returns pending manual confirmation. "
    "After create_restaurant_order returns a pending_payment order, that tool call is not the end of the conversation: the very next response MUST say that the order is registered and pending payment, give the exact total, give the configured Datos para pagos (número para pagos and titular, plus instructions when present), request the abono and request the comprobante after paying. Never say the order is ready, that the customer can pick it up, or that the restaurant is waiting for them while payment_status is pending. "
    "If the customer says ok, thanks, or otherwise tries to close while a restaurant order is pending payment, do not say goodbye; remind them naturally that the abono and verification are still missing. "
    "When the customer reports a payment operation and amount, call report_restaurant_payment only with those explicit values. A voucher never confirms payment: the tool records the evidence and moves the order to manual payment review. Tell the customer that an administrator must review it. "
    "For delivery, charge only the product subtotal and tell the customer that delivery is not included and will be coordinated directly by the delivery staff. "
    "The tool result is the only confirmation that the order was created."
)

RESTAURANT_PAYMENT_REPORT_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "operation_id": {"type": "string", "minLength": 1, "maxLength": 180},
        "amount": {"type": "number", "exclusiveMinimum": 0},
    },
    "required": ["operation_id", "amount"],
    "additionalProperties": False,
}

_RESTAURANT_ORDER_CHANNELS = {"playground", "whatsapp", "whatsapp_cloud"}
_RESTAURANT_ORDER_ARGUMENTS = {
    "modality", "customer_name", "customer_phone", "address", "address_reference",
    "delivery_zone", "items", "payment_method",  # accepted only for old callers; ignored
}


_DATA_FIELDS = ("name", "phone", "email", "interest", "budget", "preferred_contact_time", "notes")
_STOP_WORDS = {
    "con", "del", "desde", "el", "ella", "en", "la", "las", "los", "para", "por", "que", "una", "uno",
    "unos", "unas", "the", "and", "for", "from", "with",
}
_TOKEN_EQUIVALENTS = (
    {"busco", "buscar", "buscando", "compra", "comprar"},
)
_FIELD_STRUCTURAL_TOKENS = {
    "interest": {"interes", "interesa", "interesado", "interesada"},
}


def _normalized_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9@.+]+", plain))


def _significant_tokens(value: str) -> set[str]:
    return {token for token in _normalized_text(value).split() if len(token) >= 3 and token not in _STOP_WORDS}


def _token_is_supported(token: str, evidence_tokens: set[str]) -> bool:
    # A five-character common prefix tolerates light inflection (comprar/compra)
    # without allowing new product or project names into the stored value.
    if any(token in group and evidence_tokens.intersection(group) for group in _TOKEN_EQUIVALENTS):
        return True
    return any(token == other or (len(token) >= 5 and len(other) >= 5 and token[:5] == other[:5]) for other in evidence_tokens)


def _validate_user_evidence(db: Session, context: LeadContext, payload: LeadToolInput) -> None:
    user_messages = list(
        db.scalars(
            select(Message.content).where(
                Message.conversation_id == context.conversation_id,
                Message.role == "user",
            )
        ).all()
    )
    normalized_messages = [_normalized_text(message) for message in user_messages]
    existing_lead = db.scalar(
        select(Lead)
        .join(LeadConversation)
        .where(
            LeadConversation.conversation_id == context.conversation_id,
            Lead.agency_id == context.agency_id,
            Lead.client_id == context.client_id,
        )
    )
    supplied = {
        field for field in _DATA_FIELDS
        if field in payload.model_fields_set and getattr(payload, field) is not None
    }
    errors: list[str] = []
    for field in _DATA_FIELDS:
        if field not in supplied:
            continue
        raw_evidence = payload.evidence.get(field)
        quotes = raw_evidence if isinstance(raw_evidence, list) else [raw_evidence] if raw_evidence else []
        quotes = [quote.strip() for quote in quotes if quote and quote.strip()]
        if not quotes:
            errors.append(f"missing user evidence for '{field}'")
            continue
        evidence_tokens: set[str] = set()
        invalid_quote = False
        for quote in quotes:
            normalized_quote = _normalized_text(quote)
            if not normalized_quote or not any(normalized_quote in message for message in normalized_messages):
                errors.append(f"evidence for '{field}' was not found in a user message")
                invalid_quote = True
                break
            evidence_tokens.update(_significant_tokens(quote))
        if invalid_quote:
            continue
        if field == "budget" and any(
            _normalized_text(str(getattr(payload, field))) in _normalized_text(quote)
            for quote in quotes
        ):
            continue
        # Values already persisted on this conversation passed this same evidence
        # gate previously, so they remain trusted when a later user turn adds more
        # explicit information without repeating every historical quote.
        if existing_lead is not None:
            existing_value = getattr(existing_lead, field)
            if existing_value is not None:
                evidence_tokens.update(_significant_tokens(str(existing_value)))
        structural_tokens = _FIELD_STRUCTURAL_TOKENS.get(field, set())
        unsupported = {
            token for token in _significant_tokens(str(getattr(payload, field)))
            if token not in structural_tokens and not _token_is_supported(token, evidence_tokens)
        }
        if unsupported:
            errors.append(f"the value for '{field}' contains information not supported by its user evidence")
    if errors:
        raise LeadCaptureError("; ".join(errors))


def enforce_lead_confirmation(text: str, tool_calls: list[dict]) -> str:
    """Never let a provider claim persistence when the last lead write failed."""
    lead_calls = [call for call in tool_calls if call.get("name") == "create_or_update_lead"]
    if lead_calls and lead_calls[-1].get("is_error"):
        return (
            "No pude guardar o actualizar los datos del prospecto en este momento. "
            "La información no quedó confirmada como registrada; por favor, inténtalo nuevamente."
        )
    return text


def _configured_payment_details(db: Session, client_id) -> dict | None:
    """Read the single customer-facing payment destination.

    The database still has the old payment-method table for compatibility with
    the onboarding API and historical orders.  The agent must never select a
    method from it: this helper only reads the active row containing the
    restaurant's canonical ``Datos para pagos``.  Existing installations may
    identify that row as ``other`` or by its display name.
    """

    rows = db.scalars(
        select(RestaurantPaymentMethod)
        .where(RestaurantPaymentMethod.client_id == client_id, RestaurantPaymentMethod.is_active.is_(True))
        .order_by(RestaurantPaymentMethod.updated_at.desc(), RestaurantPaymentMethod.created_at.desc())
    ).all()
    if not rows:
        return None
    payment = next(
        (
            row for row in rows
            if (row.display_name or "").strip().casefold() == "datos para pagos"
            or row.method == "other"
        ),
        rows[0],
    )
    return {
        "number": (payment.account_number or "").strip(),
        "holder": (payment.account_name or "").strip(),
        "instructions": (payment.instructions or "").strip(),
        "receipt_required": bool(payment.receipt_required),
    }


def pending_restaurant_payment_message(
    db: Session,
    context: LeadContext,
    *,
    verification_failed: bool = False,
) -> str | None:
    """Build the mandatory customer continuation for an unpaid order."""

    order = db.scalar(
        select(RestaurantOrder)
        .where(
            RestaurantOrder.client_id == context.client_id,
            RestaurantOrder.conversation_id == context.conversation_id,
            RestaurantOrder.source == "whatsapp",
            RestaurantOrder.order_status == "pending_payment",
            RestaurantOrder.payment_status == "pending",
        )
        .order_by(RestaurantOrder.created_at.desc())
    )
    if order is None:
        return None
    payment = _configured_payment_details(db, context.client_id)
    total = Decimal(order.total).quantize(Decimal("0.01"))
    lines = [
        f"Tu pedido {order.order_number} está registrado y pendiente de pago.",
        f"Total exacto: S/ {total:.2f}.",
    ]
    if payment and payment["number"]:
        lines.append(f"Número para pagos: {payment['number']}.")
        if payment["holder"]:
            lines.append(f"Titular: {payment['holder']}.")
        if payment["instructions"]:
            lines.append(f"Instrucciones: {payment['instructions']}")
        lines.append("Realiza el abono por el total indicado y envíame el comprobante después del pago.")
    else:
        lines.append("El restaurante aún no tiene configurado un número para pagos visible para este pedido.")
        lines.append("Cuando realices el abono, envíame el comprobante para poder verificarlo.")
    if verification_failed:
        lines.append("Recibí el comprobante y los datos del pago; el pedido quedó en revisión manual y un administrador debe confirmar el abono.")
    else:
        lines.append("El pedido no estará listo para recoger hasta que un administrador confirme manualmente el pago.")
    return " ".join(lines)


async def execute_internal_tool(db: Session, name: str, args: dict, context: LeadContext) -> tuple[str, bool]:
    if name == "create_restaurant_order":
        try:
            # Playground and WhatsApp both use the same tenant-scoped conversation
            # context. The conversation channel is the origin; it is not a
            # reason to reject an otherwise valid restaurant order.
            validate_lead_context(db, context)
            if (
                context.channel not in _RESTAURANT_ORDER_CHANNELS
                or not isinstance(args, dict)
                or set(args) - _RESTAURANT_ORDER_ARGUMENTS
                or not {"modality", "items"}.issubset(args)
            ):
                raise ValueError("Invalid restaurant order context")
            items = args.get("items")
            if not isinstance(items, list) or not items:
                raise ValueError("At least one item is required")
            # A legacy payment_method must not change idempotency identity and
            # create a second copy of the same conversational order.
            idempotency_args = {key: value for key, value in args.items() if key != "payment_method"}
            normalized = json.dumps(idempotency_args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            namespace = "whatsapp" if context.channel in {"whatsapp", "whatsapp_cloud"} else context.channel
            key = f"{namespace}:{context.conversation_id}:{hashlib.sha256(normalized.encode()).hexdigest()}"
            phone = args.get("customer_phone") or trusted_whatsapp_phone(db, context)
            order_items = resolve_whatsapp_items(db, context.client_id, items)
            order = create_order(
                db,
                client_id=context.client_id,
                # Keep the existing operational source so pending-payment
                # visibility and payment verification continue to use the
                # established conversational-order path. The true channel
                # remains on Conversation.channel.
                source="whatsapp",
                modality=args["modality"],
                items=order_items,
                customer_name=args.get("customer_name"),
                customer_phone=phone,
                address=args.get("address"),
                address_reference=args.get("address_reference"),
                # ``delivery_zone`` is accepted only for old serialized tool
                # calls; it is deliberately ignored for new orders.
                delivery_zone_name=None,
                # Legacy callers may still include payment_method.  It is
                # intentionally ignored: customer payments use the single
                # configured destination number.
                payment_method=None,
                conversation_id=context.conversation_id,
                idempotency_key=key,
            )
            return json.dumps({"ok": True, "order_number": order.order_number, "status": order.order_status, "payment_status": order.payment_status, "subtotal": str(order.subtotal), "total": str(order.total), "delivery_fee": str(order.delivery_fee), "amount_due_for_products": str(order.subtotal), "payment_details": _configured_payment_details(db, context.client_id)}, separators=(",", ":")), False
        except (LeadCaptureError, ValueError, HTTPException) as exc:
            return f"Error: {getattr(exc, 'detail', str(exc))}", True
    if name == "report_restaurant_payment":
        try:
            validate_lead_context(db, context)
            if not isinstance(args, dict) or set(args) != {"operation_id", "amount"}:
                raise ValueError("The payment operation and amount are required")
            operation_id = str(args["operation_id"]).strip()
            if not operation_id or len(operation_id) > 180:
                raise ValueError("Invalid payment operation")
            try:
                amount = Decimal(str(args["amount"]))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError("Invalid payment amount") from exc
            if amount <= 0:
                raise ValueError("Invalid payment amount")
            pending_orders = db.scalars(
                select(RestaurantOrder)
                .where(
                    RestaurantOrder.client_id == context.client_id,
                    RestaurantOrder.conversation_id == context.conversation_id,
                    RestaurantOrder.source == "whatsapp",
                    RestaurantOrder.order_status == "pending_payment",
                    RestaurantOrder.payment_status == "pending",
                )
                .order_by(RestaurantOrder.created_at.desc())
            ).all()
            if not pending_orders:
                raise ValueError("There is no pending restaurant order for this conversation")
            matching_amount = [order for order in pending_orders if Decimal(order.subtotal) == amount]
            if len(pending_orders) == 1:
                order = pending_orders[0]
            elif len(matching_amount) == 1:
                order = matching_amount[0]
            else:
                raise ValueError("The payment report does not identify one pending restaurant order")
            used_operation = db.scalar(
                select(RestaurantOrder.id).where(
                    RestaurantOrder.client_id == context.client_id,
                    RestaurantOrder.id != order.id,
                    func.lower(func.btrim(RestaurantOrder.payment_reference)) == operation_id.casefold(),
                )
            )
            used_notification = db.scalar(
                select(PaymentNotification.id).where(
                    PaymentNotification.client_id == context.client_id,
                    func.lower(func.btrim(PaymentNotification.external_operation_id)) == operation_id.casefold(),
                )
            )
            if used_operation or used_notification:
                raise ValueError("This payment operation has already been used")
            order.payment_reference = operation_id
            order.reported_payment_amount = amount
            order.receipt_submitted = True
            order.payment_reported_at = order.payment_reported_at or now_utc()
            order.payment_review_status = "review"
            order.payment_rejection_reason = None
            db.commit()
            return json.dumps(
                {
                    "ok": True,
                    "order_number": order.order_number,
                    "status": order.order_status,
                    "payment_status": order.payment_status,
                    "payment_review_status": order.payment_review_status,
                    "reported_operation_id": operation_id,
                    "reported_amount": str(amount),
                },
                separators=(",", ":"),
            ), False
        except (LeadCaptureError, ValueError, HTTPException) as exc:
            return f"Error: {getattr(exc, 'detail', str(exc))}", True
    if name == "consultar_disponibilidad":
        try:
            if not isinstance(args, dict):
                raise CalendarError("Invalid calendar arguments")
            result = await availability(
                db,
                context,
                date=str(args.get("date") or ""),
                part_of_day=args.get("part_of_day"),
            )
            return json.dumps(result, ensure_ascii=False, separators=(",", ":")), False
        except (CalendarError, ValueError) as exc:
            return f"Error: {exc}", True
    if name == "crear_cita":
        try:
            if not isinstance(args, dict):
                raise CalendarError("Invalid calendar arguments")
            _require_latest_user_evidence(db, context, args.get("confirmation_evidence"))
            result = await create_appointment(db, context, start_iso=str(args.get("start_iso") or ""))
            return json.dumps(result, ensure_ascii=False, separators=(",", ":")), False
        except (CalendarError, ValueError) as exc:
            return f"Error: {exc}", True
    if name == "reprogramar_cita":
        try:
            if not isinstance(args, dict):
                raise CalendarError("Invalid calendar arguments")
            _require_latest_user_evidence(db, context, args.get("confirmation_evidence"))
            result = await reschedule_appointment(db, context, start_iso=str(args.get("start_iso") or ""))
            return json.dumps(result, ensure_ascii=False, separators=(",", ":")), False
        except (CalendarError, ValueError) as exc:
            return f"Error: {exc}", True
    if name == "cancelar_cita":
        try:
            if not isinstance(args, dict):
                raise CalendarError("Invalid calendar arguments")
            _require_latest_user_evidence(db, context, args.get("confirmation_evidence"))
            result = await cancel_appointment(db, context)
            return json.dumps(result, ensure_ascii=False, separators=(",", ":")), False
        except (CalendarError, ValueError) as exc:
            return f"Error: {exc}", True
    if name == "notify_sales_advisor":
        try:
            evidence = args.get("consent_evidence") if isinstance(args, dict) else None
            if not isinstance(evidence, str) or set(args) != {"consent_evidence"}:
                raise LeadHandoffError("Only consent_evidence is accepted")
            result = await notify_sales_advisor(db, context, evidence)
            return json.dumps(result, separators=(",", ":")), False
        except (LeadHandoffError, ValueError) as exc:
            return f"Error: {exc}", True
    if name != "create_or_update_lead":
        return f"Error: unknown internal tool '{name}'", True
    try:
        tool_payload = LeadToolInput.model_validate(args)
        _validate_user_evidence(db, context, tool_payload)
        client = db.scalar(
            select(Client).where(
                Client.id == context.client_id,
                Client.agency_id == context.agency_id,
            )
        )
        if "budget" in tool_payload.model_fields_set and tool_payload.budget is not None:
            if client and budget_needs_clarification(client.industry, tool_payload.budget):
                return json.dumps(
                    {
                        "ok": False,
                        "skipped": True,
                        "reason": "budget_needs_clarification",
                        "instruction": (
                            "The stated budget is clearly implausible for this business context. "
                            "Do not save it as a real budget. Ask one brief clarification question."
                        ),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ), False
        if "notes" in tool_payload.model_fields_set and tool_payload.notes is not None:
            if client and notes_need_clarification(client.industry, tool_payload.notes):
                return json.dumps(
                    {
                        "ok": False,
                        "skipped": True,
                        "reason": "qualification_value_needs_clarification",
                        "instruction": (
                            "The stated purchase horizon is clearly implausible. Do not save it as a real "
                            "qualification fact. Ask one brief clarification question."
                        ),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ), False
        payload = LeadCaptureInput.model_validate(tool_payload.model_dump(exclude={"evidence"}))
        result = create_or_update_lead(db, context, payload)
    except LeadIdentityRequired:
        return json.dumps(
            {
                "ok": False,
                "skipped": True,
                "reason": "identity_required",
                "instruction": "Do not mention lead storage. Continue answering the user's request normally.",
            },
            separators=(",", ":"),
        ), False
    except (LeadCaptureError, ValueError) as exc:
        return f"Error: {exc}", True
    return json.dumps(
        {
            "ok": True,
            "created": result.created,
            "updated_fields": result.updated_fields,
        },
        separators=(",", ":"),
    ), False

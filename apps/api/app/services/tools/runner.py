"""Single entry point for generating an agent reply, with or without tools.

Drop-in replacement for chat_completion at the chat call sites: same error
semantics (HTTPException 502 on provider failure), same Completion result —
plus tool_calls metadata when tools ran.
"""

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import Agent, AgentTool, Client, Lead, LeadConversation, Message
from ..ai import Completion, chat_completion
from ..leads import LeadContext, trusted_whatsapp_phone
from ..lead_handoffs import handoff_available
from ..calendar import calendar_connected
from ..restaurant import is_restaurant_client
from .internal import (
    CALENDAR_AVAILABILITY_TOOL_SCHEMA,
    CALENDAR_CANCEL_TOOL_SCHEMA,
    CALENDAR_CREATE_TOOL_SCHEMA,
    CALENDAR_RESCHEDULE_TOOL_SCHEMA,
    CALENDAR_RULES,
    LEAD_CAPTURE_RULES,
    LEAD_TOOL_SCHEMA,
    RESTAURANT_ADD_ITEM_TOOL_SCHEMA,
    RESTAURANT_CONFIRM_TOOL_SCHEMA,
    RESTAURANT_DETAILS_TOOL_SCHEMA,
    RESTAURANT_MENU_TOOL_SCHEMA,
    RESTAURANT_ORDER_RULES,
    RESTAURANT_PAYMENT_TOOL_SCHEMA,
    RESTAURANT_REMOVE_ITEM_TOOL_SCHEMA,
    SALES_ADVISOR_RULES,
    SALES_ADVISOR_TOOL_SCHEMA,
    TRUSTED_WHATSAPP_LEAD_RULE,
)
from .loop import anthropic_tool_loop, openai_tool_loop
from .specs import ToolSpec, build_tool_specs

# Injected whenever the agent has tools: a failing tool must never be papered
# over with the model's own knowledge.
TOOL_FAILURE_RULE = (
    "Tool usage rules: when the user's request depends on a tool and the tool call fails or returns an error, "
    "do not answer from memory and do not invent data. Tell the user that the information or action is not "
    "available right now and that they can try again later."
)


def _with_tool_rules(messages: list[dict]) -> list[dict]:
    amended = list(messages)
    for index, message in enumerate(amended):
        if message["role"] == "system":
            amended[index] = {**message, "content": f"{message['content']}\n\n{TOOL_FAILURE_RULE}"}
            return amended
    return [{"role": "system", "content": TOOL_FAILURE_RULE}, *amended]


def _with_lead_rules(messages: list[dict], rules: str) -> list[dict]:
    amended = list(messages)
    for index, message in enumerate(amended):
        if message["role"] == "system":
            amended[index] = {**message, "content": f"{message['content']}\n\n{rules}"}
            return amended
    return [{"role": "system", "content": rules}, *amended]


def _sales_qualification_rules(db: Session, agent: Agent, context: LeadContext) -> str:
    """Guide WhatsApp sales agents to qualify progressively before handoff."""
    if context.channel not in ("whatsapp", "whatsapp_cloud"):
        return ""

    lead = db.scalar(
        select(Lead)
        .join(LeadConversation)
        .where(LeadConversation.conversation_id == context.conversation_id)
    )
    known: list[str] = []
    missing: list[str] = []
    fields = (
        ("name", "nombre"),
        ("interest", "interés"),
        ("budget", "presupuesto"),
        ("preferred_contact_time", "horario preferido de contacto"),
    )
    for attr, label in fields:
        value = getattr(lead, attr, None) if lead else None
        (known if value else missing).append(label)

    assistant_count = db.scalar(
        select(func.count(Message.id)).where(
            Message.conversation_id == context.conversation_id,
            Message.role == "assistant",
        )
    ) or 0

    intro = (
        f"Esta es la primera respuesta del agente en esta conversación. Preséntate brevemente como {agent.name}, "
        f"asesora virtual de {agent.client.name}, antes de continuar. "
        if assistant_count == 0
        else ""
    )
    known_text = ", ".join(known) if known else "ninguno"
    missing_text = ", ".join(missing) if missing else "ninguno"

    return (
        "Flujo comercial por WhatsApp: responde primero la pregunta actual del prospecto y luego continúa "
        "la conversación para completar la calificación, sin convertir la respuesta en un formulario. "
        f"{intro}"
        f"Datos ya registrados: {known_text}. Datos aún faltantes: {missing_text}. "
        "Haz como máximo una pregunta de calificación por turno, priorizando en este orden: nombre, interés, "
        "presupuesto y horario preferido de contacto. No insertes preguntas adicionales de calificación, como plazo de compra, "
        "antes de completar ese orden salvo que las instrucciones del negocio lo exijan expresamente. "
        "No vuelvas a preguntar datos ya registrados. Si una respuesta parece claramente absurda, imposible, contradictoria "
        "o una broma (por ejemplo un presupuesto simbólico para un inmueble o un plazo de cientos de años), no la trates como "
        "dato comercial válido ni la guardes: haz una sola pregunta breve para confirmar o corregir ese dato. "
        "El número de WhatsApp ya es una identidad válida del contacto: no pidas su teléfono solo para crear el lead. "
        "Cuando el prospecto aporte un dato nuevo, llama create_or_update_lead en ese mismo turno con evidencia literal. "
        "El lead debe existir desde el contacto por WhatsApp; aceptar hablar con un asesor NO es requisito para ser lead. "
        "No ofrezcas ni notifiques al asesor antes de intentar completar nombre, interés, presupuesto y horario de contacto, "
        "salvo que el prospecto pida explícitamente hablar con un asesor. Si el prospecto rechaza proporcionar un dato, "
        "no insistas repetidamente: continúa ayudando y registra los datos que sí entregue. "
        "Cuando ya exista información suficiente y corresponda el handoff, pide una confirmación clara antes de notificar al asesor."
    )


async def run_completion(
    db: Session,
    agent: Agent,
    base_url: str,
    api_key: str,
    messages: list[dict],
    *,
    tool_context: LeadContext | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> Completion:
    model = agent.model.strip()
    rows = db.scalars(select(AgentTool).where(AgentTool.agent_id == agent.id, AgentTool.enabled.is_(True))).all()
    specs = build_tool_specs(list(rows))
    if tool_context is not None:
        has_trusted_whatsapp_phone = trusted_whatsapp_phone(db, tool_context) is not None
        identity_description = (
            "The server already has the validated WhatsApp sender phone, so an explicit user-provided identity "
            "is not required for a new lead. Do not include the channel phone in arguments or evidence and do not "
            "ask for the same number merely to create the lead."
            if has_trusted_whatsapp_phone else
            "A new lead requires an explicitly provided name, phone, or email; do not call this tool for an "
            "anonymous interest-only first turn."
        )
        specs.insert(
            0,
            ToolSpec(
                name="create_or_update_lead",
                description=(
                    "Create or update the current prospect's lead record using only information explicitly stated "
                    "in user messages. Assistant recommendations or statements are not prospect data. Make one "
                    "consolidated call per user turn. Include one exact user quote or a list of exact user quotes in "
                    "evidence for every supplied data field. Multiple quotes may consolidate facts from several user "
                    f"turns. {identity_description} Interest, budget, or notes may be sent without "
                    "repeating identity when a lead is already linked. Call it again when the prospect provides new "
                    "or corrected information."
                ),
                input_schema=LEAD_TOOL_SCHEMA,
                internal_name="create_or_update_lead",
            ),
        )
        client = db.get(Client, tool_context.client_id)
        restaurant_mode = is_restaurant_client(client)
        if restaurant_mode:
            specs = [spec for spec in specs if spec.name != "create_or_update_lead"]
            restaurant_specs = [
                ToolSpec(
                    name="consultar_menu",
                    description="Read the restaurant's active structured menu and server-side prices.",
                    input_schema=RESTAURANT_MENU_TOOL_SCHEMA,
                    internal_name="consultar_menu",
                ),
                ToolSpec(
                    name="agregar_item_pedido",
                    description="Add an explicitly requested menu item and quantity to the current order; totals are calculated by the server.",
                    input_schema=RESTAURANT_ADD_ITEM_TOOL_SCHEMA,
                    internal_name="agregar_item_pedido",
                ),
                ToolSpec(
                    name="quitar_item_pedido",
                    description="Remove or reduce an explicitly requested item from the current editable order.",
                    input_schema=RESTAURANT_REMOVE_ITEM_TOOL_SCHEMA,
                    internal_name="quitar_item_pedido",
                ),
                ToolSpec(
                    name="configurar_entrega_pedido",
                    description="Save explicit customer name, delivery/table/pickup mode, table identifier, or delivery address for the current order.",
                    input_schema=RESTAURANT_DETAILS_TOOL_SCHEMA,
                    internal_name="configurar_entrega_pedido",
                ),
                ToolSpec(
                    name="ver_pedido",
                    description="Return the current order with server-calculated prices and total.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                    internal_name="ver_pedido",
                ),
                ToolSpec(
                    name="confirmar_pedido",
                    description="Confirm the complete order only after the customer explicitly accepts the itemized order and total.",
                    input_schema=RESTAURANT_CONFIRM_TOOL_SCHEMA,
                    internal_name="confirmar_pedido",
                ),
                ToolSpec(
                    name="registrar_pago_reportado",
                    description="Record that the customer says they paid. This never confirms payment; human verification is still required.",
                    input_schema=RESTAURANT_PAYMENT_TOOL_SCHEMA,
                    internal_name="registrar_pago_reportado",
                ),
            ]
            specs[0:0] = restaurant_specs
            messages = _with_lead_rules(messages, RESTAURANT_ORDER_RULES)
        calendar_is_connected = bool(client and calendar_connected(client)) and not restaurant_mode
        if calendar_is_connected:
            calendar_specs = [
                ToolSpec(
                    name="consultar_disponibilidad",
                    description="Consult the client's real Google Calendar and return only appointment slots that are currently available.",
                    input_schema=CALENDAR_AVAILABILITY_TOOL_SCHEMA,
                    internal_name="consultar_disponibilidad",
                ),
                ToolSpec(
                    name="crear_cita",
                    description="Create a Google Calendar appointment only after the prospect explicitly confirms one exact offered time.",
                    input_schema=CALENDAR_CREATE_TOOL_SCHEMA,
                    internal_name="crear_cita",
                ),
                ToolSpec(
                    name="reprogramar_cita",
                    description="Move the current conversation's confirmed Google Calendar appointment after explicit confirmation of the new time.",
                    input_schema=CALENDAR_RESCHEDULE_TOOL_SCHEMA,
                    internal_name="reprogramar_cita",
                ),
                ToolSpec(
                    name="cancelar_cita",
                    description="Cancel the current conversation's confirmed appointment only when the prospect explicitly asks to cancel it.",
                    input_schema=CALENDAR_CANCEL_TOOL_SCHEMA,
                    internal_name="cancelar_cita",
                ),
            ]
            specs[1:1] = calendar_specs
        if not restaurant_mode and handoff_available(db, tool_context):
            specs.insert(
                1,
                ToolSpec(
                    name="notify_sales_advisor",
                    description=(
                        "Notify this client's configured sales advisor after explicit acceptance, or after an "
                        "unequivocal contact time/day/method response to the immediately preceding advisor-contact "
                        "coordination question. Supply only the exact literal prospect quote."
                    ),
                    input_schema=SALES_ADVISOR_TOOL_SCHEMA,
                    internal_name="notify_sales_advisor",
                ),
            )
        if not restaurant_mode:
            lead_rules = LEAD_CAPTURE_RULES
            if has_trusted_whatsapp_phone:
                lead_rules = f"{lead_rules} {TRUSTED_WHATSAPP_LEAD_RULE}"
            if handoff_available(db, tool_context):
                lead_rules = f"{lead_rules} {SALES_ADVISOR_RULES}"
            if calendar_is_connected:
                lead_rules = f"{lead_rules} {CALENDAR_RULES}"
            qualification_rules = _sales_qualification_rules(db, agent, tool_context)
            if qualification_rules:
                lead_rules = f"{lead_rules} {qualification_rules}"
            messages = _with_lead_rules(messages, lead_rules)
    if not specs:
        return await chat_completion(agent.provider, base_url, api_key, model, messages, temperature=temperature, max_tokens=max_tokens)
    messages = _with_tool_rules(messages)
    try:
        if agent.provider == "anthropic":
            return await anthropic_tool_loop(
                base_url, api_key, model, messages, specs, temperature, max_tokens, db, tool_context
            )
        return await openai_tool_loop(
            base_url, api_key, model, messages, specs, temperature, max_tokens, db, tool_context
        )
    except HTTPException:
        raise
    except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not get a valid response from the AI provider. Check the API key and the model.",
        ) from exc

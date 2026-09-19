"""Single entry point for generating an agent reply, with or without tools.

Drop-in replacement for chat_completion at the chat call sites: same error
semantics (HTTPException 502 on provider failure), same Completion result —
plus tool_calls metadata when tools ran.
"""

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Agent, AgentTool
from ..ai import Completion, chat_completion
from ..leads import LeadContext, trusted_whatsapp_phone
from ..lead_handoffs import handoff_available
from .internal import (
    LEAD_CAPTURE_RULES,
    LEAD_TOOL_SCHEMA,
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
        if handoff_available(db, tool_context):
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
        lead_rules = LEAD_CAPTURE_RULES
        if has_trusted_whatsapp_phone:
            lead_rules = f"{lead_rules} {TRUSTED_WHATSAPP_LEAD_RULE}"
        if handoff_available(db, tool_context):
            lead_rules = f"{lead_rules} {SALES_ADVISOR_RULES}"
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

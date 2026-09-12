import uuid

from sqlalchemy.orm import Session

from ..models import Conversation, Message, UsageRecord, new_uuid
from .ai import Completion


def record_usage(
    db: Session,
    agency_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    provider: str,
    model: str,
    completion: Completion,
    *,
    conversation: Conversation | None = None,
    message: Message | None = None,
) -> None:
    """Store what a completion used and cost, linked to the reply it produced.

    ``conversation`` and ``message`` are the reply's context when the caller
    has them: a pending ``Message`` gets its id here so the link holds once
    the session flushes. The caller owns the commit.
    """
    if completion.input_tokens <= 0 and completion.output_tokens <= 0:
        return
    if message is not None and message.id is None:
        message.id = new_uuid()
    db.add(
        UsageRecord(
            agency_id=agency_id,
            agent_id=agent_id,
            provider=provider,
            model=model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=completion.cost_usd,
            cached_tokens=completion.cached_tokens,
            reasoning_tokens=completion.reasoning_tokens,
            served_by=(completion.served_by or "")[:60],
            duration_ms=completion.duration_ms,
            conversation_id=conversation.id if conversation is not None else None,
            message_id=message.id if message is not None else None,
        )
    )

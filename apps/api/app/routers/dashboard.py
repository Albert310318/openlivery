from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Agent, Client, Conversation, Lead, Message, UsageRecord, User, WhatsAppChannel, now_utc
from ..schemas import DashboardMetrics, DashboardOut


router = APIRouter(prefix="/dashboard", tags=["Inicio"])
LEAD_STATUSES = ("new", "qualified", "follow_up", "won", "lost")


def _scope(stmt, column, user: User):
    return stmt if user.is_vendiq_admin else stmt.where(column == user.agency_id)


@router.get("", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    clients = db.scalar(_scope(select(func.count(Client.id)), Client.agency_id, user)) or 0
    active_clients = db.scalar(
        _scope(select(func.count(Client.id)).where(Client.is_active.is_(True)), Client.agency_id, user)
    ) or 0
    agents = db.scalar(_scope(select(func.count(Agent.id)), Agent.agency_id, user)) or 0
    active_agents = db.scalar(
        _scope(select(func.count(Agent.id)).where(Agent.is_active.is_(True)), Agent.agency_id, user)
    ) or 0
    conversations = db.scalar(_scope(select(func.count(Conversation.id)), Conversation.agency_id, user)) or 0
    channels = db.scalar(_scope(select(func.count(WhatsAppChannel.id)), WhatsAppChannel.agency_id, user)) or 0
    connected_channels = db.scalar(
        _scope(
            select(func.count(WhatsAppChannel.id)).where(WhatsAppChannel.status == "connected"),
            WhatsAppChannel.agency_id,
            user,
        )
    ) or 0
    recent_agents_query = select(Agent).order_by(Agent.created_at.desc()).limit(5)
    recent_agents = db.scalars(_scope(recent_agents_query, Agent.agency_id, user)).all()
    lead_rows = db.execute(
        _scope(
            select(Lead.status, func.count(Lead.id)).group_by(Lead.status),
            Lead.agency_id,
            user,
        )
    ).all()
    leads_by_status = {status: 0 for status in LEAD_STATUSES}
    leads_by_status.update({status: count for status, count in lead_rows})
    total_leads = sum(leads_by_status.values())
    conversion_rate = round((leads_by_status["won"] / total_leads) * 100, 1) if total_leads else 0.0
    current_time = now_utc()
    follow_up_query = (
        select(Lead)
        .where(Lead.next_follow_up_at.is_not(None))
        .order_by(
            case((Lead.next_follow_up_at < current_time, 0), else_=1),
            Lead.next_follow_up_at.asc(),
        )
        .limit(5)
    )
    follow_up_rows = db.scalars(_scope(follow_up_query, Lead.agency_id, user)).all()
    return {
        "clients": clients,
        "active_clients": active_clients,
        "agents": agents,
        "active_agents": active_agents,
        "conversations": conversations,
        "channels": channels,
        "connected_channels": connected_channels,
        "recent_agents": recent_agents,
        "total_leads": total_leads,
        "leads_by_status": leads_by_status,
        "conversion_rate": conversion_rate,
        "pending_follow_ups": [
            {
                "id": lead.id,
                "name": lead.name,
                "interest": lead.interest,
                "next_follow_up_at": lead.next_follow_up_at,
                "is_overdue": lead.next_follow_up_at < current_time,
            }
            for lead in follow_up_rows
        ],
    }


@router.get("/metrics", response_model=DashboardMetrics)
def dashboard_metrics(
    days: int = Query(default=14, ge=1, le=365),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    start_date = (now_utc() - timedelta(days=days - 1)).date()
    since = now_utc() - timedelta(days=days)

    messages = db.scalar(
        select(func.count(Message.id))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Message.created_at >= since)
    )
    messages = db.scalar(_scope(messages, Conversation.agency_id, user)) or 0
    human_query = select(func.count(Conversation.id)).where(
        Conversation.mode == "human", Conversation.created_at >= since
    )
    human_conversations = db.scalar(_scope(human_query, Conversation.agency_id, user)) or 0

    channel_query = (
        select(Conversation.channel, func.count(Conversation.id))
        .where(Conversation.created_at >= since)
        .group_by(Conversation.channel)
    )
    channel_rows = db.execute(_scope(channel_query, Conversation.agency_id, user)).all()
    by_channel = {channel: count for channel, count in channel_rows}

    # New conversations per day over the selected window (zero-filled).
    day = func.date(Conversation.created_at)
    daily_query = select(day, func.count(Conversation.id)).where(day >= start_date).group_by(day)
    daily_rows = db.execute(_scope(daily_query, Conversation.agency_id, user)).all()
    counts = {str(d): c for d, c in daily_rows}
    daily_conversations = [
        {"date": (start_date + timedelta(days=i)).isoformat(), "count": counts.get((start_date + timedelta(days=i)).isoformat(), 0)}
        for i in range(days)
    ]

    top_query = (
        select(Agent.id, Agent.name, func.count(Conversation.id))
        .join(Conversation, Conversation.agent_id == Agent.id)
        .where(Conversation.created_at >= since)
        .group_by(Agent.id, Agent.name)
        .order_by(func.count(Conversation.id).desc())
        .limit(5)
    )
    top_rows = db.execute(_scope(top_query, Agent.agency_id, user)).all()
    top_agents = [{"id": aid, "name": name, "conversations": count} for aid, name, count in top_rows]

    usage_total_query = select(
        func.coalesce(func.sum(UsageRecord.input_tokens), 0),
        func.coalesce(func.sum(UsageRecord.output_tokens), 0),
    ).where(UsageRecord.created_at >= since)
    tokens_in, tokens_out = db.execute(_scope(usage_total_query, UsageRecord.agency_id, user)).one()
    usage_query = (
        select(
            UsageRecord.model,
            func.coalesce(func.sum(UsageRecord.input_tokens), 0),
            func.coalesce(func.sum(UsageRecord.output_tokens), 0),
        )
        .where(UsageRecord.created_at >= since)
        .group_by(UsageRecord.model)
        .order_by((func.sum(UsageRecord.input_tokens) + func.sum(UsageRecord.output_tokens)).desc())
        .limit(6)
    )
    usage_rows = db.execute(_scope(usage_query, UsageRecord.agency_id, user)).all()
    usage_by_model = [{"model": model, "input_tokens": input_tokens, "output_tokens": output_tokens} for model, input_tokens, output_tokens in usage_rows]

    return {
        "messages": messages,
        "human_conversations": human_conversations,
        "by_channel": by_channel,
        "daily_conversations": daily_conversations,
        "top_agents": top_agents,
        "tokens_in": int(tokens_in),
        "tokens_out": int(tokens_out),
        "usage_by_model": usage_by_model,
    }

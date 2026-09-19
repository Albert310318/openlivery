import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import get_current_user
from ..models import Lead, LeadConversation, User, now_utc
from ..schemas_leads import (
    LeadConversationOut,
    LeadDetail,
    LeadFollowUpUpdate,
    LeadOut,
    LeadStatus,
    LeadStatusUpdate,
)


router = APIRouter(prefix="/leads", tags=["Leads"])


def _lead(db: Session, user: User, lead_id: uuid.UUID) -> Lead:
    query = (
        select(Lead)
        .options(selectinload(Lead.conversations).joinedload(LeadConversation.conversation))
        .where(Lead.id == lead_id)
    )
    if not user.is_vendiq_admin:
        query = query.where(Lead.agency_id == user.agency_id)
    lead = db.scalar(query)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def _detail(lead: Lead) -> LeadDetail:
    base = LeadOut.model_validate(lead)
    conversations = [
        LeadConversationOut(
            conversation_id=link.conversation_id,
            channel=link.conversation.channel,
            title=link.conversation.title,
            created_at=link.created_at,
        )
        for link in sorted(lead.conversations, key=lambda item: item.created_at)
    ]
    return LeadDetail(**base.model_dump(), conversations=conversations)


@router.get("", response_model=list[LeadOut])
def list_leads(
    client_id: uuid.UUID | None = None,
    status: LeadStatus | None = None,
    search: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(Lead)
    if not user.is_vendiq_admin:
        query = query.where(Lead.agency_id == user.agency_id)
    if client_id:
        query = query.where(Lead.client_id == client_id)
    if status:
        query = query.where(Lead.status == status)
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.where(or_(
            Lead.name.ilike(term),
            Lead.phone.ilike(term),
            Lead.email.ilike(term),
            Lead.interest.ilike(term),
        ))
    return db.scalars(query.order_by(Lead.updated_at.desc()).limit(limit).offset(offset)).all()


@router.get("/{lead_id}", response_model=LeadDetail)
def get_lead(
    lead_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _detail(_lead(db, user, lead_id))


@router.patch("/{lead_id}/status", response_model=LeadOut)
def update_lead_status(
    lead_id: uuid.UUID,
    payload: LeadStatusUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    lead = _lead(db, user, lead_id)
    lead.status = payload.status
    lead.updated_at = now_utc()
    db.commit()
    db.refresh(lead)
    return lead


@router.patch("/{lead_id}/follow-up", response_model=LeadOut)
def update_lead_follow_up(
    lead_id: uuid.UUID,
    payload: LeadFollowUpUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    lead = _lead(db, user, lead_id)
    lead.next_follow_up_at = payload.next_follow_up_at
    lead.updated_at = now_utc()
    db.commit()
    db.refresh(lead)
    return lead

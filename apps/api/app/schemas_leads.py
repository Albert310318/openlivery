import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


LeadStatus = Literal["new", "qualified", "follow_up", "won", "lost"]
ToolLeadStatus = Literal["new", "qualified", "follow_up"]


class LeadCaptureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=180)
    phone: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=320)
    interest: str | None = Field(default=None, max_length=8000)
    budget: str | None = Field(default=None, max_length=180)
    preferred_contact_time: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=8000)
    status: ToolLeadStatus | None = None

    @field_validator("name", "phone", "email", "interest", "budget", "preferred_contact_time", "notes", mode="before")
    @classmethod
    def empty_to_none(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class LeadToolInput(LeadCaptureInput):
    evidence: dict[str, str | list[str]]


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agency_id: uuid.UUID
    client_id: uuid.UUID
    agent_id: uuid.UUID
    name: str | None
    phone: str | None
    email: str | None
    interest: str | None
    budget: str | None
    preferred_contact_time: str | None
    notes: str | None
    source: str
    status: LeadStatus
    next_follow_up_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LeadConversationOut(BaseModel):
    conversation_id: uuid.UUID
    channel: str
    title: str
    created_at: datetime


class LeadDetail(LeadOut):
    conversations: list[LeadConversationOut] = []


class LeadStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: LeadStatus


class LeadFollowUpUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_follow_up_at: datetime | None

    @field_validator("next_follow_up_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("next_follow_up_at must include a timezone")
        return value.astimezone(timezone.utc)

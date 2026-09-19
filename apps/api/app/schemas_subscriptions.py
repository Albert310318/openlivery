"""Subscription billing is separate from a business's customer payments."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from .schemas import ORMModel

SubscriptionStatus = Literal["TRIAL", "ACTIVE", "PAYMENT_PENDING", "SUSPENDED", "CANCELLED"]


class ModuleOut(ORMModel):
    code: str
    name: str
    is_available: bool


class PlanOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    description: str
    monthly_price: Decimal | None
    currency: str
    is_active: bool
    available_for_new_subscriptions: bool
    modules: list[ModuleOut]


class SubscriptionOut(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    plan: PlanOut
    status: SubscriptionStatus
    first_activated_at: datetime | None
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    next_renewal_at: datetime | None


class SubscriptionWrite(BaseModel):
    # Activation/trial dates are deliberately read-only in Phase 1A.
    model_config = ConfigDict(extra="forbid")
    plan_id: uuid.UUID
    status: SubscriptionStatus
    next_renewal_at: AwareDatetime | None = None

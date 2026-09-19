import uuid
from decimal import Decimal
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class PromotionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=64)
    discount_type: Literal["PERCENTAGE", "FIXED"]
    value: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    plan_scope: Literal["ALL", "SELECTED"] = "ALL"
    plan_ids: list[uuid.UUID] = Field(default_factory=list)
    duration: Literal["FIRST_PAID_MONTH", "NEXT_N_RENEWALS"] = "FIRST_PAID_MONTH"
    cycles: int = Field(default=1, gt=0, le=2147483647, strict=True)
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    max_total_uses: int | None = Field(default=None, gt=0, le=2147483647, strict=True)
    max_uses_per_client: int | None = Field(default=1, gt=0, le=2147483647, strict=True)
    restricted_client_id: uuid.UUID | None = None
    is_active: bool = True

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("value", mode="before")
    @classmethod
    def reject_float(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("Send decimal amounts as strings")
        return value

    @model_validator(mode="after")
    def valid_conditions(self):
        if self.discount_type == "PERCENTAGE" and self.value > 100:
            raise ValueError("Percentage must be <= 100")
        if self.discount_type == "FIXED" and self.currency is None:
            raise ValueError("Fixed discounts require currency")
        if (self.plan_scope == "SELECTED") != bool(self.plan_ids):
            raise ValueError("SELECTED requires plans; ALL requires an empty plan list")
        if len(set(self.plan_ids)) != len(self.plan_ids):
            raise ValueError("Duplicate plans")
        if self.duration == "FIRST_PAID_MONTH" and self.cycles != 1:
            raise ValueError("FIRST_PAID_MONTH grants exactly one cycle")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("Expiration must follow start")
        return self


class PromotionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=64)
    plan_id: uuid.UUID

    _normalize_code = field_validator("code", mode="before")(PromotionWrite.normalize_code.__func__)


class PromotionOut(PromotionWrite):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class PromotionPreviewOut(BaseModel):
    code: str
    plan_id: uuid.UUID
    currency: str
    original_price: Decimal
    discount: Decimal
    total_final: Decimal
    duration: str
    cycles: int
    consumes_use: bool = False
    extends_trial: bool = False


class RedemptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    client_id: uuid.UUID
    subscription_id: uuid.UUID
    plan_id: uuid.UUID
    promotion_id: uuid.UUID
    snapshot: dict
    cycles_granted: int
    cycles_consumed: int
    is_active: bool
    confirmed_at: datetime

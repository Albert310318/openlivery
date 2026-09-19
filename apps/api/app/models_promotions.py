"""Subscription economics only; no payment execution or enrollment hooks."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_uuid, now_utc


class SubscriptionPromotion(Base):
    __tablename__ = "subscription_promotions"
    __table_args__ = (
        CheckConstraint("value <= 9999999999.99", name="ck_sp_finite_value"),
        CheckConstraint("code = upper(trim(code)) AND length(code) > 0", name="ck_sp_code"),
        CheckConstraint("(discount_type = 'PERCENTAGE' AND value > 0 AND value <= 100) OR (discount_type = 'FIXED' AND value > 0 AND currency IS NOT NULL)", name="ck_sp_discount"),
        CheckConstraint("currency IS NULL OR (length(currency) = 3 AND currency = upper(currency))", name="ck_sp_currency"),
        CheckConstraint("plan_scope IN ('ALL', 'SELECTED')", name="ck_sp_scope"),
        CheckConstraint("(duration = 'FIRST_PAID_MONTH' AND cycles = 1) OR (duration = 'NEXT_N_RENEWALS' AND cycles > 0)", name="ck_sp_duration"),
        CheckConstraint("ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at", name="ck_sp_dates"),
        CheckConstraint("max_total_uses IS NULL OR max_total_uses > 0", name="ck_sp_total_limit"),
        CheckConstraint("max_uses_per_client IS NULL OR max_uses_per_client > 0", name="ck_sp_client_limit"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    discount_type: Mapped[str] = mapped_column(String(10))
    value: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    plan_scope: Mapped[str] = mapped_column(String(8))
    duration: Mapped[str] = mapped_column(String(20))
    cycles: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_total_uses: Mapped[int | None] = mapped_column(Integer)
    max_uses_per_client: Mapped[int | None] = mapped_column(Integer().evaluates_none(), default=1, server_default="1")
    restricted_client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class SubscriptionPromotionPlan(Base):
    __tablename__ = "subscription_promotion_plans"
    promotion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subscription_promotions.id", ondelete="CASCADE"), primary_key=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"), primary_key=True)


class SubscriptionPromotionRedemption(Base):
    """Only future server-confirmed contracts may insert these records."""
    __tablename__ = "subscription_promotion_redemptions"
    __table_args__ = (
        UniqueConstraint("id", "client_id", "subscription_id", "plan_id", "promotion_id", name="uq_spr_identity"),
        CheckConstraint("cycles_granted > 0 AND cycles_consumed >= 0 AND cycles_consumed <= cycles_granted", name="ck_spr_cycles"),
        Index("uq_spr_active_subscription", "subscription_id", unique=True,
              postgresql_where=text("is_active"), sqlite_where=text("is_active = 1")),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))
    subscription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client_subscriptions.id", ondelete="RESTRICT"))
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    promotion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subscription_promotions.id", ondelete="RESTRICT"), index=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    cycles_granted: Mapped[int] = mapped_column(Integer)
    cycles_consumed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubscriptionCharge(Base):
    __tablename__ = "subscription_charges"
    __table_args__ = (
        UniqueConstraint("subscription_id", "cycle_number", name="uq_sc_cycle"),
        ForeignKeyConstraint(["redemption_id", "client_id", "subscription_id", "plan_id", "promotion_id"],
                             ["subscription_promotion_redemptions." + c for c in ("id", "client_id", "subscription_id", "plan_id", "promotion_id")], ondelete="RESTRICT", name="fk_sc_redemption_identity"),
        CheckConstraint("cycle_number > 0 AND period_end > period_start", name="ck_sc_period"),
        CheckConstraint("original_price <= 9999999999.99", name="ck_sc_finite_price"),
        CheckConstraint("original_price >= 0 AND discount >= 0 AND discount <= original_price AND total_final = original_price - discount AND total_final >= 0", name="ck_sc_amounts"),
        CheckConstraint("length(currency) = 3 AND currency = upper(currency)", name="ck_sc_currency"),
        CheckConstraint("(redemption_id IS NULL AND promotion_id IS NULL AND discount = 0) OR (redemption_id IS NOT NULL AND promotion_id IS NOT NULL)", name="ck_sc_promotion"),
        CheckConstraint("status IN ('ISSUED', 'PAID', 'VOID')", name="ck_sc_status"),
        CheckConstraint("(status = 'PAID' AND paid_at IS NOT NULL AND paid_at >= issued_at) OR (status <> 'PAID' AND paid_at IS NULL)", name="ck_sc_paid"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))
    subscription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client_subscriptions.id", ondelete="RESTRICT"))
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    promotion_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subscription_promotions.id", ondelete="RESTRICT"))
    redemption_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subscription_promotion_redemptions.id", ondelete="RESTRICT"))
    cycle_number: Mapped[int] = mapped_column(Integer)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    currency: Mapped[str] = mapped_column(String(3))
    original_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    total_final: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(10))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)


# ORM guards also protect application writes in SQLite tests. PostgreSQL triggers
# in 0023 enforce the same rules for SQL outside the ORM.
from sqlalchemy import event, inspect, select  # noqa: E402


@event.listens_for(SubscriptionPromotionRedemption, "before_update")
@event.listens_for(SubscriptionCharge, "before_update")
def preserve_history(mapper, connection, target):
    mutable = {"cycles_consumed", "is_active"} if isinstance(target, SubscriptionPromotionRedemption) else {"status", "paid_at"}
    previous = connection.execute(select(target.__table__).where(target.__table__.c.id == target.id)).mappings().one()
    for column in target.__table__.columns:
        if column.name not in mutable and inspect(target).attrs[column.name].history.has_changes():
            raise ValueError("Economic history is immutable")
    if isinstance(target, SubscriptionPromotionRedemption):
        if target.cycles_consumed < previous["cycles_consumed"] or (not previous["is_active"] and target.is_active):
            raise ValueError("Redemption cannot be reset")
    elif previous["status"] != "ISSUED" and any(inspect(target).attrs[c].history.has_changes() for c in mutable):
        raise ValueError("Final charge is immutable")


@event.listens_for(SubscriptionPromotionRedemption, "before_delete")
@event.listens_for(SubscriptionCharge, "before_delete")
def prevent_history_deletion(mapper, connection, target):
    raise ValueError("Economic history cannot be deleted")

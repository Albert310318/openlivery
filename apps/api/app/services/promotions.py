"""Read-only eligibility. No reservation, redemption, charge or trial mutation."""
import uuid
from datetime import timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Client, ClientSubscription, Plan, now_utc
from ..models_promotions import SubscriptionCharge, SubscriptionPromotion as Promotion, SubscriptionPromotionPlan as PromotionPlan, SubscriptionPromotionRedemption as Redemption
from ..schemas_promotions import PromotionOut, PromotionPreviewOut, PromotionPreviewRequest, PromotionWrite


def promotion_out(db, promotion):
    values = {name: getattr(promotion, name) for name in PromotionWrite.model_fields if name != "plan_ids"}
    values["plan_ids"] = list(db.scalars(select(PromotionPlan.plan_id).where(PromotionPlan.promotion_id == promotion.id)).all())
    return PromotionOut(id=promotion.id, **values)


def save_promotion(db: Session, payload: PromotionWrite, promotion_id: uuid.UUID | None = None):
    promotion = db.get(Promotion, promotion_id, with_for_update=True) if promotion_id else Promotion()
    if promotion is None:
        raise HTTPException(404, "Promotion not found")
    if payload.restricted_client_id and db.get(Client, payload.restricted_client_id) is None:
        raise HTTPException(404, "Client not found")
    if any(db.get(Plan, plan_id) is None for plan_id in payload.plan_ids):
        raise HTTPException(422, "Unknown plan")
    for key, value in payload.model_dump(exclude={"plan_ids"}).items():
        setattr(promotion, key, value)
    db.add(promotion)
    try:
        db.flush()
        db.execute(delete(PromotionPlan).where(PromotionPlan.promotion_id == promotion.id))
        db.add_all(PromotionPlan(promotion_id=promotion.id, plan_id=plan_id) for plan_id in payload.plan_ids)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Promotion conflicts with existing data") from exc
    return promotion_out(db, promotion)


def granted_snapshot(db: Session, promotion: Promotion) -> dict:
    """For a future confirmed contracting transaction; Decimal encoded as strings."""
    return promotion_out(db, promotion).model_dump(mode="json")


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def preview(db: Session, client_id: uuid.UUID, payload: PromotionPreviewRequest):
    if db.get(Client, client_id) is None:
        raise HTTPException(404, "Client not found")
    promotion = db.scalar(select(Promotion).where(Promotion.code == payload.code))
    plan = db.get(Plan, payload.plan_id)
    now = now_utc()
    if promotion is None or not promotion.is_active or (
        promotion.starts_at and now < _utc(promotion.starts_at)
    ) or (promotion.ends_at and now >= _utc(promotion.ends_at)):
        raise HTTPException(422, "Code is not applicable")
    if plan is None or not plan.is_active or not plan.available_for_new_subscriptions or plan.monthly_price is None:
        raise HTTPException(422, "Plan is not available")
    if promotion.restricted_client_id and promotion.restricted_client_id != client_id:
        raise HTTPException(422, "Code is not applicable")
    if promotion.plan_scope == "SELECTED" and db.get(PromotionPlan, (promotion.id, plan.id)) is None:
        raise HTTPException(422, "Code is not applicable")
    if promotion.currency is not None and promotion.currency != plan.currency:
        raise HTTPException(422, "Code currency does not match plan")
    subscription = db.scalar(select(ClientSubscription).where(ClientSubscription.client_id == client_id))
    if subscription:
        if db.scalar(select(Redemption.id).where(Redemption.subscription_id == subscription.id, Redemption.is_active.is_(True)).limit(1)):
            raise HTTPException(409, "Subscription already has a promotion")
        if promotion.duration == "FIRST_PAID_MONTH" and db.scalar(select(SubscriptionCharge.id).where(
            SubscriptionCharge.subscription_id == subscription.id, SubscriptionCharge.status == "PAID").limit(1)):
            raise HTTPException(422, "First paid month has already occurred")
    total = db.scalar(select(func.count()).select_from(Redemption).where(Redemption.promotion_id == promotion.id))
    own = db.scalar(select(func.count()).select_from(Redemption).where(Redemption.promotion_id == promotion.id, Redemption.client_id == client_id))
    if (promotion.max_total_uses is not None and total >= promotion.max_total_uses) or (
        promotion.max_uses_per_client is not None and own >= promotion.max_uses_per_client
    ):
        raise HTTPException(422, "Code usage limit reached")
    original = plan.monthly_price
    discount = original * promotion.value / Decimal("100") if promotion.discount_type == "PERCENTAGE" else promotion.value
    discount = min(original, discount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return PromotionPreviewOut(code=promotion.code, plan_id=plan.id, currency=plan.currency,
                               original_price=original, discount=discount, total_final=original-discount,
                               duration=promotion.duration, cycles=promotion.cycles)

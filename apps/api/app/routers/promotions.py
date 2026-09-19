import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Client, User
from ..models_promotions import SubscriptionPromotion, SubscriptionPromotionRedemption
from ..schemas_promotions import PromotionOut, PromotionPreviewOut, PromotionPreviewRequest, PromotionWrite, RedemptionOut
from ..services.promotions import preview, promotion_out, save_promotion
from .portal import _portal_client

router = APIRouter(tags=["Subscription promotions"])


def vendiq_admin(user: User = Depends(get_current_user)):
    # Separate privilege: never inferred from agency admin role or JWT claims.
    if not user.is_vendiq_admin:
        raise HTTPException(403, "VENDIQ general administrator required")
    return user


@router.post("/subscription-promotions", response_model=PromotionOut, status_code=201)
def create(payload: PromotionWrite, db: Session = Depends(get_db), user: User = Depends(vendiq_admin)):
    return save_promotion(db, payload)


@router.put("/subscription-promotions/{promotion_id}", response_model=PromotionOut)
def update(promotion_id: uuid.UUID, payload: PromotionWrite, db: Session = Depends(get_db), user: User = Depends(vendiq_admin)):
    return save_promotion(db, payload, promotion_id)


@router.get("/subscription-promotions", response_model=list[PromotionOut])
def listing(db: Session = Depends(get_db), user: User = Depends(vendiq_admin),
            offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return [promotion_out(db, p) for p in db.scalars(select(SubscriptionPromotion).order_by(SubscriptionPromotion.code).offset(offset).limit(limit))]


@router.post("/clients/{client_id}/subscription-promotions/preview", response_model=PromotionPreviewOut)
def admin_preview(client_id: uuid.UUID, payload: PromotionPreviewRequest, db: Session = Depends(get_db), user: User = Depends(vendiq_admin)):
    return preview(db, client_id, payload)


@router.post("/portal/{slug}/subscription-promotions/preview", response_model=PromotionPreviewOut)
def portal_preview(payload: PromotionPreviewRequest, client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return preview(db, client.id, payload)


def redemptions(db, client_id, offset, limit):
    return db.scalars(select(SubscriptionPromotionRedemption).where(SubscriptionPromotionRedemption.client_id == client_id)
                      .order_by(SubscriptionPromotionRedemption.confirmed_at, SubscriptionPromotionRedemption.id).offset(offset).limit(limit)).all()


@router.get("/clients/{client_id}/subscription-promotion-redemptions", response_model=list[RedemptionOut])
def admin_redemptions(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(vendiq_admin),
                      offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    if db.get(Client, client_id) is None:
        raise HTTPException(404, "Client not found")
    return redemptions(db, client_id, offset, limit)


@router.get("/portal/{slug}/subscription-promotion-redemptions", response_model=list[RedemptionOut])
def portal_redemptions(client: Client = Depends(_portal_client), db: Session = Depends(get_db),
                       offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return redemptions(db, client.id, offset, limit)

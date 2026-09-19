from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import uuid

from ..database import get_db
from ..deps import get_current_user
from ..models import Client, User
from ..schemas_subscriptions import PlanOut, SubscriptionOut, SubscriptionWrite
from ..services.subscriptions import admin_client, available_plans, save_subscription, subscription_for_client
from .portal import _portal_client

router = APIRouter(tags=["Subscriptions"])


@router.get("/plans", response_model=list[PlanOut])
def list_plans(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return available_plans(db)


@router.get("/clients/{client_id}/subscription", response_model=SubscriptionOut | None)
def get_subscription(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    admin_client(db, user, client_id)
    return subscription_for_client(db, client_id)


@router.put("/clients/{client_id}/subscription", response_model=SubscriptionOut)
def put_subscription(client_id: uuid.UUID, payload: SubscriptionWrite,
                     db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return save_subscription(db, user, client_id, payload)


@router.get("/portal/{slug}/subscription", response_model=SubscriptionOut | None)
def portal_subscription(client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return subscription_for_client(db, client.id)


@router.get("/portal/{slug}/plans", response_model=list[PlanOut])
def portal_plans(client: Client = Depends(_portal_client), db: Session = Depends(get_db)):
    return available_plans(db)

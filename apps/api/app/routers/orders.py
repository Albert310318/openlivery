import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import get_current_user
from ..models import Client, Conversation, Message, User
from ..models_orders import PaymentNotification, RestaurantDeliveryNotification, RestaurantOrder, RestaurantTableAccount
from ..models_restaurant import RestaurantModality, RestaurantPaymentMethod, RestaurantStaff
from ..schemas_orders import (
    OrderPaymentUpdate,
    PaymentRejectionInput,
    OrderStatusUpdate,
    PaymentNotificationCreate,
    PaymentNotificationOut,
    DeliveryOrderOut,
    DeliveryNotificationOut,
    RestaurantOrderOut,
    TableAccountOut,
    WaiterOrderItemUpdate,
    WaiterOrderCreate,
)
from ..services.payment_notifications import (
    DuplicatePaymentNotificationError,
    payment_verification_status,
    record_payment_notification,
    requires_payment_notification,
)
from ..services.restaurant_orders import create_order, recalculate_order_totals, restaurant_access
from ..services.restaurant_fulfillment import delivery_order_view, notify_delivery_ready, notify_pickup_ready, pickup_ready_text
from ..services.whatsapp import send_channel_message

router = APIRouter(prefix="/restaurants", tags=["Restaurant Orders"])
logger = logging.getLogger(__name__)


def _order(db: Session, client_id: uuid.UUID, order_id: uuid.UUID) -> RestaurantOrder:
    row = db.scalar(
        select(RestaurantOrder)
        .options(selectinload(RestaurantOrder.items))
        .where(RestaurantOrder.id == order_id, RestaurantOrder.client_id == client_id)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    row.payment_verification_status = payment_verification_status(db, row)
    return row


def _decorate_orders(db: Session, orders: list[RestaurantOrder]) -> list[RestaurantOrder]:
    for row in orders:
        row.payment_verification_status = payment_verification_status(db, row)
    return orders


def _editable_waiter_order(order: RestaurantOrder, user: User, role: str) -> None:
    if role == "waiter" and order.created_by_user_id != user.id:
        raise HTTPException(status_code=403, detail="A waiter can only edit their own order before preparation")
    if order.payment_status == "confirmed" or order.order_status not in {"pending_payment", "confirmed"}:
        raise HTTPException(status_code=409, detail="This order can no longer be edited")


@router.get("/{client_id}/orders", response_model=list[RestaurantOrderOut])
def list_orders(
    client_id: uuid.UUID,
    order_status: str | None = Query(default=None, alias="status"),
    source: str | None = None,
    payment_status: str | None = None,
    search: str | None = None,
    view_as_role: str | None = Query(default=None),
    view_as_staff_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _, role = restaurant_access(
        db,
        user,
        client_id,
        panel_role=view_as_role,
        panel_staff_id=view_as_staff_id,
    )
    waiter_user_id = user.id
    if view_as_role == "waiter" and view_as_staff_id:
        waiter_user_id = db.scalar(select(RestaurantStaff.user_id).where(RestaurantStaff.id == view_as_staff_id)) or user.id
    query = select(RestaurantOrder).options(selectinload(RestaurantOrder.items)).where(RestaurantOrder.client_id == client_id)
    if role == "waiter":
        query = query.where(RestaurantOrder.created_by_user_id == waiter_user_id)
    elif role == "kitchen":
        query = query.where(
            or_(
                and_(
                    RestaurantOrder.order_status.in_(["confirmed", "preparing"]),
                    (RestaurantOrder.source == "waiter") | (RestaurantOrder.payment_status == "confirmed") | (RestaurantOrder.payment_method == "cash"),
                ),
                and_(
                    RestaurantOrder.ready_at.is_not(None),
                    RestaurantOrder.order_status.in_(["ready", "on_the_way", "delivered", "served", "closed"]),
                ),
            ),
        )
    elif role == "delivery":
        query = query.where(RestaurantOrder.modality == "delivery", RestaurantOrder.order_status.in_(["ready", "on_the_way"]))
    elif role == "cashier":
        query = query.where((RestaurantOrder.modality == "dine_in") | (RestaurantOrder.payment_status == "pending"))
    if order_status == "pending_payment" and role == "admin":
        query = query.where(
            or_(
                RestaurantOrder.order_status == "pending_payment",
                and_(
                    RestaurantOrder.modality == "dine_in",
                    RestaurantOrder.payment_status == "pending",
                    RestaurantOrder.order_status != "cancelled",
                ),
            )
        )
    elif order_status:
        query = query.where(RestaurantOrder.order_status == order_status)
    if source:
        query = query.where(RestaurantOrder.source == source)
    if payment_status:
        query = query.where(RestaurantOrder.payment_status == payment_status)
    if search:
        query = query.where(RestaurantOrder.table_number.ilike(f"%{search.strip()}%"))
    return _decorate_orders(db, db.scalars(query.order_by(RestaurantOrder.created_at.desc()).limit(200)).unique().all())


@router.get("/{client_id}/delivery-orders", response_model=list[DeliveryOrderOut])
def list_delivery_orders(
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    restaurant_access(db, user, client_id, {"delivery"})
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.scalars(
        select(RestaurantOrder)
        .options(selectinload(RestaurantOrder.items))
        .where(
            RestaurantOrder.client_id == client_id,
            RestaurantOrder.modality == "delivery",
            or_(
                RestaurantOrder.order_status.in_(["ready", "on_the_way"]),
                and_(RestaurantOrder.order_status == "delivered", RestaurantOrder.delivered_at >= today),
            ),
        )
        .order_by(RestaurantOrder.ready_at.desc().nullslast(), RestaurantOrder.created_at.desc())
    ).unique().all()
    return [delivery_order_view(row) for row in rows]


@router.get("/{client_id}/delivery-notifications", response_model=list[DeliveryNotificationOut])
def list_delivery_notifications(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    restaurant_access(db, user, client_id, {"admin"})
    historical_ready_orders = db.scalars(
        select(RestaurantOrder)
        .where(
            RestaurantOrder.client_id == client_id,
            RestaurantOrder.modality == "delivery",
            RestaurantOrder.order_status.in_({"ready", "on_the_way", "delivered"}),
            RestaurantOrder.ready_at.is_not(None),
        )
        .order_by(RestaurantOrder.ready_at.desc())
        .limit(100)
    ).all()
    existing_order_ids = set(
        db.scalars(
            select(RestaurantDeliveryNotification.order_id).where(
                RestaurantDeliveryNotification.client_id == client_id,
            )
        ).all()
    )
    modality = db.scalar(select(RestaurantModality).where(RestaurantModality.client_id == client_id))
    recipient = (modality.delivery_whatsapp or "").strip() if modality else ""
    missing_orders = [order for order in historical_ready_orders if order.id not in existing_order_ids]
    if missing_orders:
        db.execute(
            postgres_insert(RestaurantDeliveryNotification)
            .values([
                {
                    "client_id": client_id,
                    "order_id": order.id,
                    "recipient": recipient or "unknown",
                    "status": "pending" if recipient else "error",
                    "error_message": None if recipient else "No hay un WhatsApp configurado para el responsable de Delivery.",
                }
                for order in missing_orders
            ])
            .on_conflict_do_nothing(index_elements=["order_id"])
        )
        db.commit()
    rows = db.execute(
        select(RestaurantDeliveryNotification, RestaurantOrder.order_number)
        .join(RestaurantOrder, RestaurantOrder.id == RestaurantDeliveryNotification.order_id)
        .where(RestaurantDeliveryNotification.client_id == client_id)
        .order_by(RestaurantDeliveryNotification.updated_at.desc())
        .limit(100)
    ).all()
    return [
        {
            "id": notification.id,
            "client_id": notification.client_id,
            "order_id": notification.order_id,
            "order_number": order_number,
            "recipient": notification.recipient,
            "provider": notification.provider,
            "status": notification.status,
            "external_message_id": notification.external_message_id,
            "error_message": notification.error_message,
            "attempts": notification.attempts,
            "sent_at": notification.sent_at,
            "updated_at": notification.updated_at,
        }
        for notification, order_number in rows
    ]


@router.post("/{client_id}/orders/{order_id}/delivery-notification/retry", response_model=DeliveryNotificationOut)
async def retry_delivery_notification(client_id: uuid.UUID, order_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    restaurant_access(db, user, client_id, {"admin"})
    order = _order(db, client_id, order_id)
    if order.modality != "delivery" or order.order_status not in {"ready", "on_the_way", "delivered"}:
        raise HTTPException(status_code=409, detail="El pedido aún no está disponible para notificar a Delivery")
    await notify_delivery_ready(db, order)
    row = db.scalar(select(RestaurantDeliveryNotification).where(RestaurantDeliveryNotification.order_id == order.id))
    if not row:
        raise HTTPException(status_code=500, detail="No se pudo registrar la notificación")
    return {**row.__dict__, "order_number": order.order_number}


@router.get("/{client_id}/orders/{order_id}", response_model=RestaurantOrderOut)
def get_order(
    client_id: uuid.UUID,
    order_id: uuid.UUID,
    view_as_role: str | None = Query(default=None),
    view_as_staff_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    restaurant_access(
        db,
        user,
        client_id,
        panel_role=view_as_role,
        panel_staff_id=view_as_staff_id,
    )
    return _order(db, client_id, order_id)


@router.post("/{client_id}/orders", response_model=RestaurantOrderOut, status_code=status.HTTP_201_CREATED)
def create_waiter_order(client_id: uuid.UUID, payload: WaiterOrderCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, role = restaurant_access(db, user, client_id, {"admin", "waiter"})
    return create_order(
        db,
        client_id=client_id,
        source="waiter",
        modality="dine_in",
        items=payload.items,
        table_number=payload.table_number,
        customer_name=payload.customer_name,
        notes=payload.notes,
        created_by_user_id=user.id,
        idempotency_key=payload.idempotency_key or f"waiter:{user.id}:{uuid.uuid4().hex}",
    )


@router.post(
    "/{client_id}/payment-notifications",
    response_model=PaymentNotificationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_payment_notification(
    client_id: uuid.UUID,
    payload: PaymentNotificationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Accept one already-parsed notification for manual/future adapter testing."""

    restaurant_access(db, user, client_id, {"admin", "cashier"})
    payment_method = None
    if payload.payment_method_id:
        payment_method = db.scalar(
            select(RestaurantPaymentMethod).where(
                RestaurantPaymentMethod.id == payload.payment_method_id,
                RestaurantPaymentMethod.client_id == client_id,
            )
        )
        if not payment_method:
            raise HTTPException(status_code=404, detail="Payment method not found")
    try:
        return record_payment_notification(
            db,
            client_id=client_id,
            payment_method=payment_method,
            notification_email=str(payload.notification_email) if payload.notification_email else None,
            external_operation_id=payload.external_operation_id,
            source_message_id=payload.source_message_id,
            amount=payload.amount,
            occurred_at=payload.occurred_at,
            received_at=payload.received_at,
            sender=payload.sender,
            subject=payload.subject,
            metadata=payload.metadata,
        )
    except DuplicatePaymentNotificationError as exc:
        raise HTTPException(status_code=409, detail=f"Payment operation already received: {exc}") from exc


@router.get("/{client_id}/payment-notifications", response_model=list[PaymentNotificationOut])
def list_payment_notifications(
    client_id: uuid.UUID,
    notification_status: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    restaurant_access(db, user, client_id, {"admin", "cashier"})
    query = select(PaymentNotification).where(PaymentNotification.client_id == client_id)
    if notification_status:
        query = query.where(PaymentNotification.status == notification_status)
    return db.scalars(query.order_by(PaymentNotification.received_at.desc()).limit(200)).all()


@router.patch("/{client_id}/orders/{order_id}/items/{item_id}", response_model=RestaurantOrderOut)
def update_waiter_order_item(
    client_id: uuid.UUID,
    order_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: WaiterOrderItemUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _, role = restaurant_access(db, user, client_id, {"admin", "waiter"})
    order = _order(db, client_id, order_id)
    _editable_waiter_order(order, user, role)
    item = next((row for row in order.items if row.id == item_id and not row.is_cancelled), None)
    if not item:
        raise HTTPException(status_code=404, detail="Order item not found")
    item.quantity = payload.quantity
    item.observations = payload.observations
    item.line_subtotal = item.unit_price * payload.quantity
    recalculate_order_totals(order)
    db.commit()
    return _order(db, client_id, order_id)


@router.delete("/{client_id}/orders/{order_id}/items/{item_id}", response_model=RestaurantOrderOut)
def cancel_waiter_order_item(
    client_id: uuid.UUID,
    order_id: uuid.UUID,
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _, role = restaurant_access(db, user, client_id, {"admin", "waiter"})
    order = _order(db, client_id, order_id)
    _editable_waiter_order(order, user, role)
    item = next((row for row in order.items if row.id == item_id and not row.is_cancelled), None)
    if not item:
        raise HTTPException(status_code=404, detail="Order item not found")
    item.is_cancelled = True
    item.cancelled_at = datetime.now(timezone.utc)
    recalculate_order_totals(order)
    if order.order_status == "cancelled" and order.table_account_id:
        remaining = db.scalar(
            select(RestaurantOrder.id)
            .where(
                RestaurantOrder.table_account_id == order.table_account_id,
                RestaurantOrder.order_status != "cancelled",
            )
            .limit(1)
        )
        if not remaining:
            account = db.get(RestaurantTableAccount, order.table_account_id)
            if account:
                account.status = "closed"
                account.is_open = False
                account.closed_at = datetime.now(timezone.utc)
    db.commit()
    return _order(db, client_id, order_id)


async def _notify_payment_decision(db: Session, order: RestaurantOrder, user: User, text: str) -> None:
    if not order.conversation_id:
        return
    conversation = db.get(Conversation, order.conversation_id)
    if not conversation:
        return
    external_message_id = await send_channel_message(db, conversation, text)
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=text,
            sender_type="human",
            sender_name=user.name,
            external_message_id=external_message_id,
        )
    )


@router.post("/{client_id}/orders/{order_id}/payment", response_model=RestaurantOrderOut)
async def confirm_payment(client_id: uuid.UUID, order_id: uuid.UUID, payload: OrderPaymentUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, role = restaurant_access(db, user, client_id, {"admin", "cashier"})
    order = _order(db, client_id, order_id)
    if order.order_status == "cancelled":
        raise HTTPException(status_code=409, detail="Cannot confirm payment for a cancelled order")
    if order.source == "whatsapp":
        restaurant_access(db, user, client_id, {"admin"})
        if order.payment_status == "confirmed":
            return _order(db, client_id, order_id)
        if order.payment_review_status != "review" or not order.payment_reference or order.reported_payment_amount is None:
            raise HTTPException(status_code=409, detail="The order has no payment submitted for manual review")
        now = datetime.now(timezone.utc)
        order.payment_status = "confirmed"
        order.payment_method = order.payment_method or "other"
        order.payment_review_status = "confirmed"
        order.payment_rejection_reason = None
        order.payment_confirmed_at = now
        order.payment_confirmed_by_user_id = user.id
        order.order_status = "confirmed"
        db.commit()
        try:
            confirmation_text = (
                f"Pago confirmado para el pedido {order.order_number}. Tu pedido pasó a preparación. "
                "El personal de delivery se comunicará contigo para coordinar directamente el costo de la entrega."
                if order.modality == "delivery"
                else f"Pago confirmado para el pedido {order.order_number}. Tu pedido pasó a preparación."
            )
            await _notify_payment_decision(
                db,
                order,
                user,
                confirmation_text,
            )
            db.commit()
        except Exception:
            db.rollback()
        return _order(db, client_id, order_id)
    now = datetime.now(timezone.utc)
    if order.table_account_id:
        account = db.scalar(select(RestaurantTableAccount).where(RestaurantTableAccount.id == order.table_account_id, RestaurantTableAccount.client_id == client_id).options(selectinload(RestaurantTableAccount.orders).selectinload(RestaurantOrder.items)))
        if account:
            open_orders = [
                row
                for row in account.orders
                if row.order_status != "cancelled" and any(not item.is_cancelled for item in row.items)
            ]
            account_total = sum((row.total for row in open_orders), Decimal("0"))
            if account_total <= Decimal("0"):
                raise HTTPException(status_code=409, detail="Cannot confirm payment for a zero-value table account")
            if any(row.order_status in {"preparing", "on_the_way"} for row in open_orders):
                raise HTTPException(status_code=409, detail="The table still has orders being prepared")
            for row in open_orders:
                row.payment_status = "confirmed"
                row.payment_method = payload.payment_method
                row.payment_reference = payload.payment_reference
                row.receipt_submitted = payload.receipt_submitted
                row.payment_confirmed_at = now
                row.payment_confirmed_by_user_id = user.id
                row.order_status = "closed"
            account.status = "closed"
            account.is_open = False
            account.paid_at = now
            account.closed_at = now
            account.cashier_user_id = user.id
    else:
        if order.total <= Decimal("0"):
            raise HTTPException(status_code=409, detail="Cannot confirm payment for a zero-value order")
        order.payment_status = "confirmed"
        order.payment_method = payload.payment_method
        order.payment_reference = payload.payment_reference
        order.receipt_submitted = payload.receipt_submitted
        order.payment_confirmed_at = now
        order.payment_confirmed_by_user_id = user.id
        if order.order_status == "pending_payment":
            order.order_status = "confirmed"
    db.commit()
    return _order(db, client_id, order_id)


@router.post("/{client_id}/orders/{order_id}/payment/reject", response_model=RestaurantOrderOut)
async def reject_payment(
    client_id: uuid.UUID,
    order_id: uuid.UUID,
    payload: PaymentRejectionInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    restaurant_access(db, user, client_id, {"admin"})
    order = _order(db, client_id, order_id)
    if order.source != "whatsapp":
        raise HTTPException(status_code=409, detail="Manual voucher rejection is only for customer orders")
    if order.payment_status == "confirmed":
        raise HTTPException(status_code=409, detail="Cannot reject an already confirmed payment")
    now = datetime.now(timezone.utc)
    order.payment_status = "pending"
    order.payment_review_status = "rejected"
    order.payment_rejection_reason = (payload.reason or "Pago no confirmado").strip()[:2000]
    order.payment_rejected_at = now
    order.order_status = "pending_payment"
    db.commit()
    try:
        await _notify_payment_decision(
            db,
            order,
            user,
            f"No pudimos confirmar el pago del pedido {order.order_number}. Revisa el monto y el número de operación y reenvía tu comprobante, por favor.",
        )
        db.commit()
    except Exception:
        db.rollback()
    return _order(db, client_id, order_id)


@router.get("/{client_id}/orders/{order_id}/payment-receipt")
def get_payment_receipt(
    client_id: uuid.UUID,
    order_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    restaurant_access(db, user, client_id, {"admin"})
    order = db.scalar(
        select(RestaurantOrder).where(RestaurantOrder.id == order_id, RestaurantOrder.client_id == client_id)
    )
    if not order or not order.payment_receipt_data:
        raise HTTPException(status_code=404, detail="Payment receipt not found")
    filename = (order.payment_receipt_filename or "comprobante").replace('"', "")
    return Response(
        content=order.payment_receipt_data,
        media_type=order.payment_receipt_mime or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.patch("/{client_id}/orders/{order_id}/status")
async def change_order_status(client_id: uuid.UUID, order_id: uuid.UUID, payload: OrderStatusUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, role = restaurant_access(db, user, client_id)
    order = _order(db, client_id, order_id)
    target = payload.status
    if role == "waiter" and target != "served":
        raise HTTPException(status_code=403, detail="A waiter can only mark ready table orders as served")
    if role == "kitchen" and target not in {"preparing", "ready"}:
        raise HTTPException(status_code=403, detail="Kitchen can only update preparation status")
    if role == "delivery" and target not in {"on_the_way", "delivered"}:
        raise HTTPException(status_code=403, detail="Delivery can only update delivery status")
    if role == "cashier":
        raise HTTPException(status_code=403, detail="This role cannot update order status")
    if target == "preparing" and order.order_status not in {"confirmed", "preparing"}:
        raise HTTPException(status_code=409, detail="The order is not available for preparation")
    if target == "ready" and order.order_status not in {"confirmed", "preparing"}:
        raise HTTPException(status_code=409, detail="The order is not being prepared")
    if target == "on_the_way" and (order.modality != "delivery" or order.order_status != "ready"):
        raise HTTPException(status_code=409, detail="Only ready delivery orders can leave for delivery")
    if target == "delivered":
        pickup_ready = role == "admin" and order.modality == "pickup" and order.order_status == "ready"
        delivery_in_transit = order.modality == "delivery" and order.order_status == "on_the_way"
        if not pickup_ready and not delivery_in_transit:
            raise HTTPException(status_code=409, detail="The order is not ready to be delivered or picked up")
    if target == "served" and (order.modality != "dine_in" or order.order_status != "ready"):
        raise HTTPException(status_code=409, detail="Only ready table orders can be marked as served")
    order.order_status = target
    if target == "ready":
        order.ready_at = datetime.now(timezone.utc)
    if target == "delivered":
        order.delivered_at = datetime.now(timezone.utc)
    db.commit()
    if target == "ready":
        try:
            if order.modality == "pickup":
                sent = await notify_pickup_ready(db, order)
                if sent and order.conversation_id:
                    conversation = db.get(Conversation, order.conversation_id)
                    client = db.get(Client, order.client_id)
                    if conversation:
                        db.add(Message(
                            conversation_id=conversation.id,
                            role="assistant",
                            content=pickup_ready_text(order, client.name if client else "el restaurante"),
                            sender_type="human",
                            sender_name="Cocina",
                        ))
            elif order.modality == "delivery":
                await notify_delivery_ready(db, order)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Could not send ready notification for order %s", order.order_number)
    if role == "delivery":
        return delivery_order_view(_order(db, client_id, order_id))
    return _order(db, client_id, order_id)


@router.get("/{client_id}/table-accounts", response_model=list[TableAccountOut])
def list_table_accounts(
    client_id: uuid.UUID,
    view_as_role: str | None = Query(default=None),
    view_as_staff_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    restaurant_access(
        db,
        user,
        client_id,
        {"admin", "cashier", "waiter"},
        panel_role=view_as_role,
        panel_staff_id=view_as_staff_id,
    )
    accounts = db.scalars(
        select(RestaurantTableAccount)
        .options(selectinload(RestaurantTableAccount.orders).selectinload(RestaurantOrder.items))
        .where(RestaurantTableAccount.client_id == client_id, RestaurantTableAccount.is_open.is_(True))
        .order_by(RestaurantTableAccount.table_number)
    ).unique().all()
    result = []
    for account in accounts:
        active_orders = [
            row for row in account.orders
            if row.order_status != "cancelled" and any(not item.is_cancelled for item in row.items)
        ]
        if not active_orders:
            continue
        result.append({
            **{key: getattr(account, key) for key in ("id", "client_id", "table_number", "status", "is_open", "opened_at", "paid_at", "closed_at")},
            "subtotal": sum((row.subtotal for row in active_orders), Decimal("0")),
            "total": sum((row.total for row in active_orders), Decimal("0")),
            "orders": _decorate_orders(db, active_orders),
        })
    return result

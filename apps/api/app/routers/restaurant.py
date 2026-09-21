import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import (
    Client,
    RestaurantMenuItem,
    RestaurantOrder,
    RestaurantOrderItem,
    User,
    WhatsAppChannel,
    now_utc,
)
from ..services.leads import normalize_phone
from ..services.restaurant import is_restaurant_client, order_payload
from ..services.whatsapp import bridge_command


router = APIRouter(prefix="/restaurant", tags=["Restaurant"])


class RestaurantSettingsIn(BaseModel):
    currency: str = Field(default="PEN", min_length=3, max_length=3)
    payment_instructions: str = Field(default="", max_length=3000)
    kitchen_phone: str | None = Field(default=None, max_length=40)
    delivery_phone: str | None = Field(default=None, max_length=40)


class MenuItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    aliases: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=2000)
    price: Decimal = Field(ge=0)
    currency: str = Field(default="PEN", min_length=3, max_length=3)
    is_active: bool = True


class WaiterOrderItemIn(BaseModel):
    menu_item_id: uuid.UUID
    quantity: int = Field(ge=1, le=99)
    notes: str = Field(default="", max_length=500)


class WaiterOrderIn(BaseModel):
    table_label: str = Field(min_length=1, max_length=80)
    customer_name: str | None = Field(default=None, max_length=180)
    items: list[WaiterOrderItemIn] = Field(min_length=1, max_length=60)


class OrderStatusIn(BaseModel):
    status: str


def _client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    if not user.is_vendiq_admin and client.agency_id != user.agency_id:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def _order(db: Session, user: User, order_id: uuid.UUID) -> RestaurantOrder:
    order = db.get(RestaurantOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if not user.is_vendiq_admin and order.agency_id != user.agency_id:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _normalize_optional_phone(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        normalized = normalize_phone(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return normalized


@router.get("/clients/{client_id}/settings")
def get_settings(
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    return {
        "restaurant": is_restaurant_client(client),
        "currency": client.restaurant_currency,
        "payment_instructions": client.restaurant_payment_instructions,
        "kitchen_phone": client.restaurant_kitchen_phone,
        "delivery_phone": client.restaurant_delivery_phone,
    }


@router.patch("/clients/{client_id}/settings")
def update_settings(
    client_id: uuid.UUID,
    payload: RestaurantSettingsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    client.restaurant_currency = payload.currency.upper()
    client.restaurant_payment_instructions = payload.payment_instructions.strip()
    client.restaurant_kitchen_phone = _normalize_optional_phone(payload.kitchen_phone)
    client.restaurant_delivery_phone = _normalize_optional_phone(payload.delivery_phone)
    db.commit()
    return {
        "restaurant": is_restaurant_client(client),
        "currency": client.restaurant_currency,
        "payment_instructions": client.restaurant_payment_instructions,
        "kitchen_phone": client.restaurant_kitchen_phone,
        "delivery_phone": client.restaurant_delivery_phone,
    }


@router.get("/clients/{client_id}/menu")
def list_menu(
    client_id: uuid.UUID,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    query = select(RestaurantMenuItem).where(
        RestaurantMenuItem.agency_id == client.agency_id,
        RestaurantMenuItem.client_id == client.id,
    )
    if not include_inactive:
        query = query.where(RestaurantMenuItem.is_active.is_(True))
    rows = db.scalars(query.order_by(RestaurantMenuItem.name.asc())).all()
    return [
        {
            "id": str(item.id),
            "name": item.name,
            "aliases": item.aliases,
            "description": item.description,
            "price": str(item.price),
            "currency": item.currency,
            "is_active": item.is_active,
        }
        for item in rows
    ]


@router.post("/clients/{client_id}/menu")
def create_menu_item(
    client_id: uuid.UUID,
    payload: MenuItemIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    item = RestaurantMenuItem(
        agency_id=client.agency_id,
        client_id=client.id,
        name=payload.name.strip(),
        aliases=[alias.strip() for alias in payload.aliases if alias.strip()],
        description=payload.description.strip(),
        price=payload.price,
        currency=payload.currency.upper(),
        is_active=payload.is_active,
    )
    db.add(item)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A menu item with that name already exists") from exc
    db.refresh(item)
    return {"id": str(item.id), "name": item.name, "price": str(item.price), "currency": item.currency}


@router.patch("/menu/{item_id}")
def update_menu_item(
    item_id: uuid.UUID,
    payload: MenuItemIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = db.get(RestaurantMenuItem, item_id)
    if not item or (not user.is_vendiq_admin and item.agency_id != user.agency_id):
        raise HTTPException(status_code=404, detail="Menu item not found")
    item.name = payload.name.strip()
    item.aliases = [alias.strip() for alias in payload.aliases if alias.strip()]
    item.description = payload.description.strip()
    item.price = payload.price
    item.currency = payload.currency.upper()
    item.is_active = payload.is_active
    db.commit()
    return {"id": str(item.id), "name": item.name, "price": str(item.price), "currency": item.currency, "is_active": item.is_active}


@router.post("/clients/{client_id}/waiter-orders")
async def create_waiter_order(
    client_id: uuid.UUID,
    payload: WaiterOrderIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    if not is_restaurant_client(client):
        raise HTTPException(status_code=409, detail="Restaurant ordering is not enabled for this client")
    requested_ids = {item.menu_item_id for item in payload.items}
    menu_rows = list(
        db.scalars(
            select(RestaurantMenuItem).where(
                RestaurantMenuItem.id.in_(requested_ids),
                RestaurantMenuItem.agency_id == client.agency_id,
                RestaurantMenuItem.client_id == client.id,
                RestaurantMenuItem.is_active.is_(True),
            )
        ).all()
    )
    by_id = {item.id: item for item in menu_rows}
    if len(by_id) != len(requested_ids):
        raise HTTPException(status_code=422, detail="One or more menu items are invalid or inactive")

    order = RestaurantOrder(
        public_code=f"PED-{uuid.uuid4().hex[:8].upper()}",
        agency_id=client.agency_id,
        client_id=client.id,
        agent_id=None,
        conversation_id=None,
        created_by_user_id=user.id,
        source="waiter",
        table_label=payload.table_label.strip(),
        fulfillment_type="table",
        customer_name=payload.customer_name.strip() if payload.customer_name else None,
        status="kitchen",
        payment_status="pay_at_table",
        currency=client.restaurant_currency or "PEN",
    )
    db.add(order)
    db.flush()

    total = Decimal("0.00")
    for requested in payload.items:
        menu_item = by_id[requested.menu_item_id]
        line_total = menu_item.price * requested.quantity
        total += line_total
        db.add(
            RestaurantOrderItem(
                order_id=order.id,
                menu_item_id=menu_item.id,
                item_name=menu_item.name,
                unit_price=menu_item.price,
                quantity=requested.quantity,
                line_total=line_total,
                notes=requested.notes.strip(),
            )
        )
    order.subtotal = total
    order.total = total
    db.flush()

    if client.restaurant_kitchen_phone:
        try:
            await _send_staff_message(
                db,
                order,
                client.restaurant_kitchen_phone,
                _order_message(db, order, "👨‍🍳 PEDIDO DE MESA — PREPARAR"),
            )
            order.kitchen_sent_at = now_utc()
        except HTTPException:
            # Kitchen staff can still see the order in their portal.
            pass

    db.commit()
    db.refresh(order)
    return order_payload(db, order, client)


@router.get("/orders")
def list_orders(
    client_id: uuid.UUID | None = None,
    status: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(RestaurantOrder)
    if not user.is_vendiq_admin:
        query = query.where(RestaurantOrder.agency_id == user.agency_id)
    if client_id:
        query = query.where(RestaurantOrder.client_id == client_id)
    if status:
        query = query.where(RestaurantOrder.status == status)
    rows = db.scalars(query.order_by(RestaurantOrder.updated_at.desc()).limit(limit)).all()
    return [order_payload(db, order) for order in rows]


@router.get("/orders/{order_id}")
def get_order(
    order_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return order_payload(db, _order(db, user, order_id))


def _order_message(db: Session, order: RestaurantOrder, title: str) -> str:
    payload = order_payload(db, order)
    lines = [title, "", f"Pedido: {payload['code']}"]
    if payload.get("customer_name"):
        lines.append(f"Cliente: {payload['customer_name']}")
    if payload.get("customer_phone"):
        lines.append(f"WhatsApp: {payload['customer_phone']}")
    if payload.get("table"):
        lines.append(f"Mesa: {payload['table']}")
    if payload.get("delivery_address"):
        lines.append(f"Dirección: {payload['delivery_address']}")
    lines.append("")
    for item in payload["items"]:
        note = f" ({item['notes']})" if item.get("notes") else ""
        lines.append(f"{item['quantity']} x {item['name']}{note}")
    lines.extend(["", f"Total: {payload['currency']} {payload['total']}"])
    return "\n".join(lines)


async def _send_staff_message(db: Session, order: RestaurantOrder, destination: str, text: str) -> None:
    channel = db.scalar(
        select(WhatsAppChannel).where(
            WhatsAppChannel.client_id == order.client_id,
            WhatsAppChannel.agency_id == order.agency_id,
            WhatsAppChannel.is_enabled.is_(True),
            WhatsAppChannel.status == "connected",
        )
    )
    if not channel:
        raise HTTPException(status_code=409, detail="The restaurant WhatsApp channel is not connected")
    await bridge_command(
        "POST",
        f"/channels/{channel.id}/send",
        {"remote_jid": f"{destination.lstrip('+')}@s.whatsapp.net", "text": text},
    )


@router.post("/orders/{order_id}/confirm-payment")
async def confirm_payment(
    order_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = _order(db, user, order_id)
    if order.status != "payment_reported" or order.payment_status != "reported":
        raise HTTPException(status_code=409, detail="The customer has not reported payment for this order")
    client = db.get(Client, order.client_id)
    order.payment_status = "confirmed"
    order.payment_confirmed_at = now_utc()
    order.status = "paid"
    db.commit()

    if client and client.restaurant_kitchen_phone:
        try:
            await _send_staff_message(db, order, client.restaurant_kitchen_phone, _order_message(db, order, "👨‍🍳 PEDIDO PAGADO — PREPARAR"))
            order.status = "kitchen"
            order.kitchen_sent_at = now_utc()
            db.commit()
        except HTTPException:
            # Payment remains confirmed even if the operational notification fails.
            pass
    db.refresh(order)
    return order_payload(db, order, client)


@router.post("/orders/{order_id}/ready")
async def mark_ready(
    order_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = _order(db, user, order_id)
    if order.status not in ("kitchen", "paid"):
        raise HTTPException(status_code=409, detail="Only a paid/kitchen order can be marked ready")
    client = db.get(Client, order.client_id)
    order.status = "ready"
    order.ready_at = now_utc()
    db.commit()
    if order.fulfillment_type == "delivery" and client and client.restaurant_delivery_phone:
        try:
            await _send_staff_message(db, order, client.restaurant_delivery_phone, _order_message(db, order, "🛵 PEDIDO LISTO PARA DELIVERY"))
        except HTTPException:
            pass
    return order_payload(db, order, client)


@router.post("/orders/{order_id}/status")
def update_order_status(
    order_id: uuid.UUID,
    payload: OrderStatusIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    allowed = {"out_for_delivery", "served", "delivered", "cancelled"}
    if payload.status not in allowed:
        raise HTTPException(status_code=422, detail="Unsupported manual order status")
    order = _order(db, user, order_id)
    order.status = payload.status
    if payload.status in ("served", "delivered"):
        order.delivered_at = now_utc()
    db.commit()
    return order_payload(db, order)

import re
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Client,
    RestaurantMenuItem,
    RestaurantOrder,
    RestaurantOrderItem,
    User,
    now_utc,
)
from .leads import LeadContext, trusted_whatsapp_phone


OPEN_ORDER_STATUSES = (
    "draft",
    "awaiting_confirmation",
    "awaiting_payment",
    "payment_reported",
    "paid",
    "kitchen",
    "ready",
    "out_for_delivery",
    "served",
)


class RestaurantOrderError(ValueError):
    pass


def _plain(value: str) -> str:
    import unicodedata

    value = "".join(
        c for c in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(c)
    )
    return " ".join(re.findall(r"[a-z0-9]+", value))


def is_restaurant_client(client: Client | None) -> bool:
    if not client:
        return False
    text = _plain(f"{client.industry} {client.description}")
    return any(
        token in text
        for token in (
            "restaurant",
            "restaurante",
            "polleria",
            "polleria",
            "comida",
            "gastronomia",
            "cevicheria",
            "pizzeria",
            "cafeteria",
        )
    )


def _client(db: Session, context: LeadContext) -> Client:
    client = db.scalar(
        select(Client).where(
            Client.id == context.client_id,
            Client.agency_id == context.agency_id,
        )
    )
    if not client or not is_restaurant_client(client):
        raise RestaurantOrderError("Restaurant ordering is not enabled for this client")
    return client


def _source(context: LeadContext) -> str:
    if context.channel in ("whatsapp", "whatsapp_cloud"):
        return "whatsapp"
    if context.channel == "playground":
        return "playground"
    return "table"


def _current_order(db: Session, context: LeadContext) -> RestaurantOrder | None:
    return db.scalar(
        select(RestaurantOrder)
        .where(
            RestaurantOrder.conversation_id == context.conversation_id,
            RestaurantOrder.agency_id == context.agency_id,
            RestaurantOrder.client_id == context.client_id,
            RestaurantOrder.status.in_(OPEN_ORDER_STATUSES),
        )
        .order_by(RestaurantOrder.created_at.desc())
        .limit(1)
    )


def _items(db: Session, order: RestaurantOrder) -> list[RestaurantOrderItem]:
    return list(
        db.scalars(
            select(RestaurantOrderItem)
            .where(RestaurantOrderItem.order_id == order.id)
            .order_by(RestaurantOrderItem.created_at.asc())
        ).all()
    )


def _recalculate(db: Session, order: RestaurantOrder) -> None:
    subtotal = sum((item.line_total for item in _items(db, order)), Decimal("0.00"))
    order.subtotal = subtotal
    order.total = subtotal
    order.updated_at = now_utc()
    db.flush()


def _new_order(db: Session, context: LeadContext, client: Client) -> RestaurantOrder:
    phone = trusted_whatsapp_phone(db, context)
    order = RestaurantOrder(
        public_code=f"PED-{uuid.uuid4().hex[:8].upper()}",
        agency_id=context.agency_id,
        client_id=context.client_id,
        agent_id=context.agent_id,
        conversation_id=context.conversation_id,
        source=_source(context),
        customer_phone=phone,
        currency=client.restaurant_currency or "PEN",
    )
    db.add(order)
    db.flush()
    return order


def _ensure_editable_order(db: Session, context: LeadContext) -> tuple[Client, RestaurantOrder]:
    client = _client(db, context)
    order = _current_order(db, context)
    if order is None:
        order = _new_order(db, context, client)
    if order.status not in ("draft", "awaiting_confirmation"):
        raise RestaurantOrderError("The current order can no longer be edited")
    return client, order


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def order_payload(db: Session, order: RestaurantOrder, client: Client | None = None) -> dict[str, Any]:
    if client is None:
        client = db.get(Client, order.client_id)
    rows = _items(db, order)
    waiter = db.get(User, order.created_by_user_id) if order.created_by_user_id else None
    return {
        "order_id": str(order.id),
        "code": order.public_code,
        "source": order.source,
        "table": order.table_label,
        "fulfillment_type": order.fulfillment_type,
        "delivery_address": order.delivery_address,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "waiter_name": waiter.name if waiter else None,
        "status": order.status,
        "payment_status": order.payment_status,
        "payment_method": order.payment_method,
        "currency": order.currency,
        "subtotal": _money(order.subtotal),
        "total": _money(order.total),
        "payment_instructions": client.restaurant_payment_instructions if client else "",
        "items": [
            {
                "name": item.item_name,
                "quantity": item.quantity,
                "unit_price": _money(item.unit_price),
                "line_total": _money(item.line_total),
                "notes": item.notes,
            }
            for item in rows
        ],
    }


def menu_payload(db: Session, context: LeadContext, query: str | None = None) -> dict[str, Any]:
    client = _client(db, context)
    rows = list(
        db.scalars(
            select(RestaurantMenuItem)
            .where(
                RestaurantMenuItem.agency_id == context.agency_id,
                RestaurantMenuItem.client_id == context.client_id,
                RestaurantMenuItem.is_active.is_(True),
            )
            .order_by(RestaurantMenuItem.name.asc())
        ).all()
    )
    if query and query.strip():
        needle = _plain(query)
        rows = [
            item for item in rows
            if needle in _plain(item.name)
            or any(needle in _plain(str(alias)) for alias in (item.aliases or []))
            or needle in _plain(item.description or "")
        ]
    return {
        "currency": client.restaurant_currency or "PEN",
        "items": [
            {
                "name": item.name,
                "description": item.description,
                "price": _money(item.price),
            }
            for item in rows[:30]
        ],
    }


def _resolve_menu_item(db: Session, context: LeadContext, requested_name: str) -> RestaurantMenuItem:
    needle = _plain(requested_name)
    rows = list(
        db.scalars(
            select(RestaurantMenuItem).where(
                RestaurantMenuItem.agency_id == context.agency_id,
                RestaurantMenuItem.client_id == context.client_id,
                RestaurantMenuItem.is_active.is_(True),
            )
        ).all()
    )
    exact = [
        item for item in rows
        if needle == _plain(item.name)
        or any(needle == _plain(str(alias)) for alias in (item.aliases or []))
    ]
    if len(exact) == 1:
        return exact[0]

    partial = [
        item for item in rows
        if needle and (
            needle in _plain(item.name)
            or any(needle in _plain(str(alias)) for alias in (item.aliases or []))
        )
    ]
    if len(partial) == 1:
        return partial[0]
    if partial:
        names = ", ".join(item.name for item in partial[:6])
        raise RestaurantOrderError(f"Menu item is ambiguous. Matching options: {names}")
    raise RestaurantOrderError("That item is not in the active restaurant menu")


def add_item(
    db: Session,
    context: LeadContext,
    *,
    item_name: str,
    quantity: int,
    notes: str = "",
) -> dict[str, Any]:
    if quantity < 1 or quantity > 99:
        raise RestaurantOrderError("Quantity must be between 1 and 99")
    client, order = _ensure_editable_order(db, context)
    menu_item = _resolve_menu_item(db, context, item_name)

    existing = db.scalar(
        select(RestaurantOrderItem).where(
            RestaurantOrderItem.order_id == order.id,
            RestaurantOrderItem.menu_item_id == menu_item.id,
            RestaurantOrderItem.notes == (notes or "").strip(),
        )
    )
    if existing:
        existing.quantity += quantity
        existing.line_total = existing.unit_price * existing.quantity
    else:
        db.add(
            RestaurantOrderItem(
                order_id=order.id,
                menu_item_id=menu_item.id,
                item_name=menu_item.name,
                unit_price=menu_item.price,
                quantity=quantity,
                line_total=menu_item.price * quantity,
                notes=(notes or "").strip(),
            )
        )
    order.status = "draft"
    db.flush()
    _recalculate(db, order)
    db.commit()
    db.refresh(order)
    return order_payload(db, order, client)


def remove_item(
    db: Session,
    context: LeadContext,
    *,
    item_name: str,
    quantity: int | None = None,
) -> dict[str, Any]:
    client, order = _ensure_editable_order(db, context)
    menu_item = _resolve_menu_item(db, context, item_name)
    row = db.scalar(
        select(RestaurantOrderItem)
        .where(
            RestaurantOrderItem.order_id == order.id,
            RestaurantOrderItem.menu_item_id == menu_item.id,
        )
        .order_by(RestaurantOrderItem.created_at.asc())
        .limit(1)
    )
    if not row:
        raise RestaurantOrderError("That item is not in the current order")
    if quantity is None or quantity >= row.quantity:
        db.delete(row)
    elif quantity < 1:
        raise RestaurantOrderError("Quantity must be positive")
    else:
        row.quantity -= quantity
        row.line_total = row.unit_price * row.quantity
    db.flush()
    _recalculate(db, order)
    db.commit()
    db.refresh(order)
    return order_payload(db, order, client)


def set_order_details(
    db: Session,
    context: LeadContext,
    *,
    customer_name: str | None = None,
    fulfillment_type: str | None = None,
    table_label: str | None = None,
    delivery_address: str | None = None,
) -> dict[str, Any]:
    client, order = _ensure_editable_order(db, context)
    if customer_name is not None:
        order.customer_name = customer_name.strip() or None
    if fulfillment_type is not None:
        if fulfillment_type not in ("delivery", "table", "pickup"):
            raise RestaurantOrderError("Invalid fulfillment type")
        order.fulfillment_type = fulfillment_type
    if table_label is not None:
        order.table_label = table_label.strip() or None
    if delivery_address is not None:
        order.delivery_address = delivery_address.strip() or None
    db.commit()
    db.refresh(order)
    return order_payload(db, order, client)


def view_order(db: Session, context: LeadContext) -> dict[str, Any]:
    client = _client(db, context)
    order = _current_order(db, context)
    if not order:
        return {
            "exists": False,
            "currency": client.restaurant_currency or "PEN",
            "items": [],
            "total": "0.00",
        }
    _recalculate(db, order)
    db.commit()
    db.refresh(order)
    payload = order_payload(db, order, client)
    payload["exists"] = True
    return payload


def confirm_order(db: Session, context: LeadContext) -> dict[str, Any]:
    client = _client(db, context)
    order = _current_order(db, context)
    if not order or order.status not in ("draft", "awaiting_confirmation"):
        raise RestaurantOrderError("There is no editable order to confirm")
    if not _items(db, order):
        raise RestaurantOrderError("The order is empty")
    if not order.fulfillment_type:
        raise RestaurantOrderError("Delivery type must be confirmed before the order")
    if order.fulfillment_type == "delivery" and not order.delivery_address:
        raise RestaurantOrderError("Delivery address is required")
    if order.fulfillment_type == "table" and not order.table_label:
        raise RestaurantOrderError("Table identifier is required")

    _recalculate(db, order)
    order.status = "awaiting_payment"
    order.customer_confirmed_at = now_utc()
    db.commit()
    db.refresh(order)
    return order_payload(db, order, client)


def report_payment(
    db: Session,
    context: LeadContext,
    *,
    method: str,
    reference: str | None = None,
) -> dict[str, Any]:
    client = _client(db, context)
    order = _current_order(db, context)
    if not order or order.status not in ("awaiting_payment", "payment_reported"):
        raise RestaurantOrderError("The order is not waiting for payment")
    order.payment_method = method.strip()
    order.payment_reference = reference.strip() if reference else None
    order.payment_status = "reported"
    order.status = "payment_reported"
    order.payment_reported_at = now_utc()
    db.commit()
    db.refresh(order)
    payload = order_payload(db, order, client)
    payload["payment_confirmation_required"] = True
    payload["instruction"] = (
        "Payment was only reported by the customer. It is NOT confirmed yet. "
        "A human operator must verify it before the order can be sent to the kitchen."
    )
    return payload

import uuid
from decimal import Decimal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..models import (
    Client,
    RestaurantMenuItem,
    RestaurantOrder,
    RestaurantOrderItem,
    RestaurantStaffUser,
    User,
    WhatsAppChannel,
    now_utc,
)
from ..ratelimit import login_rate_limit
from ..security import (
    create_restaurant_staff_token,
    decode_restaurant_staff_token,
    hash_password,
    verify_password,
)
from ..services.leads import normalize_phone
from ..services.restaurant import is_restaurant_client, order_payload
from ..services.whatsapp import bridge_command


router = APIRouter(prefix="/restaurant", tags=["Restaurant staff"])
STAFF_COOKIE = "restaurant_staff_token"


class StaffCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    phone: str = Field(min_length=6, max_length=40)
    password: str = Field(min_length=6, max_length=128)
    role: str = Field(pattern="^(waiter|kitchen|delivery)$")


class StaffUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    phone: str | None = Field(default=None, min_length=6, max_length=40)
    password: str | None = Field(default=None, min_length=6, max_length=128)
    role: str | None = Field(default=None, pattern="^(waiter|kitchen|delivery)$")
    is_active: bool | None = None


class StaffLoginIn(BaseModel):
    phone: str = Field(min_length=6, max_length=40)
    password: str = Field(min_length=1, max_length=128)


class StaffOrderItemIn(BaseModel):
    menu_item_id: uuid.UUID
    quantity: int = Field(ge=1, le=99)
    notes: str = Field(default="", max_length=500)


class StaffWaiterOrderIn(BaseModel):
    table_label: str = Field(min_length=1, max_length=80)
    customer_name: str | None = Field(default=None, max_length=180)
    items: list[StaffOrderItemIn] = Field(min_length=1, max_length=60)


def _admin_client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    client = db.get(Client, client_id)
    if not client or (not user.is_vendiq_admin and client.agency_id != user.agency_id):
        raise HTTPException(status_code=404, detail="Client not found")
    if not is_restaurant_client(client):
        raise HTTPException(status_code=409, detail="Restaurant ordering is not enabled for this client")
    return client


def _public_restaurant(db: Session, slug: str) -> Client:
    client = db.scalar(
        select(Client).where(
            Client.portal_slug == slug,
            Client.is_active.is_(True),
        )
    )
    if not client or not is_restaurant_client(client):
        raise HTTPException(status_code=404, detail="Restaurant portal not found")
    return client


def _staff_out(staff: RestaurantStaffUser) -> dict:
    return {
        "id": str(staff.id),
        "client_id": str(staff.client_id),
        "name": staff.name,
        "phone": staff.phone,
        "role": staff.role,
        "is_active": staff.is_active,
        "created_at": staff.created_at.isoformat(),
    }


def _staff_session(
    slug: str,
    restaurant_staff_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> tuple[Client, RestaurantStaffUser]:
    if not restaurant_staff_token:
        raise HTTPException(status_code=401, detail="Inicia sesión")
    payload = decode_restaurant_staff_token(restaurant_staff_token)
    if not payload or payload.get("portal_slug") != slug:
        raise HTTPException(status_code=401, detail="La sesión venció")
    try:
        staff_id = uuid.UUID(payload["sub"])
        client_id = uuid.UUID(payload["client_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Sesión inválida") from exc
    client = _public_restaurant(db, slug)
    staff = db.get(RestaurantStaffUser, staff_id)
    if (
        not staff
        or not staff.is_active
        or staff.client_id != client_id
        or staff.client_id != client.id
        or payload.get("credentials_version") != staff.credentials_version
    ):
        raise HTTPException(status_code=401, detail="La cuenta ya no está disponible")
    return client, staff


async def _send_operational_whatsapp(
    db: Session,
    *,
    client: Client,
    destination: str,
    text: str,
) -> None:
    channel = db.scalar(
        select(WhatsAppChannel).where(
            WhatsAppChannel.client_id == client.id,
            WhatsAppChannel.agency_id == client.agency_id,
            WhatsAppChannel.is_enabled.is_(True),
            WhatsAppChannel.status == "connected",
        )
    )
    if not channel:
        raise HTTPException(status_code=409, detail="El WhatsApp del restaurante no está conectado")
    await bridge_command(
        "POST",
        f"/channels/{channel.id}/send",
        {"remote_jid": f"{destination.lstrip('+')}@s.whatsapp.net", "text": text},
    )


def _order_message(db: Session, order: RestaurantOrder, title: str) -> str:
    payload = order_payload(db, order)
    lines = [title, "", f"Pedido: {payload['code']}"]
    if payload.get("waiter_name"):
        lines.append(f"Mesero: {payload['waiter_name']}")
    if payload.get("table"):
        lines.append(f"Mesa: {payload['table']}")
    if payload.get("customer_name"):
        lines.append(f"Cliente: {payload['customer_name']}")
    if payload.get("delivery_address"):
        lines.append(f"Dirección: {payload['delivery_address']}")
    lines.append("")
    for item in payload["items"]:
        note = f" ({item['notes']})" if item.get("notes") else ""
        lines.append(f"{item['quantity']} x {item['name']}{note}")
    lines.extend(["", f"Total: {payload['currency']} {payload['total']}"])
    return "\n".join(lines)


@router.get("/clients/{client_id}/staff")
def list_staff(
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _admin_client(db, user, client_id)
    rows = db.scalars(
        select(RestaurantStaffUser)
        .where(RestaurantStaffUser.client_id == client.id)
        .order_by(RestaurantStaffUser.role.asc(), RestaurantStaffUser.name.asc())
    ).all()
    return [_staff_out(row) for row in rows]


@router.post("/clients/{client_id}/staff", status_code=status.HTTP_201_CREATED)
def create_staff(
    client_id: uuid.UUID,
    payload: StaffCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _admin_client(db, user, client_id)
    try:
        phone = normalize_phone(payload.phone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    duplicate = db.scalar(
        select(RestaurantStaffUser.id).where(
            RestaurantStaffUser.client_id == client.id,
            RestaurantStaffUser.phone_normalized == phone,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="Ese teléfono ya tiene acceso a este restaurante")
    staff = RestaurantStaffUser(
        agency_id=client.agency_id,
        client_id=client.id,
        name=payload.name.strip(),
        phone=payload.phone.strip(),
        phone_normalized=phone,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return _staff_out(staff)


@router.patch("/staff-users/{staff_id}")
def update_staff(
    staff_id: uuid.UUID,
    payload: StaffUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    staff = db.get(RestaurantStaffUser, staff_id)
    if not staff or (not user.is_vendiq_admin and staff.agency_id != user.agency_id):
        raise HTTPException(status_code=404, detail="Acceso de personal no encontrado")
    if payload.name is not None:
        staff.name = payload.name.strip()
    if payload.phone is not None:
        try:
            phone = normalize_phone(payload.phone)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        duplicate = db.scalar(
            select(RestaurantStaffUser.id).where(
                RestaurantStaffUser.client_id == staff.client_id,
                RestaurantStaffUser.phone_normalized == phone,
                RestaurantStaffUser.id != staff.id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="Ese teléfono ya tiene acceso a este restaurante")
        staff.phone = payload.phone.strip()
        staff.phone_normalized = phone
        staff.credentials_version += 1
    if payload.password is not None:
        staff.password_hash = hash_password(payload.password)
        staff.credentials_version += 1
    if payload.role is not None:
        staff.role = payload.role
        staff.credentials_version += 1
    if payload.is_active is not None and payload.is_active != staff.is_active:
        staff.is_active = payload.is_active
        staff.credentials_version += 1
    db.commit()
    db.refresh(staff)
    return _staff_out(staff)


@router.get("/staff-portal/{slug}")
def staff_portal_public(slug: str, db: Session = Depends(get_db)):
    client = _public_restaurant(db, slug)
    return {
        "client_name": client.name,
        "portal_slug": client.portal_slug,
    }


@router.post("/staff-portal/{slug}/login", dependencies=[Depends(login_rate_limit)])
def staff_login(
    slug: str,
    payload: StaffLoginIn,
    response: Response,
    db: Session = Depends(get_db),
):
    client = _public_restaurant(db, slug)
    try:
        phone = normalize_phone(payload.phone)
    except ValueError:
        raise HTTPException(status_code=401, detail="Teléfono o contraseña incorrectos")
    staff = db.scalar(
        select(RestaurantStaffUser).where(
            RestaurantStaffUser.client_id == client.id,
            RestaurantStaffUser.phone_normalized == phone,
            RestaurantStaffUser.is_active.is_(True),
        )
    )
    if not staff or not verify_password(payload.password, staff.password_hash):
        raise HTTPException(status_code=401, detail="Teléfono o contraseña incorrectos")
    settings = get_settings()
    response.set_cookie(
        key=STAFF_COOKIE,
        value=create_restaurant_staff_token(
            str(staff.id),
            str(client.id),
            client.portal_slug,
            staff.credentials_version,
        ),
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.access_token_minutes * 60,
        path="/",
    )
    return _staff_out(staff)


@router.post("/staff-portal/{slug}/logout", status_code=status.HTTP_204_NO_CONTENT)
def staff_logout(response: Response):
    response.delete_cookie(STAFF_COOKIE, path="/")


@router.get("/staff-portal/{slug}/me")
def staff_me(
    slug: str,
    session: tuple[Client, RestaurantStaffUser] = Depends(_staff_session),
):
    client, staff = session
    return {
        **_staff_out(staff),
        "client_name": client.name,
        "portal_slug": client.portal_slug,
    }


@router.get("/staff-portal/{slug}/menu")
def staff_menu(
    slug: str,
    session: tuple[Client, RestaurantStaffUser] = Depends(_staff_session),
    db: Session = Depends(get_db),
):
    client, staff = session
    if staff.role != "waiter":
        raise HTTPException(status_code=403, detail="Este perfil no usa el menú")
    rows = db.scalars(
        select(RestaurantMenuItem)
        .where(
            RestaurantMenuItem.client_id == client.id,
            RestaurantMenuItem.is_active.is_(True),
        )
        .order_by(RestaurantMenuItem.name.asc())
    ).all()
    return [
        {
            "id": str(item.id),
            "name": item.name,
            "description": item.description,
            "price": str(item.price),
            "currency": item.currency,
        }
        for item in rows
    ]


@router.get("/staff-portal/{slug}/orders")
def staff_orders(
    slug: str,
    limit: int = Query(default=100, ge=1, le=200),
    session: tuple[Client, RestaurantStaffUser] = Depends(_staff_session),
    db: Session = Depends(get_db),
):
    client, staff = session
    query = select(RestaurantOrder).where(RestaurantOrder.client_id == client.id)
    if staff.role == "waiter":
        query = query.where(
            RestaurantOrder.created_by_staff_id == staff.id,
            RestaurantOrder.fulfillment_type == "table",
            RestaurantOrder.status.in_(("kitchen", "ready", "served")),
        )
    elif staff.role == "kitchen":
        query = query.where(RestaurantOrder.status.in_(("kitchen", "paid", "ready")))
    elif staff.role == "delivery":
        query = query.where(
            RestaurantOrder.fulfillment_type == "delivery",
            RestaurantOrder.status.in_(("ready", "out_for_delivery", "delivered")),
        )
    rows = db.scalars(query.order_by(RestaurantOrder.updated_at.desc()).limit(limit)).all()
    return [order_payload(db, row, client) for row in rows]


@router.post("/staff-portal/{slug}/waiter-orders", status_code=status.HTTP_201_CREATED)
async def staff_waiter_order(
    slug: str,
    payload: StaffWaiterOrderIn,
    session: tuple[Client, RestaurantStaffUser] = Depends(_staff_session),
    db: Session = Depends(get_db),
):
    client, staff = session
    if staff.role != "waiter":
        raise HTTPException(status_code=403, detail="Solo los meseros pueden registrar pedidos de mesa")
    requested_ids = {item.menu_item_id for item in payload.items}
    menu_rows = list(
        db.scalars(
            select(RestaurantMenuItem).where(
                RestaurantMenuItem.id.in_(requested_ids),
                RestaurantMenuItem.client_id == client.id,
                RestaurantMenuItem.is_active.is_(True),
            )
        ).all()
    )
    by_id = {item.id: item for item in menu_rows}
    if len(by_id) != len(requested_ids):
        raise HTTPException(status_code=422, detail="Uno o más productos no están disponibles")

    order = RestaurantOrder(
        public_code=f"PED-{uuid.uuid4().hex[:8].upper()}",
        agency_id=client.agency_id,
        client_id=client.id,
        agent_id=None,
        conversation_id=None,
        created_by_user_id=None,
        created_by_staff_id=staff.id,
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
            await _send_operational_whatsapp(
                db,
                client=client,
                destination=client.restaurant_kitchen_phone,
                text=_order_message(db, order, "👨‍🍳 PEDIDO DE MESA — PREPARAR"),
            )
            order.kitchen_sent_at = now_utc()
        except HTTPException:
            # The kitchen portal remains the source of truth; WhatsApp is only an optional alert.
            pass
    db.commit()
    db.refresh(order)
    return order_payload(db, order, client)


@router.post("/staff-portal/{slug}/orders/{order_id}/ready")
async def staff_mark_ready(
    slug: str,
    order_id: uuid.UUID,
    session: tuple[Client, RestaurantStaffUser] = Depends(_staff_session),
    db: Session = Depends(get_db),
):
    client, staff = session
    if staff.role != "kitchen":
        raise HTTPException(status_code=403, detail="Solo cocina puede marcar pedidos como listos")
    order = db.get(RestaurantOrder, order_id)
    if not order or order.client_id != client.id:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    if order.status not in ("kitchen", "paid"):
        raise HTTPException(status_code=409, detail="El pedido no está en cocina")
    order.status = "ready"
    order.ready_at = now_utc()
    db.commit()

    destination = None
    title = ""
    if order.fulfillment_type == "delivery" and client.restaurant_delivery_phone:
        destination = client.restaurant_delivery_phone
        title = "🛵 PEDIDO LISTO PARA DELIVERY"
    elif order.fulfillment_type == "table" and order.created_by_staff_id:
        waiter = db.get(RestaurantStaffUser, order.created_by_staff_id)
        if waiter and waiter.is_active:
            destination = waiter.phone_normalized
            title = f"🍽️ PEDIDO LISTO — MESA {order.table_label or ''}".strip()

    if destination:
        try:
            await _send_operational_whatsapp(
                db,
                client=client,
                destination=destination,
                text=_order_message(db, order, title),
            )
        except HTTPException:
            pass
    db.refresh(order)
    return order_payload(db, order, client)


@router.post("/staff-portal/{slug}/orders/{order_id}/served")
def staff_mark_served(
    slug: str,
    order_id: uuid.UUID,
    session: tuple[Client, RestaurantStaffUser] = Depends(_staff_session),
    db: Session = Depends(get_db),
):
    client, staff = session
    if staff.role != "waiter":
        raise HTTPException(status_code=403, detail="Solo el mesero puede marcar la mesa como servida")
    order = db.get(RestaurantOrder, order_id)
    if (
        not order
        or order.client_id != client.id
        or order.created_by_staff_id != staff.id
        or order.fulfillment_type != "table"
    ):
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    if order.status != "ready":
        raise HTTPException(status_code=409, detail="El pedido todavía no está listo")
    order.status = "served"
    order.delivered_at = now_utc()
    db.commit()
    return order_payload(db, order, client)


@router.post("/staff-portal/{slug}/orders/{order_id}/start-delivery")
def staff_start_delivery(
    slug: str,
    order_id: uuid.UUID,
    session: tuple[Client, RestaurantStaffUser] = Depends(_staff_session),
    db: Session = Depends(get_db),
):
    client, staff = session
    if staff.role != "delivery":
        raise HTTPException(status_code=403, detail="Solo delivery puede iniciar el reparto")
    order = db.get(RestaurantOrder, order_id)
    if not order or order.client_id != client.id or order.fulfillment_type != "delivery":
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    if order.status != "ready":
        raise HTTPException(status_code=409, detail="El pedido todavía no está listo")
    order.status = "out_for_delivery"
    db.commit()
    return order_payload(db, order, client)


@router.post("/staff-portal/{slug}/orders/{order_id}/delivered")
def staff_mark_delivered(
    slug: str,
    order_id: uuid.UUID,
    session: tuple[Client, RestaurantStaffUser] = Depends(_staff_session),
    db: Session = Depends(get_db),
):
    client, staff = session
    if staff.role != "delivery":
        raise HTTPException(status_code=403, detail="Solo delivery puede marcar el pedido como entregado")
    order = db.get(RestaurantOrder, order_id)
    if not order or order.client_id != client.id or order.fulfillment_type != "delivery":
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    if order.status != "out_for_delivery":
        raise HTTPException(status_code=409, detail="El pedido no está en reparto")
    order.status = "delivered"
    order.delivered_at = now_utc()
    db.commit()
    return order_payload(db, order, client)

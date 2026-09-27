"""Shared validation and total calculation for restaurant orders."""

import re
import unicodedata
import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Client, User
from ..models_orders import RestaurantOrder, RestaurantOrderItem, RestaurantTableAccount
from ..models_restaurant import (
    MenuProduct,
    MenuProductExtra,
    MenuProductVariant,
    RestaurantModality,
    RestaurantStaff,
)
from ..schemas_orders import OrderItemCreate


ROLE_LABELS = {"admin", "cashier", "waiter", "kitchen", "delivery"}
OPERATIONAL_ROLES = {"cashier", "waiter", "kitchen", "delivery"}
DIGITAL_PAYMENT_METHODS = {"yape", "plin", "transfer", "card", "other"}


def recalculate_order_totals(order: RestaurantOrder) -> None:
    active_items = [item for item in order.items if not item.is_cancelled]
    order.subtotal = sum((item.line_subtotal for item in active_items), Decimal("0"))
    # Delivery is collected separately from the restaurant product amount.
    order.total = order.subtotal
    if not active_items:
        order.order_status = "cancelled"


def restaurant_access(
    db: Session,
    user: User,
    client_id: uuid.UUID,
    allowed: set[str] | None = None,
    *,
    panel_role: str | None = None,
    panel_staff_id: uuid.UUID | None = None,
) -> tuple[Client, str]:
    client = db.scalar(select(Client).where(Client.id == client_id, Client.agency_id == user.agency_id) if not user.is_vendiq_admin else select(Client).where(Client.id == client_id))
    if not client:
        raise HTTPException(status_code=404, detail="Restaurant client not found")
    if client.industry.strip().casefold() != "restaurante":
        raise HTTPException(status_code=404, detail="Orders are only available for restaurant clients")
    assigned_client_id = getattr(user, "restaurant_client_id", None)
    if not user.is_vendiq_admin:
        if assigned_client_id and assigned_client_id != client_id:
            raise HTTPException(status_code=404, detail="Restaurant client not found")
        if not assigned_client_id:
            raise HTTPException(status_code=404, detail="Restaurant client not found")
    staff = db.scalar(
        select(RestaurantStaff).where(
            RestaurantStaff.client_id == client_id,
            RestaurantStaff.user_id == user.id,
            RestaurantStaff.is_active.is_(True),
        )
    )
    role = "admin" if user.is_vendiq_admin or getattr(user, "restaurant_role", None) == "admin" else staff.role if staff else ""
    if panel_role is not None or panel_staff_id is not None:
        if panel_role not in OPERATIONAL_ROLES or not panel_staff_id:
            raise HTTPException(status_code=400, detail="Invalid administrator panel context")
        if not user.is_vendiq_admin and (user.role != "admin" or role != "admin"):
            raise HTTPException(status_code=403, detail="Only a restaurant administrator can view an operational panel")
        panel_staff = db.scalar(
            select(RestaurantStaff).where(
                RestaurantStaff.id == panel_staff_id,
                RestaurantStaff.client_id == client_id,
                RestaurantStaff.role == panel_role,
                RestaurantStaff.is_active.is_(True),
            )
        )
        if not panel_staff:
            raise HTTPException(status_code=404, detail="Active restaurant staff assignment not found")
        role = panel_role
    if role not in ROLE_LABELS or (allowed is not None and role not in allowed):
        raise HTTPException(status_code=403, detail="You do not have access to this restaurant operation")
    return client, role


def _normal(value: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", plain))


def _load_product(db: Session, client_id: uuid.UUID, item: OrderItemCreate) -> tuple[MenuProduct, list[MenuProductVariant], list[MenuProductExtra]]:
    product = db.scalar(
        select(MenuProduct)
        .options(selectinload(MenuProduct.variants), selectinload(MenuProduct.extras))
        .where(MenuProduct.id == item.product_id, MenuProduct.client_id == client_id)
    )
    if not product or not product.is_available:
        raise HTTPException(status_code=422, detail="One or more products are not available")
    variant_map = {row.id: row for row in product.variants if row.is_available}
    extra_map = {row.id: row for row in product.extras if row.is_available}
    if len(set(item.variant_ids)) != len(item.variant_ids) or any(option_id not in variant_map for option_id in item.variant_ids):
        raise HTTPException(status_code=422, detail=f"Invalid variants for product '{product.name}'")
    if len(set(item.extra_ids)) != len(item.extra_ids) or any(option_id not in extra_map for option_id in item.extra_ids):
        raise HTTPException(status_code=422, detail=f"Invalid extras for product '{product.name}'")
    return product, [variant_map[option_id] for option_id in item.variant_ids], [extra_map[option_id] for option_id in item.extra_ids]


def _selected_options(variants: list[MenuProductVariant], extras: list[MenuProductExtra]) -> tuple[list[dict], list[dict], Decimal]:
    variant_data = [{"id": str(row.id), "name": row.name, "price_delta": str(row.price_delta)} for row in variants]
    extra_data = [{"id": str(row.id), "name": row.name, "price": str(row.price)} for row in extras]
    return variant_data, extra_data, sum((row.price_delta for row in variants), Decimal("0")) + sum((row.price for row in extras), Decimal("0"))


def _get_table_account(db: Session, client_id: uuid.UUID, table_number: str) -> RestaurantTableAccount:
    normalized = table_number.strip()
    account = db.scalar(
        select(RestaurantTableAccount).where(
            RestaurantTableAccount.client_id == client_id,
            RestaurantTableAccount.table_number == normalized,
            RestaurantTableAccount.is_open.is_(True),
        )
    )
    if account:
        return account
    account = RestaurantTableAccount(client_id=client_id, table_number=normalized)
    db.add(account)
    db.flush()
    return account


def create_order(
    db: Session,
    *,
    client_id: uuid.UUID,
    source: str,
    modality: str,
    items: list[OrderItemCreate],
    table_number: str | None = None,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    address: str | None = None,
    address_reference: str | None = None,
    delivery_zone_name: str | None = None,
    payment_method: str | None = None,
    notes: str | None = None,
    created_by_user_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> RestaurantOrder:
    if idempotency_key:
        existing = db.scalar(select(RestaurantOrder).where(RestaurantOrder.client_id == client_id, RestaurantOrder.idempotency_key == idempotency_key).options(selectinload(RestaurantOrder.items)))
        if existing:
            return existing
    modalities = db.scalar(select(RestaurantModality).where(RestaurantModality.client_id == client_id))
    if not modalities or not getattr(modalities, f"{modality}_enabled"):
        raise HTTPException(status_code=422, detail="This restaurant modality is not enabled")
    if modality == "dine_in" and not table_number:
        raise HTTPException(status_code=422, detail="A table number is required")
    if modality == "delivery" and (not customer_name or not customer_phone or not address or not address_reference):
        raise HTTPException(status_code=422, detail="Delivery requires name, phone, exact address and reference")
    order_items: list[RestaurantOrderItem] = []
    subtotal = Decimal("0")
    for position, item in enumerate(items):
        product, variants, extras = _load_product(db, client_id, item)
        variant_data, extra_data, options_total = _selected_options(variants, extras)
        unit_price = Decimal(product.price) + options_total
        line_subtotal = unit_price * item.quantity
        subtotal += line_subtotal
        order_items.append(RestaurantOrderItem(
            client_id=client_id,
            product_id=product.id,
            product_name=product.name,
            base_unit_price=product.price,
            unit_price=unit_price,
            quantity=item.quantity,
            line_subtotal=line_subtotal,
            variants=variant_data,
            extras=extra_data,
            observations=item.observations,
            position=position,
        ))
    account = _get_table_account(db, client_id, table_number) if modality == "dine_in" else None
    # Customer orders always follow the configured payment-number flow.  The
    # old payment method value may still be supplied by an older caller, but
    # it is deliberately not validated, selected, or persisted for WhatsApp.
    order_status = "confirmed" if source == "waiter" else "pending_payment"
    order = RestaurantOrder(
        client_id=client_id,
        order_number=f"ORD-{uuid.uuid4().hex[:8].upper()}",
        source=source,
        modality=modality,
        table_account_id=account.id if account else None,
        table_number=table_number.strip() if table_number else None,
        customer_name=customer_name,
        customer_phone=customer_phone,
        address=address,
        address_reference=address_reference,
        # Delivery zones and external delivery fees are legacy data only. They
        # are intentionally not read or applied to new orders.
        delivery_zone_id=None,
        delivery_zone_name=None,
        subtotal=subtotal,
        delivery_fee=Decimal("0"),
        # ``total`` is the amount payable to the restaurant for products;
        # ``delivery_fee`` is an independent cash-on-delivery charge.
        total=subtotal,
        order_status=order_status,
        payment_status="pending",
        payment_method=payment_method if source == "waiter" else None,
        notes=notes,
        created_by_user_id=created_by_user_id,
        conversation_id=conversation_id,
        idempotency_key=idempotency_key,
        items=order_items,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def resolve_whatsapp_items(db: Session, client_id: uuid.UUID, raw_items: list[dict]) -> list[OrderItemCreate]:
    products = db.scalars(select(MenuProduct).options(selectinload(MenuProduct.variants), selectinload(MenuProduct.extras)).where(MenuProduct.client_id == client_id, MenuProduct.is_available.is_(True))).unique().all()
    by_name = {_normal(product.name): product for product in products}
    result = []
    for raw in raw_items:
        product = by_name.get(_normal(str(raw.get("product_name", ""))))
        if not product:
            raise HTTPException(status_code=422, detail="The requested product is not available in the configured menu")
        variant_names = {_normal(str(value)) for value in raw.get("variant_names", [])}
        extra_names = {_normal(str(value)) for value in raw.get("extra_names", [])}
        variants = [row.id for row in product.variants if row.is_available and _normal(row.name) in variant_names]
        extras = [row.id for row in product.extras if row.is_available and _normal(row.name) in extra_names]
        if len(variants) != len(variant_names) or len(extras) != len(extra_names):
            raise HTTPException(status_code=422, detail=f"One or more options for '{product.name}' are not available")
        result.append(OrderItemCreate(product_id=product.id, quantity=int(raw.get("quantity", 0)), variant_ids=variants, extra_ids=extras, observations=raw.get("observations")))
    return result

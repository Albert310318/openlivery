import uuid
import re
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..config import is_local_development
from ..database import get_db
from ..deps import get_current_user
from ..models import Agent, Client, User
from ..models_restaurant import (
    DeliveryZone,
    MenuCategory,
    MenuProduct,
    MenuProductExtra,
    MenuProductVariant,
    RestaurantModality,
    RestaurantPaymentMethod,
    RestaurantPaymentMailbox,
    RestaurantProfile,
    RestaurantStaff,
    RestaurantWelcomeFlyer,
)
from ..schemas_restaurant import (
    DeliveryZoneCreate,
    DeliveryZoneOut,
    DeliveryZoneUpdate,
    MenuCategoryCreate,
    MenuCategoryOut,
    MenuCategoryUpdate,
    MenuProductCreate,
    MenuProductOut,
    MenuProductUpdate,
    RestaurantAgentOut,
    RestaurantModalitiesOut,
    RestaurantModalitiesUpdate,
    RestaurantOnboardingOut,
    RestaurantPaymentMethodOut,
    RestaurantPaymentMethodUpdate,
    RestaurantPaymentMailboxOut,
    RestaurantPaymentMailboxUpdate,
    RestaurantProfileOut,
    RestaurantProfileUpdate,
    RestaurantReadinessOut,
    RestaurantStaffCreate,
    RestaurantStaffOut,
    RestaurantStaffUpdate,
    RestaurantStaffUserOut,
    RestaurantWelcomeFlyerOut,
)
from ..security import hash_password
from ..security import encrypt_secret
from ..services.portal_verification import issue_user_code
from ..services.payment_mailbox import PaymentMailboxError, test_mailbox_connection
from ..services.welcome_flyer import validate_welcome_flyer, welcome_flyer_payload, MAX_WELCOME_FLYER_BYTES


router = APIRouter(prefix="/restaurants", tags=["Restaurants"])
PAYMENT_METHODS = {"cash", "yape", "plin", "transfer", "card", "other"}


def _normalize_delivery_whatsapp(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    raw = value.strip()
    if not re.fullmatch(r"\+?[0-9().\s-]+", raw):
        raise HTTPException(status_code=422, detail="WhatsApp del responsable de Delivery no es válido")
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 9 and digits.startswith("9"):
        digits = "51" + digits
    if not 7 <= len(digits) <= 15:
        raise HTTPException(status_code=422, detail="WhatsApp del responsable de Delivery no es válido")
    return digits


def _client(db: Session, user: User, client_id: uuid.UUID, *, allow_operational: bool = False) -> Client:
    query = select(Client).where(Client.id == client_id)
    if not user.is_vendiq_admin:
        assigned_id = getattr(user, "restaurant_client_id", None)
        if assigned_id:
            if assigned_id != client_id:
                raise HTTPException(status_code=404, detail="Restaurant client not found")
            if getattr(user, "restaurant_role", None) in {"cashier", "waiter", "kitchen", "delivery"} and not allow_operational:
                raise HTTPException(status_code=403, detail="This setup area is restricted to restaurant administrators")
            query = query.where(Client.agency_id == user.agency_id, Client.id == assigned_id)
        else:
            # Keep the existing single-company boundary for agency owners.
            current_id = db.scalar(
                select(Client.id)
                .where(Client.agency_id == user.agency_id)
                .order_by(Client.created_at.asc())
                .limit(1)
            )
            query = query.where(Client.agency_id == user.agency_id, Client.id == current_id)
    client = db.scalar(query)
    if not client:
        raise HTTPException(status_code=404, detail="Restaurant client not found")
    return client


def _profile(db: Session, client_id: uuid.UUID) -> RestaurantProfile:
    profile = db.scalar(select(RestaurantProfile).where(RestaurantProfile.client_id == client_id))
    if profile:
        return profile
    profile = RestaurantProfile(client_id=client_id)
    db.add(profile)
    db.flush()
    return profile


def _modalities(db: Session, client_id: uuid.UUID) -> RestaurantModality:
    modalities = db.scalar(select(RestaurantModality).where(RestaurantModality.client_id == client_id))
    if modalities:
        return modalities
    modalities = RestaurantModality(client_id=client_id)
    db.add(modalities)
    db.flush()
    return modalities


def _payment_mailbox_out(row: RestaurantPaymentMailbox | None) -> dict | None:
    if not row:
        return None
    return {
        "id": row.id,
        "client_id": row.client_id,
        "email": row.email,
        "imap_host": row.imap_host,
        "imap_port": row.imap_port,
        "imap_ssl": row.imap_ssl,
        "has_app_password": bool(row.encrypted_app_password),
        "is_enabled": row.is_enabled,
        "connection_status": row.connection_status,
        "last_checked_at": row.last_checked_at,
        "last_error": row.last_error,
    }


def _category(db: Session, client_id: uuid.UUID, category_id: uuid.UUID) -> MenuCategory:
    category = db.scalar(
        select(MenuCategory).where(MenuCategory.id == category_id, MenuCategory.client_id == client_id)
    )
    if not category:
        raise HTTPException(status_code=404, detail="Menu category not found")
    return category


def _product(db: Session, client_id: uuid.UUID, product_id: uuid.UUID) -> MenuProduct:
    product = db.scalar(
        select(MenuProduct)
        .options(selectinload(MenuProduct.variants), selectinload(MenuProduct.extras))
        .where(MenuProduct.id == product_id, MenuProduct.client_id == client_id)
    )
    if not product:
        raise HTTPException(status_code=404, detail="Menu product not found")
    return product


def _replace_options(product: MenuProduct, variants: list, extras: list) -> None:
    product.variants.clear()
    product.variants.extend(
        MenuProductVariant(
            client_id=product.client_id,
            name=item.name,
            price_delta=item.price,
            is_available=item.is_available,
            position=item.position,
        )
        for item in variants
    )
    product.extras.clear()
    product.extras.extend(
        MenuProductExtra(
            client_id=product.client_id,
            name=item.name,
            price=item.price,
            is_available=item.is_available,
            position=item.position,
        )
        for item in extras
    )


def _readiness(
    profile: RestaurantProfile,
    categories: list[MenuCategory],
    modalities: RestaurantModality,
    delivery_zones: list[DeliveryZone],
    payment_methods: list[RestaurantPaymentMethod],
    staff: list[RestaurantStaff],
    agent: Agent | None,
) -> RestaurantReadinessOut:
    profile_ready = bool(
        profile.address.strip()
        and profile.phone.strip()
        and profile.currency.strip()
        and any(str(value).strip() for value in (profile.opening_hours or {}).values())
    )
    menu_ready = any(category.is_active and product.name.strip() for category in categories for product in category.products)
    modalities_ready = any((modalities.dine_in_enabled, modalities.pickup_enabled, modalities.delivery_enabled))
    payments_ready = any(method.is_active for method in payment_methods)
    staff_ready = any(item.is_active and item.role == "admin" for item in staff)
    agent_ready = bool(
        agent
        and agent.name.strip()
        and agent.personality.strip()
        and agent.widget_greeting.strip()
    )
    ready = all((profile_ready, menu_ready, modalities_ready, payments_ready, staff_ready, agent_ready))
    current_status = "agent_active" if agent and agent.is_active else "ready_to_activate" if ready else "incomplete"
    return RestaurantReadinessOut(
        profile=profile_ready,
        menu=menu_ready,
        modalities=modalities_ready,
        payments=payments_ready,
        staff=staff_ready,
        agent=agent_ready,
        ready=ready,
        status=current_status,
    )


def _load_categories(db: Session, client_id: uuid.UUID) -> list[MenuCategory]:
    return db.scalars(
        select(MenuCategory)
        .options(selectinload(MenuCategory.products).selectinload(MenuProduct.variants), selectinload(MenuCategory.products).selectinload(MenuProduct.extras))
        .where(MenuCategory.client_id == client_id)
        .order_by(MenuCategory.position, MenuCategory.created_at)
    ).unique().all()


@router.get("/{client_id}/onboarding", response_model=RestaurantOnboardingOut)
def get_onboarding(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    profile = _profile(db, client_id)
    modalities = _modalities(db, client_id)
    db.commit()
    categories = _load_categories(db, client_id)
    delivery_zones = db.scalars(
        select(DeliveryZone).where(DeliveryZone.client_id == client_id).order_by(DeliveryZone.created_at)
    ).all()
    payment_methods = db.scalars(
        select(RestaurantPaymentMethod)
        .where(RestaurantPaymentMethod.client_id == client_id)
        .order_by(RestaurantPaymentMethod.created_at)
    ).all()
    payment_mailbox = db.scalar(
        select(RestaurantPaymentMailbox).where(RestaurantPaymentMailbox.client_id == client_id)
    )
    staff = db.scalars(
        select(RestaurantStaff)
        .options(selectinload(RestaurantStaff.user))
        .where(RestaurantStaff.client_id == client_id)
        .order_by(RestaurantStaff.created_at)
    ).all()
    client = _client(db, user, client_id)
    candidates = db.scalars(select(User).where(User.agency_id == client.agency_id).order_by(User.name)).all()
    agent = db.scalar(select(Agent).where(Agent.client_id == client_id).order_by(Agent.created_at.asc()))
    flyer = db.scalar(select(RestaurantWelcomeFlyer).where(RestaurantWelcomeFlyer.client_id == client_id))
    return {
        "profile": profile,
        "welcome_flyer": welcome_flyer_payload(flyer, client_id) if flyer else None,
        "categories": categories,
        "modalities": modalities,
        "delivery_zones": delivery_zones,
        "payment_methods": payment_methods,
        "payment_mailbox": _payment_mailbox_out(payment_mailbox),
        "staff": staff,
        "staff_candidates": candidates,
        "agent": agent,
        "readiness": _readiness(profile, categories, modalities, delivery_zones, payment_methods, staff, agent),
    }


@router.get("/{client_id}/welcome-flyer/image")
def get_welcome_flyer_image(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    flyer = db.scalar(select(RestaurantWelcomeFlyer).where(RestaurantWelcomeFlyer.client_id == client_id))
    if not flyer:
        raise HTTPException(status_code=404, detail="Welcome flyer not configured")
    return Response(content=flyer.image_data, media_type=flyer.mime_type, headers={"Cache-Control": "no-store"})


@router.put("/{client_id}/welcome-flyer", response_model=RestaurantWelcomeFlyerOut)
async def upsert_welcome_flyer(
    client_id: uuid.UUID,
    file: UploadFile | None = File(default=None),
    enabled: bool = Form(default=True),
    message: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _client(db, user, client_id)
    message = message.strip()
    if len(message) > 1000:
        raise HTTPException(status_code=422, detail="El mensaje del flyer no puede superar 1000 caracteres")
    flyer = db.scalar(select(RestaurantWelcomeFlyer).where(RestaurantWelcomeFlyer.client_id == client_id))
    if file is not None:
        data = await file.read(MAX_WELCOME_FLYER_BYTES + 1)
        try:
            mime = validate_welcome_flyer(data, file.content_type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not flyer:
            flyer = RestaurantWelcomeFlyer(client_id=client_id, image_data=data, mime_type=mime, filename=file.filename or "welcome-flyer")
            db.add(flyer)
        else:
            flyer.image_data = data
            flyer.mime_type = mime
            flyer.filename = file.filename or flyer.filename
    elif not flyer:
        raise HTTPException(status_code=422, detail="Selecciona una imagen para configurar el flyer")
    flyer.enabled = enabled
    flyer.message = message
    db.commit()
    db.refresh(flyer)
    return welcome_flyer_payload(flyer, client_id)


@router.delete("/{client_id}/welcome-flyer", status_code=status.HTTP_204_NO_CONTENT)
def delete_welcome_flyer(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    flyer = db.scalar(select(RestaurantWelcomeFlyer).where(RestaurantWelcomeFlyer.client_id == client_id))
    if flyer:
        db.delete(flyer)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{client_id}/profile", response_model=RestaurantProfileOut)
def update_profile(client_id: uuid.UUID, payload: RestaurantProfileUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    profile = _profile(db, client_id)
    for key, value in payload.model_dump().items():
        setattr(profile, key, value)
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/{client_id}/menu", response_model=list[MenuCategoryOut])
def list_menu(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id, allow_operational=True)
    return _load_categories(db, client_id)


@router.post("/{client_id}/menu/categories", response_model=MenuCategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(client_id: uuid.UUID, payload: MenuCategoryCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    category = MenuCategory(client_id=client_id, **payload.model_dump())
    db.add(category)
    db.commit()
    return db.scalar(
        select(MenuCategory)
        .options(selectinload(MenuCategory.products).selectinload(MenuProduct.variants), selectinload(MenuCategory.products).selectinload(MenuProduct.extras))
        .where(MenuCategory.id == category.id)
    )


@router.patch("/{client_id}/menu/categories/{category_id}", response_model=MenuCategoryOut)
def update_category(client_id: uuid.UUID, category_id: uuid.UUID, payload: MenuCategoryUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    category = _category(db, client_id, category_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(category, key, value)
    db.commit()
    return db.scalar(
        select(MenuCategory)
        .options(selectinload(MenuCategory.products).selectinload(MenuProduct.variants), selectinload(MenuCategory.products).selectinload(MenuProduct.extras))
        .where(MenuCategory.id == category.id)
    )


@router.delete("/{client_id}/menu/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(client_id: uuid.UUID, category_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    db.delete(_category(db, client_id, category_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{client_id}/menu/products", response_model=MenuProductOut, status_code=status.HTTP_201_CREATED)
def create_product(client_id: uuid.UUID, payload: MenuProductCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    _category(db, client_id, payload.category_id)
    values = payload.model_dump(exclude={"variants", "extras"})
    product = MenuProduct(client_id=client_id, **values)
    db.add(product)
    db.flush()
    _replace_options(product, payload.variants, payload.extras)
    db.commit()
    return _product(db, client_id, product.id)


@router.patch("/{client_id}/menu/products/{product_id}", response_model=MenuProductOut)
def update_product(client_id: uuid.UUID, product_id: uuid.UUID, payload: MenuProductUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    product = _product(db, client_id, product_id)
    values = payload.model_dump(exclude_unset=True)
    variants = values.pop("variants", None)
    extras = values.pop("extras", None)
    if "category_id" in values:
        _category(db, client_id, values["category_id"])
    for key, value in values.items():
        setattr(product, key, value)
    if variants is not None or extras is not None:
        _replace_options(product, variants or [], extras or [])
    db.commit()
    return _product(db, client_id, product.id)


@router.delete("/{client_id}/menu/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(client_id: uuid.UUID, product_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    db.delete(_product(db, client_id, product_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{client_id}/modalities", response_model=RestaurantModalitiesOut)
def get_modalities(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    return _modalities(db, client_id)


@router.patch("/{client_id}/modalities", response_model=RestaurantModalitiesOut)
def update_modalities(client_id: uuid.UUID, payload: RestaurantModalitiesUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    modalities = _modalities(db, client_id)
    values = payload.model_dump()
    values["delivery_whatsapp"] = _normalize_delivery_whatsapp(values.get("delivery_whatsapp"))
    for key, value in values.items():
        setattr(modalities, key, value)
    db.commit()
    db.refresh(modalities)
    return modalities


@router.get("/{client_id}/delivery-zones", response_model=list[DeliveryZoneOut])
def list_delivery_zones(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    return db.scalars(select(DeliveryZone).where(DeliveryZone.client_id == client_id).order_by(DeliveryZone.created_at)).all()


@router.post("/{client_id}/delivery-zones", response_model=DeliveryZoneOut, status_code=status.HTTP_201_CREATED)
def create_delivery_zone(client_id: uuid.UUID, payload: DeliveryZoneCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    zone = DeliveryZone(client_id=client_id, **payload.model_dump())
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone


@router.patch("/{client_id}/delivery-zones/{zone_id}", response_model=DeliveryZoneOut)
def update_delivery_zone(client_id: uuid.UUID, zone_id: uuid.UUID, payload: DeliveryZoneUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    zone = db.scalar(select(DeliveryZone).where(DeliveryZone.id == zone_id, DeliveryZone.client_id == client_id))
    if not zone:
        raise HTTPException(status_code=404, detail="Delivery zone not found")
    for key, value in payload.model_dump().items():
        setattr(zone, key, value)
    db.commit()
    db.refresh(zone)
    return zone


@router.delete("/{client_id}/delivery-zones/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_delivery_zone(client_id: uuid.UUID, zone_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    zone = db.scalar(select(DeliveryZone).where(DeliveryZone.id == zone_id, DeliveryZone.client_id == client_id))
    if not zone:
        raise HTTPException(status_code=404, detail="Delivery zone not found")
    db.delete(zone)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{client_id}/payment-methods", response_model=list[RestaurantPaymentMethodOut])
def list_payment_methods(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    return db.scalars(select(RestaurantPaymentMethod).where(RestaurantPaymentMethod.client_id == client_id).order_by(RestaurantPaymentMethod.created_at)).all()


@router.put("/{client_id}/payment-methods/{method}", response_model=RestaurantPaymentMethodOut)
def upsert_payment_method(client_id: uuid.UUID, method: str, payload: RestaurantPaymentMethodUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    if method not in PAYMENT_METHODS:
        raise HTTPException(status_code=422, detail="Unsupported payment method")
    row = db.scalar(select(RestaurantPaymentMethod).where(RestaurantPaymentMethod.client_id == client_id, RestaurantPaymentMethod.method == method))
    if not row:
        row = RestaurantPaymentMethod(client_id=client_id, method=method)
        db.add(row)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{client_id}/payment-methods/{method}", status_code=status.HTTP_204_NO_CONTENT)
def delete_payment_method(client_id: uuid.UUID, method: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    row = db.scalar(select(RestaurantPaymentMethod).where(RestaurantPaymentMethod.client_id == client_id, RestaurantPaymentMethod.method == method))
    if row:
        db.delete(row)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{client_id}/payment-mailbox", response_model=RestaurantPaymentMailboxOut | None)
def get_payment_mailbox(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    return _payment_mailbox_out(db.scalar(select(RestaurantPaymentMailbox).where(RestaurantPaymentMailbox.client_id == client_id)))


@router.put("/{client_id}/payment-mailbox", response_model=RestaurantPaymentMailboxOut)
def upsert_payment_mailbox(
    client_id: uuid.UUID,
    payload: RestaurantPaymentMailboxUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _client(db, user, client_id)
    row = db.scalar(select(RestaurantPaymentMailbox).where(RestaurantPaymentMailbox.client_id == client_id))
    if not row:
        if not payload.app_password:
            raise HTTPException(status_code=422, detail="An app password is required to configure the mailbox")
        row = RestaurantPaymentMailbox(client_id=client_id)
        db.add(row)
    previous = (row.email, row.imap_host, row.imap_port, row.imap_ssl, row.is_enabled)
    row.email = str(payload.email).strip().lower()
    row.imap_host = payload.imap_host.strip()
    row.imap_port = payload.imap_port
    row.imap_ssl = payload.imap_ssl
    row.is_enabled = payload.is_enabled
    if payload.app_password:
        row.encrypted_app_password = encrypt_secret(payload.app_password.strip())
    if previous != (row.email, row.imap_host, row.imap_port, row.imap_ssl, row.is_enabled) or payload.app_password:
        row.connection_status = "not_tested"
        row.last_error = None
    db.commit()
    db.refresh(row)
    return _payment_mailbox_out(row)


@router.post("/{client_id}/payment-mailbox/test", response_model=RestaurantPaymentMailboxOut)
def test_payment_mailbox(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    row = db.scalar(select(RestaurantPaymentMailbox).where(RestaurantPaymentMailbox.client_id == client_id))
    if not row:
        raise HTTPException(status_code=404, detail="Payment mailbox is not configured")
    try:
        test_mailbox_connection(db, row)
    except PaymentMailboxError:
        db.refresh(row)
    return _payment_mailbox_out(row)


@router.get("/{client_id}/staff/candidates", response_model=list[RestaurantStaffUserOut])
def list_staff_candidates(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    return db.scalars(select(User).where(User.agency_id == client.agency_id).order_by(User.name)).all()


@router.get("/{client_id}/staff", response_model=list[RestaurantStaffOut])
def list_staff(client_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    return db.scalars(
        select(RestaurantStaff).options(selectinload(RestaurantStaff.user)).where(RestaurantStaff.client_id == client_id).order_by(RestaurantStaff.created_at)
    ).all()


@router.post("/{client_id}/staff", response_model=RestaurantStaffOut, status_code=status.HTTP_201_CREATED)
def add_staff(client_id: uuid.UUID, payload: RestaurantStaffCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    development_verification_bypassed = False
    if payload.user_id:
        staff_user = db.scalar(select(User).where(User.id == payload.user_id, User.agency_id == client.agency_id))
        if not staff_user:
            raise HTTPException(status_code=404, detail="Staff user not found")
    else:
        email = str(payload.email).strip().lower()
        staff_user = db.scalar(select(User).where(func.lower(User.email) == email))
        if staff_user and staff_user.agency_id != client.agency_id:
            raise HTTPException(status_code=409, detail="This email already belongs to another agency")
        if not staff_user:
            normalized_phone = payload.phone.strip() if payload.phone and payload.phone.strip() else None
            if normalized_phone and db.scalar(select(User.id).where(User.phone == normalized_phone)):
                raise HTTPException(status_code=409, detail="A user with that phone already exists")
            staff_user = User(
                agency_id=client.agency_id,
                name=payload.name.strip(),
                email=email,
                phone=normalized_phone,
                password_hash=hash_password(payload.password),
                role="staff",
                is_vendiq_admin=False,
                email_verification_pending=True,
            )
            db.add(staff_user)
            db.flush()
            staff_user.password_recovery_credentials_version += 1
            verification_sent = issue_user_code(db, staff_user, allow_delivery_failure=is_local_development())
            if not verification_sent:
                staff_user.email_verification_pending = False
                development_verification_bypassed = True
                db.commit()
        elif staff_user.email_verification_pending and not staff_user.password_recovery_code_hash:
            staff_user.password_recovery_credentials_version += 1
            verification_sent = issue_user_code(db, staff_user, allow_delivery_failure=is_local_development())
            if not verification_sent:
                staff_user.email_verification_pending = False
                development_verification_bypassed = True
                db.commit()
    existing = db.scalar(select(RestaurantStaff).where(RestaurantStaff.client_id == client_id, RestaurantStaff.user_id == staff_user.id))
    if existing:
        raise HTTPException(status_code=409, detail="This user is already assigned to the restaurant")
    row = RestaurantStaff(client_id=client_id, user_id=staff_user.id, role=payload.role, is_active=payload.is_active)
    db.add(row)
    db.commit()
    result = db.scalar(select(RestaurantStaff).options(selectinload(RestaurantStaff.user)).where(RestaurantStaff.id == row.id))
    result.development_verification_bypassed = development_verification_bypassed
    return result


@router.patch("/{client_id}/staff/{staff_id}", response_model=RestaurantStaffOut)
def update_staff(client_id: uuid.UUID, staff_id: uuid.UUID, payload: RestaurantStaffUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    client = _client(db, user, client_id)
    row = db.scalar(select(RestaurantStaff).where(RestaurantStaff.id == staff_id, RestaurantStaff.client_id == client_id))
    if not row:
        raise HTTPException(status_code=404, detail="Restaurant staff assignment not found")
    values = payload.model_dump(exclude_unset=True)
    for key in ("role", "is_active"):
        if key in values:
            setattr(row, key, values.pop(key))

    user_values = {key: value for key, value in values.items() if key != "password"}
    if "email" in user_values:
        if not user_values["email"]:
            raise HTTPException(status_code=422, detail="Email is required")
        normalized_email = str(user_values["email"]).strip().lower()
        duplicate = db.scalar(
            select(User).where(func.lower(User.email) == normalized_email, User.id != row.user_id)
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="A user with that email already exists")
        user_values["email"] = normalized_email
    if "phone" in user_values:
        phone_value = user_values["phone"]
        if phone_value:
            user_values["phone"] = phone_value.strip() or None
        else:
            user_values["phone"] = None
        if user_values["phone"]:
            duplicate_phone = db.scalar(select(User).where(User.phone == user_values["phone"], User.id != row.user_id))
            if duplicate_phone:
                raise HTTPException(status_code=409, detail="A user with that phone already exists")
    for key, value in user_values.items():
        setattr(row.user, key, value.strip() if isinstance(value, str) else value)
    if "password" in values and values["password"]:
        row.user.password_hash = hash_password(values["password"])
        row.user.password_recovery_credentials_version += 1
    db.commit()
    return db.scalar(select(RestaurantStaff).options(selectinload(RestaurantStaff.user)).where(RestaurantStaff.id == row.id))


@router.delete("/{client_id}/staff/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_staff(client_id: uuid.UUID, staff_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _client(db, user, client_id)
    row = db.scalar(select(RestaurantStaff).where(RestaurantStaff.id == staff_id, RestaurantStaff.client_id == client_id))
    if not row:
        raise HTTPException(status_code=404, detail="Restaurant staff assignment not found")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

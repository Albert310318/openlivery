import uuid

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import is_local_development
from .database import get_db
from .models import Client, User
from .models_restaurant import RestaurantStaff
from .security import decode_access_token


OPERATIONAL_ROLES = {"cashier", "waiter", "kitchen", "delivery"}
RESTAURANT_ROLES = OPERATIONAL_ROLES | {"admin"}
PLATFORM_PREFIXES = (
    "/dashboard", "/leads", "/conversations", "/whatsapp", "/agency", "/providers",
    "/catalog", "/subscriptions", "/promotions",
)


def get_current_user(
    request: Request,
    access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="You are not signed in")
    user_id = decode_access_token(access_token)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="The session expired")
    try:
        parsed_id = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session") from exc
    user = db.get(User, parsed_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.email_verification_pending and not user.is_vendiq_admin and not is_local_development():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Verifica tu correo antes de continuar.")
    restaurant_client = None
    restaurant_role = None
    if not user.is_vendiq_admin:
        assignment = db.scalar(
            select(RestaurantStaff)
            .join(Client, Client.id == RestaurantStaff.client_id)
            .where(
                RestaurantStaff.user_id == user.id,
                RestaurantStaff.is_active.is_(True),
                func.lower(func.trim(Client.industry)) == "restaurante",
            )
            .order_by(RestaurantStaff.created_at.asc())
        )
        restaurant_client = db.get(Client, assignment.client_id) if assignment else None
        restaurant_role = assignment.role if assignment else None
        if not restaurant_role and user.role == "admin":
            restaurant_client = db.scalar(
                select(Client)
                .where(Client.agency_id == user.agency_id, func.lower(func.trim(Client.industry)) == "restaurante")
                .order_by(Client.created_at.asc())
            )
            if restaurant_client:
                restaurant_role = "admin"
    # These are request-local authorization attributes; no new persisted user
    # role is introduced. They let the web shell choose the correct panel and
    # let API dependencies reject platform access for operational staff.
    user.restaurant_role = restaurant_role
    user.restaurant_client_id = restaurant_client.id if restaurant_client else None
    user.restaurant_client_name = restaurant_client.name if restaurant_client else None

    # A staff account without an active restaurant assignment is incomplete
    # data, not an agency administrator. Never let it fall through to the
    # platform shell while the assignment is repaired.
    if user.role == "staff" and not user.is_vendiq_admin and not restaurant_role:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This staff account is not assigned to an active restaurant")

    path = request.url.path.removeprefix("/api") or "/"
    if restaurant_role in RESTAURANT_ROLES and not user.is_vendiq_admin:
        is_current_client = path == "/clients/current"
        platform_route = path.startswith(PLATFORM_PREFIXES) or path == "/clients" or path.startswith("/clients/")
        if restaurant_role in OPERATIONAL_ROLES:
            platform_route = platform_route or path.startswith("/agents")
        elif restaurant_role == "admin" and any(
            path == prefix or path.startswith(f"{prefix}/")
            for prefix in ("/agents", "/conversations", "/leads", "/whatsapp", "/whatsapp-cloud")
        ):
            # A restaurant administrator may use the tenant-scoped commercial
            # modules, but never the agency/global administration endpoints.
            platform_route = False
        elif restaurant_client and path.startswith(f"/clients/{restaurant_client.id}"):
            platform_route = False
        if platform_route and not is_current_client:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is limited to its restaurant operation")

    pending_registration = db.scalar(select(Client).where(
        Client.agency_id == user.agency_id,
        func.lower(Client.portal_email) == user.email,
        Client.portal_enabled.is_(False),
        Client.portal_email_verified_at.is_(None),
    ))
    if pending_registration and not (
        is_local_development()
        and pending_registration.industry.strip().casefold() != "restaurante"
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Verifica tu correo antes de continuar.")
    return user

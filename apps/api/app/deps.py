import uuid

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import get_db
from .models import Client, User
from .security import decode_access_token


def get_current_user(
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
    pending_registration = db.scalar(select(Client.id).where(
        Client.agency_id == user.agency_id,
        func.lower(Client.portal_email) == user.email,
        Client.portal_enabled.is_(False),
        Client.portal_email_verified_at.is_(None),
    ))
    if pending_registration:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Verifica tu correo antes de continuar.")
    return user

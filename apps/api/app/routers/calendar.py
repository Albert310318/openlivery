import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Client, User
from ..services.calendar import calendar_connected, clear_calendar_connection, store_refresh_token


router = APIRouter(prefix="/calendar", tags=["Calendar"])


class CalendarSettingsIn(BaseModel):
    calendar_id: str = Field(default="primary", max_length=255)
    timezone: str = Field(default="America/Lima", max_length=64)
    workday_start: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    workday_end: str = Field(default="18:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    working_days: str = Field(default="0,1,2,3,4,5", max_length=20)
    slot_minutes: int = Field(default=30, ge=5, le=240)
    buffer_minutes: int = Field(default=0, ge=0, le=120)
    min_notice_minutes: int = Field(default=60, ge=0, le=10080)
    booking_horizon_days: int = Field(default=30, ge=1, le=365)


class CalendarStatusOut(BaseModel):
    connected: bool
    calendar_id: str
    timezone: str
    workday_start: str
    workday_end: str
    working_days: str
    slot_minutes: int
    buffer_minutes: int
    min_notice_minutes: int
    booking_horizon_days: int
    oauth_configured: bool


def _current_client(db: Session, user: User) -> Client | None:
    return db.scalar(
        select(Client)
        .where(Client.agency_id == user.agency_id)
        .order_by(Client.created_at.asc())
        .limit(1)
    )


def _client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    if not user.is_vendiq_admin:
        current = _current_client(db, user)
        if not current or current.id != client_id or client.agency_id != user.agency_id:
            raise HTTPException(status_code=404, detail="Client not found")
    return client


def _status(client: Client) -> CalendarStatusOut:
    settings = get_settings()
    return CalendarStatusOut(
        connected=calendar_connected(client),
        calendar_id=client.google_calendar_id,
        timezone=client.google_calendar_timezone,
        workday_start=client.calendar_workday_start,
        workday_end=client.calendar_workday_end,
        working_days=client.calendar_working_days,
        slot_minutes=client.calendar_slot_minutes,
        buffer_minutes=client.calendar_buffer_minutes,
        min_notice_minutes=client.calendar_min_notice_minutes,
        booking_horizon_days=client.calendar_booking_horizon_days,
        oauth_configured=bool(settings.google_oauth_client_id and settings.google_oauth_client_secret),
    )


@router.get("/clients/{client_id}", response_model=CalendarStatusOut)
def calendar_status(
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _status(_client(db, user, client_id))


@router.patch("/clients/{client_id}", response_model=CalendarStatusOut)
def update_calendar_settings(
    client_id: uuid.UUID,
    payload: CalendarSettingsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    try:
        days = {int(value) for value in payload.working_days.split(",") if value.strip()}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="working_days must be comma-separated values from 0 to 6") from exc
    if not days or any(day < 0 or day > 6 for day in days):
        raise HTTPException(status_code=422, detail="working_days must contain values from 0 to 6")
    if payload.workday_start >= payload.workday_end:
        raise HTTPException(status_code=422, detail="workday_start must be earlier than workday_end")

    client.google_calendar_id = payload.calendar_id.strip() or "primary"
    client.google_calendar_timezone = payload.timezone.strip() or "America/Lima"
    client.calendar_workday_start = payload.workday_start
    client.calendar_workday_end = payload.workday_end
    client.calendar_working_days = ",".join(str(day) for day in sorted(days))
    client.calendar_slot_minutes = payload.slot_minutes
    client.calendar_buffer_minutes = payload.buffer_minutes
    client.calendar_min_notice_minutes = payload.min_notice_minutes
    client.calendar_booking_horizon_days = payload.booking_horizon_days
    db.commit()
    return _status(client)


@router.post("/clients/{client_id}/connect")
def connect_google_calendar(
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    settings = get_settings()
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise HTTPException(status_code=503, detail="Google Calendar OAuth is not configured on the server")

    state = jwt.encode(
        {
            "client_id": str(client.id),
            "user_id": str(user.id),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
            "type": "google_calendar_oauth",
        },
        settings.secret_key,
        algorithm="HS256",
    )
    redirect_uri = f"{settings.frontend_url.rstrip('/')}/api/calendar/google/callback"
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": settings.google_oauth_scope,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return {"authorization_url": f"{settings.google_oauth_authorize_url}?{urlencode(params)}"}


@router.get("/google/callback")
async def google_calendar_callback(code: str, state: str, db: Session = Depends(get_db)):
    settings = get_settings()
    try:
        payload = jwt.decode(state, settings.secret_key, algorithms=["HS256"])
        if payload.get("type") != "google_calendar_oauth":
            raise ValueError("invalid state")
        client_id = uuid.UUID(payload["client_id"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Google Calendar authorization state") from exc

    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    redirect_uri = f"{settings.frontend_url.rstrip('/')}/api/calendar/google/callback"
    async with httpx.AsyncClient(timeout=20) as http:
        response = await http.post(
            settings.google_oauth_token_url,
            data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=400, detail="Google Calendar authorization failed")
    token_data = response.json()
    refresh_token = token_data.get("refresh_token")
    if not refresh_token and not client.google_calendar_refresh_token_encrypted:
        raise HTTPException(status_code=400, detail="Google did not return a refresh token; reconnect and grant calendar access")
    if refresh_token:
        store_refresh_token(client, refresh_token)
        db.commit()
    return RedirectResponse(
        url=f"{settings.frontend_url.rstrip('/')}/clients/{client.id}?calendar=connected",
        status_code=302,
    )


@router.delete("/clients/{client_id}", response_model=CalendarStatusOut)
def disconnect_google_calendar(
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _client(db, user, client_id)
    clear_calendar_connection(client)
    db.commit()
    return _status(client)

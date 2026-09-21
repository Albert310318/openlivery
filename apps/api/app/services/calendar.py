import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import CalendarAppointment, Client, Conversation, Lead, LeadConversation, now_utc
from ..security import decrypt_secret, encrypt_secret
from .leads import LeadContext


class CalendarError(ValueError):
    pass


def calendar_connected(client: Client) -> bool:
    return bool(client.google_calendar_refresh_token_encrypted)


async def _access_token(client: Client) -> str:
    if not client.google_calendar_refresh_token_encrypted:
        raise CalendarError("Google Calendar is not connected for this client")
    settings = get_settings()
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise CalendarError("Google Calendar OAuth is not configured on the server")
    refresh_token = decrypt_secret(client.google_calendar_refresh_token_encrypted)
    async with httpx.AsyncClient(timeout=20) as http:
        response = await http.post(
            settings.google_oauth_token_url,
            data={
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if response.status_code >= 400:
        raise CalendarError("Google Calendar authorization needs to be reconnected")
    token = response.json().get("access_token")
    if not token:
        raise CalendarError("Google Calendar did not return an access token")
    return token


def store_refresh_token(client: Client, refresh_token: str) -> None:
    client.google_calendar_refresh_token_encrypted = encrypt_secret(refresh_token)


def clear_calendar_connection(client: Client) -> None:
    client.google_calendar_refresh_token_encrypted = None


def _client_for_context(db: Session, context: LeadContext) -> Client:
    client = db.scalar(
        select(Client).where(
            Client.id == context.client_id,
            Client.agency_id == context.agency_id,
        )
    )
    if not client:
        raise CalendarError("Client calendar configuration was not found")
    return client


def _working_days(client: Client) -> set[int]:
    try:
        return {int(value) for value in client.calendar_working_days.split(",") if value.strip() != ""}
    except ValueError as exc:
        raise CalendarError("Calendar working days are invalid") from exc


def _local_datetime(date_value: str, time_value: str, tz: ZoneInfo) -> datetime:
    try:
        return datetime.fromisoformat(f"{date_value}T{time_value}").replace(tzinfo=tz)
    except ValueError as exc:
        raise CalendarError("Use date YYYY-MM-DD and time HH:MM") from exc


async def availability(
    db: Session,
    context: LeadContext,
    *,
    date: str,
    part_of_day: str | None = None,
    max_slots: int | None = 12,
) -> dict:
    client = _client_for_context(db, context)
    if not calendar_connected(client):
        raise CalendarError("Google Calendar is not connected for this client")

    tz = ZoneInfo(client.google_calendar_timezone)
    day_start = _local_datetime(date, client.calendar_workday_start, tz)
    day_end = _local_datetime(date, client.calendar_workday_end, tz)
    if day_start.weekday() not in _working_days(client):
        return {"date": date, "timezone": client.google_calendar_timezone, "slots": []}

    now_local = now_utc().astimezone(tz)
    latest = now_local + timedelta(days=client.calendar_booking_horizon_days)
    if day_end < now_local or day_start > latest:
        return {"date": date, "timezone": client.google_calendar_timezone, "slots": []}

    if part_of_day == "morning":
        day_end = min(day_end, day_start.replace(hour=12, minute=0))
    elif part_of_day == "afternoon":
        day_start = max(day_start, day_start.replace(hour=12, minute=0))
        day_end = min(day_end, day_start.replace(hour=18, minute=0))
    elif part_of_day == "evening":
        day_start = max(day_start, day_start.replace(hour=18, minute=0))

    token = await _access_token(client)
    settings = get_settings()
    calendar_id = client.google_calendar_id or "primary"
    async with httpx.AsyncClient(timeout=20) as http:
        response = await http.post(
            f"{settings.google_calendar_api_base_url}/freeBusy",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "timeMin": day_start.astimezone(timezone.utc).isoformat(),
                "timeMax": day_end.astimezone(timezone.utc).isoformat(),
                "timeZone": client.google_calendar_timezone,
                "items": [{"id": calendar_id}],
            },
        )
    if response.status_code >= 400:
        raise CalendarError("Could not read Google Calendar availability")
    busy = response.json().get("calendars", {}).get(calendar_id, {}).get("busy", [])

    slot_minutes = max(5, client.calendar_slot_minutes)
    duration = timedelta(minutes=slot_minutes)
    buffer = timedelta(minutes=max(0, client.calendar_buffer_minutes))
    min_start = now_local + timedelta(minutes=max(0, client.calendar_min_notice_minutes))
    slots: list[str] = []
    cursor = day_start
    while cursor + duration <= day_end:
        slot_end = cursor + duration
        available = cursor >= min_start
        if available:
            for item in busy:
                busy_start = datetime.fromisoformat(item["start"].replace("Z", "+00:00")).astimezone(tz) - buffer
                busy_end = datetime.fromisoformat(item["end"].replace("Z", "+00:00")).astimezone(tz) + buffer
                if cursor < busy_end and slot_end > busy_start:
                    available = False
                    break
        if available:
            slots.append(cursor.isoformat())
            if max_slots is not None and len(slots) >= max_slots:
                break
        cursor += duration
    return {"date": date, "timezone": client.google_calendar_timezone, "duration_minutes": slot_minutes, "slots": slots}


async def _assert_slot_available(db: Session, context: LeadContext, start_at: datetime) -> Client:
    client = _client_for_context(db, context)
    tz = ZoneInfo(client.google_calendar_timezone)
    local = start_at.astimezone(tz)
    result = await availability(
        db,
        context,
        date=local.date().isoformat(),
        max_slots=None,
    )
    if local.isoformat() not in result["slots"]:
        raise CalendarError("That appointment time is no longer available")
    return client


def _lead_for_context(db: Session, context: LeadContext) -> Lead | None:
    return db.scalar(
        select(Lead)
        .join(LeadConversation)
        .where(LeadConversation.conversation_id == context.conversation_id)
    )


async def create_appointment(
    db: Session,
    context: LeadContext,
    *,
    start_iso: str,
    title: str | None = None,
) -> dict:
    try:
        start_at = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalendarError("start_iso must be an ISO 8601 date/time with timezone") from exc
    if start_at.tzinfo is None:
        raise CalendarError("start_iso must include a timezone")

    existing = db.scalar(
        select(CalendarAppointment).where(
            CalendarAppointment.conversation_id == context.conversation_id,
        )
    )
    if existing and existing.status == "confirmed":
        raise CalendarError("This conversation already has a confirmed appointment; use reschedule instead")

    client = await _assert_slot_available(db, context, start_at)
    lead = _lead_for_context(db, context)
    end_at = start_at + timedelta(minutes=client.calendar_slot_minutes)
    token = await _access_token(client)
    settings = get_settings()
    calendar_id = client.google_calendar_id or "primary"
    summary = title or f"Cita AYV - {lead.name if lead and lead.name else 'Lead'}"
    description_lines = [
        f"Cliente: {client.name}",
        f"Lead: {lead.name if lead and lead.name else 'Por confirmar'}",
        f"WhatsApp: {lead.phone if lead and lead.phone else 'Por confirmar'}",
        f"Interés: {lead.interest if lead and lead.interest else 'Por confirmar'}",
        f"Presupuesto: {lead.budget if lead and lead.budget else 'Por confirmar'}",
    ]
    async with httpx.AsyncClient(timeout=20) as http:
        response = await http.post(
            f"{settings.google_calendar_api_base_url}/calendars/{quote(calendar_id, safe='')}/events",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "summary": summary,
                "description": "\n".join(description_lines),
                "start": {"dateTime": start_at.isoformat(), "timeZone": client.google_calendar_timezone},
                "end": {"dateTime": end_at.isoformat(), "timeZone": client.google_calendar_timezone},
            },
        )
    if response.status_code >= 400:
        raise CalendarError("Could not create the Google Calendar appointment")
    event = response.json()
    row = existing or CalendarAppointment(
        agency_id=context.agency_id,
        client_id=context.client_id,
        agent_id=context.agent_id,
        conversation_id=context.conversation_id,
    )
    row.lead_id = lead.id if lead else None
    row.google_event_id = event["id"]
    row.calendar_id = calendar_id
    row.start_at = start_at
    row.end_at = end_at
    row.status = "confirmed"
    if existing is None:
        db.add(row)
    db.commit()
    return {
        "ok": True,
        "appointment_id": str(row.id),
        "start": start_at.isoformat(),
        "end": end_at.isoformat(),
        "timezone": client.google_calendar_timezone,
        "html_link": event.get("htmlLink"),
    }


async def reschedule_appointment(db: Session, context: LeadContext, *, start_iso: str) -> dict:
    appointment = db.scalar(
        select(CalendarAppointment).where(
            CalendarAppointment.conversation_id == context.conversation_id,
            CalendarAppointment.status == "confirmed",
        )
    )
    if not appointment:
        raise CalendarError("There is no confirmed appointment to reschedule")
    try:
        start_at = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalendarError("start_iso must be ISO 8601") from exc
    if start_at.tzinfo is None:
        raise CalendarError("start_iso must include a timezone")
    client = await _assert_slot_available(db, context, start_at)
    end_at = start_at + timedelta(minutes=client.calendar_slot_minutes)
    token = await _access_token(client)
    settings = get_settings()
    async with httpx.AsyncClient(timeout=20) as http:
        response = await http.patch(
            f"{settings.google_calendar_api_base_url}/calendars/{quote(appointment.calendar_id, safe='')}/events/{quote(appointment.google_event_id, safe='')}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "start": {"dateTime": start_at.isoformat(), "timeZone": client.google_calendar_timezone},
                "end": {"dateTime": end_at.isoformat(), "timeZone": client.google_calendar_timezone},
            },
        )
    if response.status_code >= 400:
        raise CalendarError("Could not reschedule the Google Calendar appointment")
    appointment.start_at = start_at
    appointment.end_at = end_at
    db.commit()
    return {"ok": True, "start": start_at.isoformat(), "end": end_at.isoformat(), "timezone": client.google_calendar_timezone}


async def cancel_appointment(db: Session, context: LeadContext) -> dict:
    appointment = db.scalar(
        select(CalendarAppointment).where(
            CalendarAppointment.conversation_id == context.conversation_id,
            CalendarAppointment.status == "confirmed",
        )
    )
    if not appointment:
        raise CalendarError("There is no confirmed appointment to cancel")
    client = _client_for_context(db, context)
    token = await _access_token(client)
    settings = get_settings()
    async with httpx.AsyncClient(timeout=20) as http:
        response = await http.delete(
            f"{settings.google_calendar_api_base_url}/calendars/{quote(appointment.calendar_id, safe='')}/events/{quote(appointment.google_event_id, safe='')}",
            headers={"Authorization": f"Bearer {token}"},
        )
    if response.status_code not in (200, 204, 410):
        raise CalendarError("Could not cancel the Google Calendar appointment")
    appointment.status = "cancelled"
    db.commit()
    return {"ok": True, "status": "cancelled"}

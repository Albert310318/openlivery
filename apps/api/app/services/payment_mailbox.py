"""Gmail-compatible IMAP mailbox access and periodic payment ingestion."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
import hashlib
import imaplib
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal
from ..models_orders import RestaurantOrder
from ..models_restaurant import RestaurantPaymentMailbox, RestaurantPaymentMethod
from ..security import decrypt_secret
from .payment_mail_parsers import PAYMENT_EMAIL_PARSERS, ParsedPaymentEmail, parse_payment_email
from .payment_notifications import (
    DuplicatePaymentNotificationError,
    reconcile_payment_notifications,
    record_payment_notification,
)


logger = logging.getLogger(__name__)
# Provider parsers are ordered before the generic fallback.  Future real
# Yape/Plin parsers only need to be added to the registry in the parser module.
PARSERS = PAYMENT_EMAIL_PARSERS


class PaymentMailboxError(Exception):
    pass


def _select_payment_method(
    methods: list[RestaurantPaymentMethod], parsed: ParsedPaymentEmail,
) -> RestaurantPaymentMethod | None:
    """Compatibility helper for old adapters; mailbox polling no longer uses it.

    Payment matching is restaurant-level now.  Keeping this pure helper avoids
    breaking integrations/tests that imported the old adapter utility while
    ensuring provider/bank hints can never gate the real polling flow.
    """
    return methods[0] if len(methods) == 1 else None


def _safe_error(exc: Exception, mailbox: RestaurantPaymentMailbox, secret: str | None = None) -> str:
    detail = str(exc)
    for sensitive, replacement in (
        (mailbox.email, "[mailbox]"),
        (mailbox.imap_host, "[imap-host]"),
        (secret, "[app-password]"),
    ):
        if sensitive:
            detail = detail.replace(sensitive, replacement)
    return f"{type(exc).__name__}: {detail[:300]}" if detail else type(exc).__name__


def _connect(mailbox: RestaurantPaymentMailbox, password: str):
    settings = get_settings()
    connection = (
        imaplib.IMAP4_SSL(mailbox.imap_host, mailbox.imap_port, timeout=settings.payment_mailbox_imap_timeout_seconds)
        if mailbox.imap_ssl
        else imaplib.IMAP4(mailbox.imap_host, mailbox.imap_port, timeout=settings.payment_mailbox_imap_timeout_seconds)
    )
    connection.login(mailbox.email, password)
    return connection


def test_mailbox_connection(db: Session, mailbox: RestaurantPaymentMailbox) -> RestaurantPaymentMailbox:
    if not mailbox.encrypted_app_password:
        raise PaymentMailboxError("App password is not configured")
    checked_at = datetime.now(timezone.utc)
    password = None
    try:
        password = decrypt_secret(mailbox.encrypted_app_password)
        connection = _connect(mailbox, password)
        connection.select("INBOX", readonly=True)
        connection.logout()
    except Exception as exc:
        mailbox.connection_status = "error"
        mailbox.last_checked_at = checked_at
        mailbox.last_error = _safe_error(exc, mailbox, password)
        db.commit()
        raise PaymentMailboxError(mailbox.last_error) from exc
    mailbox.connection_status = "connected"
    mailbox.last_checked_at = checked_at
    mailbox.last_error = None
    db.commit()
    return mailbox


def _review_email(raw_message: bytes, received_at: datetime) -> ParsedPaymentEmail:
    try:
        return parse_payment_email(raw_message)
    except Exception:
        return ParsedPaymentEmail(
            source_message_id=f"raw:{hashlib.sha256(raw_message).hexdigest()}",
            sender=None,
            subject=None,
            amount=None,
            occurred_at=received_at,
            external_operation_id=None,
            receiver=None,
            parse_ready=False,
            metadata={"parser": "generic", "parse_ready": False, "parser_error": True},
        )


def _fetch_since(connection, since: date) -> list[tuple[bytes, bytes]]:
    """Fetch messages received on/after an order-relative calendar date.

    Do not use UNSEEN here.  A mailbox poll may have already marked a valid
    notification as read before its corresponding order was created.  The
    payment notification table deduplicates repeated fetches by message id.
    """

    status, data = connection.select("INBOX")
    if status != "OK":
        raise PaymentMailboxError("Could not select the INBOX")
    status, data = connection.uid("search", None, "SINCE", since.strftime("%d-%b-%Y"))
    if status != "OK":
        raise PaymentMailboxError("Could not search the INBOX")
    messages: list[tuple[bytes, bytes]] = []
    for uid in (data[0] or b"").split():
        status, fetched = connection.uid("fetch", uid, "(BODY.PEEK[])")
        if status != "OK":
            continue
        raw = next((part[1] for part in fetched if isinstance(part, tuple) and len(part) > 1), None)
        if isinstance(raw, bytes):
            messages.append((uid, raw))
    return messages


def _fetch_unseen(connection) -> list[tuple[bytes, bytes]]:
    """Backward-compatible helper for callers that used the old adapter."""

    return _fetch_since(connection, date.today())


def _message_received_at(raw_message: bytes, fallback: datetime) -> datetime:
    """Read the email receipt timestamp without trusting its payment date."""

    try:
        message = BytesParser().parsebytes(raw_message)
        parsed = parsedate_to_datetime(message.get("Date", ""))
        if parsed:
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    return fallback


def poll_mailbox(mailbox_id) -> int:
    """Poll one mailbox while holding its DB row lock for the whole pass."""

    db = SessionLocal()
    connection = None
    password = None
    processed = 0
    try:
        mailbox = db.scalar(
            select(RestaurantPaymentMailbox)
            .where(RestaurantPaymentMailbox.id == mailbox_id)
            .with_for_update(skip_locked=True)
        )
        if not mailbox:
            return 0
        checked_at = datetime.now(timezone.utc)
        mailbox.last_checked_at = checked_at
        if not mailbox.is_enabled or not mailbox.encrypted_app_password:
            mailbox.connection_status = "not_tested"
            mailbox.last_error = "App password is not configured" if mailbox.is_enabled else None
            db.commit()
            return 0
        earliest_pending_order = db.scalar(
            select(func.min(RestaurantOrder.created_at)).where(
                RestaurantOrder.client_id == mailbox.client_id,
                RestaurantOrder.source == "whatsapp",
                RestaurantOrder.payment_status == "pending",
                RestaurantOrder.order_status == "pending_payment",
            )
        )
        password = decrypt_secret(mailbox.encrypted_app_password)
        connection = _connect(mailbox, password)
        # If there are no pending digital orders there is nothing to verify.
        # Once an order exists, search from its lower bound so a notification
        # received up to 30 minutes before order creation is still considered.
        if earliest_pending_order is None:
            mailbox.connection_status = "connected"
            mailbox.last_error = None
            db.commit()
            return 0
        search_since = earliest_pending_order - timedelta(minutes=30)
        for uid, raw_message in _fetch_since(connection, search_since.date()):
            received_at = _message_received_at(raw_message, checked_at)
            parsed = _review_email(raw_message, received_at)
            try:
                with db.begin_nested():
                    record_payment_notification(
                        db,
                        client_id=mailbox.client_id,
                        # The mailbox is the restaurant-level payment
                        # destination.  The origin bank/wallet and all legacy
                        # method rows are irrelevant to matching.
                        payment_method=None,
                        notification_email=mailbox.email,
                        external_operation_id=parsed.external_operation_id,
                        source_message_id=parsed.source_message_id,
                        amount=parsed.amount,
                        occurred_at=parsed.occurred_at,
                        received_at=received_at,
                        sender=parsed.sender,
                        subject=parsed.subject,
                        metadata=parsed.metadata,
                        review_required=not parsed.parse_ready,
                        commit=False,
                    )
                processed += 1
            except DuplicatePaymentNotificationError:
                pass
            connection.uid("store", uid, "+FLAGS", "(\\Seen)")
        # This is also what makes a notification received before order
        # creation verifiable on a later poll, without requiring a second
        # email or a customer action.
        reconcile_payment_notifications(db, client_id=mailbox.client_id, now=checked_at, commit=False)
        mailbox.connection_status = "connected"
        mailbox.last_error = None
        db.commit()
        return processed
    except Exception as exc:
        db.rollback()
        logger.warning("Payment mailbox polling failed: %s", type(exc).__name__)
        try:
            mailbox = db.get(RestaurantPaymentMailbox, mailbox_id)
            if mailbox:
                mailbox.connection_status = "error"
                mailbox.last_checked_at = datetime.now(timezone.utc)
                mailbox.last_error = _safe_error(exc, mailbox, password)
                db.commit()
        except Exception:
            db.rollback()
        return processed
    finally:
        if connection is not None:
            try:
                connection.logout()
            except Exception:
                pass
        db.close()


def poll_all_mailboxes() -> None:
    db = SessionLocal()
    try:
        mailbox_ids = db.scalars(
            select(RestaurantPaymentMailbox.id).where(RestaurantPaymentMailbox.is_enabled.is_(True))
        ).all()
    finally:
        db.close()
    for mailbox_id in mailbox_ids:
        poll_mailbox(mailbox_id)


async def payment_mailbox_poll_loop() -> None:
    settings = get_settings()
    while True:
        try:
            await asyncio.to_thread(poll_all_mailboxes)
        except Exception as exc:
            logger.warning("Payment mailbox polling cycle failed: %s", type(exc).__name__)
        await asyncio.sleep(settings.payment_mailbox_poll_interval_seconds)

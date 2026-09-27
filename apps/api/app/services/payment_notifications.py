"""Legacy storage for parsed mailbox notifications.

Mailbox parsing remains available for audit and compatibility, but manual
administrator review is the only active path that can confirm an order.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models_orders import PaymentNotification, RestaurantOrder
from ..models_restaurant import RestaurantPaymentMethod
from ..models import now_utc


PAYMENT_NOTIFICATION_LOOKBACK = timedelta(minutes=30)


class DuplicatePaymentNotificationError(Exception):
    pass


def requires_payment_notification(order: RestaurantOrder) -> bool:
    # IMAP remains available only as a legacy adapter. Manual administrator
    # review is the sole operational payment confirmation path.
    return False


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _operation_key(value: str | None) -> str | None:
    """Return the comparison form of an operation code.

    Operation codes are identifiers, not amounts: preserve zeroes and
    punctuation, while ignoring harmless surrounding whitespace/case.
    """

    if not value:
        return None
    normalized = value.strip().casefold()
    return normalized or None


def _notification_is_in_order_window(
    order: RestaurantOrder,
    received_at: datetime,
    *,
    now: datetime | None = None,
) -> bool:
    """Whether the verification email was received in the allowed window.

    This intentionally uses the email's receipt time, rather than the bank's
    operation time.  Banking notifications can arrive before the order has
    finished being created.
    """

    received_at = _as_utc(received_at)
    now = _as_utc(now or datetime.now(timezone.utc))
    return _as_utc(order.created_at) - PAYMENT_NOTIFICATION_LOOKBACK <= received_at <= now


def _eligible_orders(
    db: Session,
    *,
    client_id,
    amount: Decimal | None,
    external_operation_id: str | None,
    received_at: datetime,
    now: datetime | None = None,
) -> list[RestaurantOrder]:
    rows = db.scalars(
        select(RestaurantOrder)
        .where(
            RestaurantOrder.client_id == client_id,
            RestaurantOrder.source == "whatsapp",
            RestaurantOrder.payment_status == "pending",
            RestaurantOrder.order_status == "pending_payment",
        )
        .with_for_update()
    ).all()
    operation_key = _operation_key(external_operation_id)
    return [
        order
        for order in rows
        if amount is not None
        and order.payment_reference
        and _operation_key(order.payment_reference) == operation_key
        and order.reported_payment_amount is not None
        and Decimal(order.reported_payment_amount) == amount
        and Decimal(order.subtotal) == amount
        and _notification_is_in_order_window(order, received_at, now=now)
    ]


def _try_match_notification(
    db: Session,
    notification: PaymentNotification,
    *,
    now: datetime | None = None,
) -> RestaurantOrder | None:
    """Try to match an existing notification exactly once.

    Matching is deliberately independent of the origin bank/wallet and of a
    customer-supplied image.  A notification needs both an operation code and
    an amount; an ambiguous amount match remains pending for later review.
    """

    if notification.status == "matched":
        return db.get(RestaurantOrder, notification.matched_order_id) if notification.matched_order_id else None
    if not notification.external_operation_id or notification.amount is None:
        notification.status = "review_required"
        return None

    candidates = _eligible_orders(
        db,
        client_id=notification.client_id,
        amount=notification.amount,
        external_operation_id=notification.external_operation_id,
        received_at=notification.received_at,
        now=now,
    )
    if len(candidates) != 1:
        notification.status = "ambiguous" if len(candidates) > 1 else "unmatched"
        return None

    order = candidates[0]
    if order.client_id != notification.client_id:
        raise ValueError("Payment match crossed client boundary")

    verification_time = _as_utc(now or datetime.now(timezone.utc))
    notification.status = "matched"
    notification.matched_order_id = order.id
    notification.processed_at = verification_time
    order.payment_status = "confirmed"
    order.payment_reference = notification.external_operation_id
    # This is the verification timestamp, not the bank operation timestamp.
    order.payment_confirmed_at = verification_time
    order.order_status = "confirmed"
    return order


def reconcile_payment_notifications(
    db: Session,
    *,
    client_id=None,
    now: datetime | None = None,
    commit: bool = True,
) -> int:
    """Legacy no-op retained so old adapters cannot confirm an order."""

    return 0


def record_payment_notification(
    db: Session,
    *,
    client_id,
    payment_method: RestaurantPaymentMethod | None,
    notification_email: str | None,
    external_operation_id: str | None,
    amount: Decimal | None,
    source_message_id: str | None = None,
    occurred_at: datetime,
    received_at: datetime | None = None,
    sender: str | None = None,
    subject: str | None = None,
    metadata: dict | None = None,
    review_required: bool = False,
    commit: bool = True,
) -> PaymentNotification:
    """Persist one parsed mailbox notification without changing an order."""

    if payment_method is not None and payment_method.client_id != client_id:
        raise ValueError("Payment method does not belong to the notification client")
    occurred_at = _as_utc(occurred_at)
    received_at = _as_utc(received_at or now_utc())
    operation_id = external_operation_id.strip() if external_operation_id else None
    # The mailbox belongs to the restaurant, so a missing parser-level method
    # hint is not a reason to reject a valid deposit.  Origin bank/wallet is
    # never used for matching.
    needs_review = review_required or amount is None or not operation_id
    if operation_id or source_message_id:
        identity_conditions = []
        if operation_id:
            identity_conditions.append(
                func.lower(func.btrim(PaymentNotification.external_operation_id))
                == _operation_key(operation_id)
            )
        if source_message_id:
            identity_conditions.append(PaymentNotification.source_message_id == source_message_id)
        existing = db.scalar(
            select(PaymentNotification)
            .where(
                PaymentNotification.client_id == client_id,
                or_(*identity_conditions),
            )
            .with_for_update()
        )
        if existing:
            raise DuplicatePaymentNotificationError(operation_id or source_message_id or "unknown")

    notification = PaymentNotification(
        client_id=client_id,
        payment_method_id=payment_method.id if payment_method else None,
        notification_email=(notification_email or (payment_method.notification_email if payment_method else None) or "").strip().lower() or None,
        external_operation_id=operation_id,
        source_message_id=source_message_id,
        amount=amount,
        occurred_at=occurred_at,
        received_at=received_at,
        sender=sender,
        subject=subject,
        notification_metadata=metadata or {},
        status="review_required" if needs_review else "unmatched",
    )
    try:
        db.add(notification)
        db.flush()
        # Mailbox notifications are retained for audit/compatibility only.
        # They never change an order in the manual-review design.
        notification.status = "review_required" if needs_review else "unmatched"
        if commit:
            db.commit()
            db.refresh(notification)
        return notification
    except IntegrityError as exc:
        if commit:
            db.rollback()
        if operation_id or source_message_id:
            raise DuplicatePaymentNotificationError(operation_id or source_message_id or "unknown") from exc
        raise
    except Exception:
        if commit:
            db.rollback()
        raise


def payment_verification_status(db: Session, order: RestaurantOrder) -> str:
    if order.payment_status == "confirmed":
        return "confirmed"
    if getattr(order, "payment_review_status", "pending") == "review":
        return "review"
    if getattr(order, "payment_review_status", "pending") == "rejected":
        return "rejected"
    return "pending"

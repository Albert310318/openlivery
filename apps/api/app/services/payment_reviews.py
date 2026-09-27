"""Manual review workflow for restaurant payment vouchers."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import now_utc
from ..models_orders import RestaurantOrder


def attach_payment_receipt(
    db: Session,
    *,
    conversation_id: uuid.UUID,
    data: bytes,
    filename: str | None,
    mime: str | None,
) -> bool:
    """Keep an inbound voucher on the current pending restaurant order.

    This only stores the submitted evidence. It never changes payment or order
    status; the customer must still report the operation/amount and an
    authorized administrator must decide.
    """

    order = db.scalar(
        select(RestaurantOrder)
        .where(
            RestaurantOrder.conversation_id == conversation_id,
            RestaurantOrder.source == "whatsapp",
            RestaurantOrder.order_status == "pending_payment",
            RestaurantOrder.payment_status == "pending",
        )
        .order_by(RestaurantOrder.created_at.desc())
    )
    if order is None:
        return False
    order.payment_receipt_data = data
    order.payment_receipt_filename = (filename or "comprobante").strip()[:255] or "comprobante"
    order.payment_receipt_mime = (mime or "application/octet-stream").strip()[:120]
    order.receipt_submitted = True
    order.payment_reported_at = order.payment_reported_at or now_utc()
    order.payment_review_status = "review"
    db.flush()
    return True

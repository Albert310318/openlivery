"""Notifications and sanitized delivery dispatches for restaurant orders."""

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Client, Conversation, WhatsAppChannel, WhatsAppCloudChannel
from ..models_orders import RestaurantDeliveryNotification, RestaurantOrder
from ..models_restaurant import RestaurantModality
from ..security import decrypt_secret
from .whatsapp import bridge_command, send_channel_message
from .whatsapp_cloud import send_text

logger = logging.getLogger(__name__)


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _whatsapp_target(value: str | None) -> str | None:
    digits = _digits(value)
    if len(digits) == 9 and digits.startswith("9"):
        digits = "51" + digits
    return digits if 7 <= len(digits) <= 15 else None


def _delivery_card(order: RestaurantOrder) -> str:
    lines = [
        "NUEVO PEDIDO PARA DELIVERY",
        "",
        f"Pedido: {order.order_number}",
        f"Cliente: {order.customer_name or 'Sin nombre'}",
        f"WhatsApp/Teléfono: {order.customer_phone or 'No registrado'}",
        f"Dirección: {order.address or 'No registrada'}",
        f"Referencia: {order.address_reference or 'No registrada'}",
        "",
        "Pedido:",
    ]
    for item in order.items:
        if not item.is_cancelled:
            lines.append(f"{item.quantity} x {item.product_name}")
    lines.append("")
    lines.append("Costo de delivery: coordinar directamente con el cliente.")
    return "\n".join(lines)


async def notify_pickup_ready(db: Session, order: RestaurantOrder) -> bool:
    """Notify the customer's original conversation when a pickup is ready."""
    if not order.conversation_id:
        return False
    conversation = db.get(Conversation, order.conversation_id)
    client = db.get(Client, order.client_id)
    if not conversation or not client:
        return False
    await send_channel_message(db, conversation, pickup_ready_text(order, client.name))
    return True


def pickup_ready_text(order: RestaurantOrder, restaurant_name: str) -> str:
    return f"¡Tu pedido {order.order_number} ya está listo! Puedes acercarte a {restaurant_name} para recogerlo."


async def notify_delivery_ready(db: Session, order: RestaurantOrder) -> bool:
    """Send only operational delivery data to the configured delivery number."""
    modality = db.scalar(select(RestaurantModality).where(RestaurantModality.client_id == order.client_id))
    target = _whatsapp_target(modality.delivery_whatsapp if modality else None)
    notification = db.scalar(select(RestaurantDeliveryNotification).where(RestaurantDeliveryNotification.order_id == order.id))
    if notification and notification.status == "sent" and notification.external_message_id:
        return True
    if not notification:
        notification = RestaurantDeliveryNotification(client_id=order.client_id, order_id=order.id, recipient=target or "unknown")
        db.add(notification)
        db.commit()
        db.refresh(notification)
    elif target and notification.recipient != target:
        notification.recipient = target
    if not target:
        notification.status = "error"
        notification.error_message = "No hay un WhatsApp válido configurado para el responsable de Delivery."
        db.commit()
        return False

    card = _delivery_card(order)
    try:
        external_message_id = None
        provider = None
        bridge = db.scalar(
            select(WhatsAppChannel)
            .where(
                WhatsAppChannel.client_id == order.client_id,
                WhatsAppChannel.is_enabled.is_(True),
                WhatsAppChannel.status == "connected",
            )
            .order_by(WhatsAppChannel.created_at.desc())
        )
        if bridge:
            provider = "whatsapp_qr"
            result = await bridge_command(
                "POST",
                f"/channels/{bridge.id}/send",
                {"remote_jid": f"{target}@s.whatsapp.net", "text": card},
            )
            external_message_id = result.get("external_message_id")
        else:
            cloud = db.scalar(
                select(WhatsAppCloudChannel)
                .where(
                    WhatsAppCloudChannel.client_id == order.client_id,
                    WhatsAppCloudChannel.is_enabled.is_(True),
                    WhatsAppCloudChannel.status == "connected",
                    WhatsAppCloudChannel.encrypted_access_token.is_not(None),
                )
                .order_by(WhatsAppCloudChannel.created_at.desc())
            )
            if cloud and cloud.phone_number_id:
                provider = "whatsapp_cloud"
                external_message_id = await send_text(decrypt_secret(cloud.encrypted_access_token), cloud.phone_number_id, target, card)
        if not provider or not external_message_id:
            raise RuntimeError("No hay un canal WhatsApp conectado o el proveedor no confirmó el mensaje")
        notification.provider = provider
        notification.status = "sent"
        notification.external_message_id = external_message_id
        notification.error_message = None
        notification.attempts += 1
        notification.sent_at = datetime.now(timezone.utc)
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        notification = db.get(RestaurantDeliveryNotification, notification.id)
        if notification:
            notification.status = "error"
            notification.error_message = str(getattr(exc, "detail", exc))[:1000]
            notification.attempts += 1
            db.commit()
        logger.exception("Delivery notification failed for order %s", order.order_number)
        return False


def delivery_order_view(order: RestaurantOrder) -> dict:
    """Return the fields a delivery operator needs, excluding restaurant finances."""
    return {
        "id": order.id,
        "client_id": order.client_id,
        "order_number": order.order_number,
        "modality": order.modality,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "address": order.address,
        "address_reference": order.address_reference,
        "order_status": order.order_status,
        "items": [
            {
                "id": item.id,
                "product_name": item.product_name,
                "quantity": item.quantity,
                "observations": item.observations,
                "variants": item.variants,
                "extras": item.extras,
            }
            for item in order.items
            if not item.is_cancelled
        ],
        "created_at": order.created_at,
        "ready_at": order.ready_at,
        "delivered_at": order.delivered_at,
    }

import base64
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Agent, Conversation, Message
from ..models_restaurant import RestaurantWelcomeFlyer

MAX_WELCOME_FLYER_BYTES = 8 * 1024 * 1024
WELCOME_FLYER_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


def validate_welcome_flyer(data: bytes, mime_type: str | None) -> str:
    mime = (mime_type or "").split(";")[0].strip().lower()
    if mime not in WELCOME_FLYER_MIME_TYPES:
        raise ValueError("El flyer debe ser JPG, PNG o WEBP")
    if not data or len(data) > MAX_WELCOME_FLYER_BYTES:
        raise ValueError("El flyer no puede superar 8 MB")
    valid_signature = {
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    }
    if not valid_signature[mime]:
        raise ValueError("El contenido del archivo no coincide con su formato")
    return mime


def welcome_flyer_payload(flyer: RestaurantWelcomeFlyer, client_id: uuid.UUID) -> dict:
    return {
        "id": flyer.id,
        "client_id": client_id,
        "filename": flyer.filename,
        "mime_type": flyer.mime_type,
        "enabled": flyer.enabled,
        "message": flyer.message,
        "image_url": f"/api/restaurants/{client_id}/welcome-flyer/image",
        "created_at": flyer.created_at,
        "updated_at": flyer.updated_at,
    }


def create_welcome_flyer_message(db: Session, conversation: Conversation, *, only_new: bool = True) -> Message | None:
    if not only_new:
        return None
    flyer = db.scalar(
        select(RestaurantWelcomeFlyer).where(
            RestaurantWelcomeFlyer.client_id == conversation.client_id,
            RestaurantWelcomeFlyer.enabled.is_(True),
        )
    )
    if not flyer:
        return None
    existing = db.scalar(
        select(Message.id).where(
            Message.conversation_id == conversation.id,
            Message.media_kind == "welcome_flyer",
        )
    )
    if existing:
        return None
    agent = db.get(Agent, conversation.agent_id)
    message = Message(
        conversation_id=conversation.id,
        role="assistant",
        # media_kind is the internal label; an empty configured message must
        # remain empty so clients receive only the image.
        content=flyer.message or "",
        sender_type="ai",
        sender_name=agent.name if agent else None,
        media_kind="welcome_flyer",
        media_mime=flyer.mime_type,
        media_filename=flyer.filename,
        media_data=flyer.image_data,
    )
    db.add(message)
    db.flush()
    return message


def flyer_transport_payload(message: Message) -> dict | None:
    if not message.media_data or message.media_kind != "welcome_flyer":
        return None
    return {
        "message_id": message.id,
        "data": base64.b64encode(message.media_data).decode("ascii"),
        "mime": message.media_mime or "image/jpeg",
        "caption": message.content or None,
    }

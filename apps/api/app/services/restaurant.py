"""Shared restaurant-client detection for conversational flows.

Operational restaurant orders live in ``restaurant_orders.py``.  This module
only keeps the industry classifier shared by WhatsApp and agent tools.
"""

import re
import unicodedata

from ..models import Client


def _plain(value: str) -> str:
    value = "".join(
        char for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )
    return " ".join(re.findall(r"[a-z0-9]+", value))


def is_restaurant_client(client: Client | None) -> bool:
    if not client:
        return False
    text = _plain(f"{client.industry} {client.description}")
    return any(
        token in text
        for token in (
            "restaurant",
            "restaurante",
            "polleria",
            "comida",
            "gastronomia",
            "cevicheria",
            "pizzeria",
            "cafeteria",
        )
    )

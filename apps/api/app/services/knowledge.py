import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Agent, KnowledgeChunk, KnowledgeDocument
from ..models_restaurant import (
    MenuCategory,
    MenuProduct,
    RestaurantModality,
    RestaurantPaymentMethod,
    RestaurantProfile,
    RestaurantStaff,
)
from .embeddings import cosine_similarity, embed_query, embed_texts
from .providers import resolve_provider_credentials


MAX_FULL_CONTEXT_CHARS = 45_000
MAX_SEARCH_CONTEXT_CHARS = 32_000


@dataclass
class KnowledgeResult:
    text: str
    sources: list[dict]


def _terms(query: str) -> set[str]:
    return {word.lower() for word in re.findall(r"[\wáéíóúñü]{3,}", query, flags=re.IGNORECASE)}


def _chunks(text: str, size: int = 1800) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    result: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= size:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            result.append(current)
        if len(paragraph) <= size:
            current = paragraph
        else:
            result.extend(paragraph[i : i + size] for i in range(0, len(paragraph), size))
            current = ""
    if current:
        result.append(current)
    return result


def build_knowledge(agent: Agent, documents: list[KnowledgeDocument], query: str) -> KnowledgeResult:
    valid_docs = [doc for doc in documents if doc.status == "processed" and doc.extracted_text.strip()]
    total = sum(len(doc.extracted_text) for doc in valid_docs)
    sources: list[dict] = []

    if total <= MAX_FULL_CONTEXT_CHARS:
        sections = []
        for doc in valid_docs:
            sections.append(f"DOCUMENTO: {doc.filename}\n{doc.extracted_text}")
            sources.append({"id": str(doc.id), "filename": doc.filename, "excerpt": doc.extracted_text[:220].strip()})
        return KnowledgeResult(text="\n\n".join(sections), sources=sources)

    terms = _terms(query)
    ranked: list[tuple[int, KnowledgeDocument, str]] = []
    for doc in valid_docs:
        for chunk in _chunks(doc.extracted_text):
            lower = chunk.lower()
            score = sum(lower.count(term) for term in terms)
            ranked.append((score, doc, chunk))
    ranked.sort(key=lambda item: item[0], reverse=True)

    selected = []
    used_chars = 0
    seen_docs: set[str] = set()
    for score, doc, chunk in ranked:
        if terms and score == 0 and selected:
            break
        if used_chars + len(chunk) > MAX_SEARCH_CONTEXT_CHARS:
            continue
        selected.append(f"DOCUMENTO: {doc.filename}\n{chunk}")
        used_chars += len(chunk)
        if str(doc.id) not in seen_docs:
            sources.append({"id": str(doc.id), "filename": doc.filename, "excerpt": chunk[:220].strip()})
            seen_docs.add(str(doc.id))
        if used_chars >= MAX_SEARCH_CONTEXT_CHARS:
            break
    return KnowledgeResult(text="\n\n".join(selected), sources=sources)


async def embed_document_chunks(db: Session, agent: Agent, document: KnowledgeDocument) -> int:
    """Chunk a processed document and store embeddings for semantic search.

    Best-effort: returns 0 (and stores nothing) when the agent has no connection
    or the provider does not support embeddings — keyword search still works.
    """
    if not document.extracted_text.strip():
        return 0
    credentials = resolve_provider_credentials(db, agent.agency_id, "openai")
    if not credentials:
        return 0
    pieces = _chunks(document.extracted_text)
    if not pieces:
        return 0
    base_url, api_key = credentials
    vectors = await embed_texts(base_url, api_key, pieces)
    if not vectors:
        return 0
    db.add_all(
        KnowledgeChunk(
            document_id=document.id,
            agent_id=agent.id,
            position=index,
            content=content,
            embedding=vector,
        )
        for index, (content, vector) in enumerate(zip(pieces, vectors))
    )
    db.commit()
    return len(pieces)


async def retrieve_knowledge(db: Session, agent: Agent, query: str) -> KnowledgeResult:
    """Return the knowledge context for a query.

    Small knowledge bases are included in full. Larger ones use semantic search
    over stored embeddings, falling back to keyword ranking when embeddings are
    unavailable.
    """
    documents = list(
        db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.agent_id == agent.id)).all()
    )
    valid_docs = [doc for doc in documents if doc.status == "processed" and doc.extracted_text.strip()]
    total = sum(len(doc.extracted_text) for doc in valid_docs)
    if total <= MAX_FULL_CONTEXT_CHARS:
        return build_knowledge(agent, valid_docs, query)

    semantic = await _semantic_search(db, agent, query)
    return semantic if semantic is not None else build_knowledge(agent, valid_docs, query)


async def _semantic_search(db: Session, agent: Agent, query: str) -> KnowledgeResult | None:
    credentials = resolve_provider_credentials(db, agent.agency_id, "openai")
    if not credentials:
        return None
    chunks = list(
        db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.agent_id == agent.id)).all()
    )
    chunks = [chunk for chunk in chunks if chunk.embedding]
    if not chunks:
        return None
    base_url, api_key = credentials
    query_vector = await embed_query(base_url, api_key, query)
    if not query_vector:
        return None

    ranked = sorted(chunks, key=lambda chunk: cosine_similarity(query_vector, chunk.embedding), reverse=True)
    selected: list[str] = []
    sources: list[dict] = []
    seen_docs: set[str] = set()
    used_chars = 0
    for chunk in ranked:
        if used_chars + len(chunk.content) > MAX_SEARCH_CONTEXT_CHARS:
            continue
        filename = chunk.document.filename
        selected.append(f"DOCUMENTO: {filename}\n{chunk.content}")
        used_chars += len(chunk.content)
        if str(chunk.document_id) not in seen_docs:
            sources.append({"id": str(chunk.document_id), "filename": filename, "excerpt": chunk.content[:220].strip()})
            seen_docs.add(str(chunk.document_id))
        if used_chars >= MAX_SEARCH_CONTEXT_CHARS:
            break
    if not selected:
        return None
    return KnowledgeResult(text="\n\n".join(selected), sources=sources)


def _business_brief(agent: Agent) -> str:
    """Compose the filled structured-brief fields into a labelled block."""
    fields = (
        ("Qué hace el negocio", agent.brief_summary),
        ("Productos y servicios", agent.brief_products),
        ("Público objetivo", agent.brief_audience),
        ("Información y políticas clave", agent.brief_policies),
        ("Objetivo principal del agente", agent.brief_goal),
        ("Debe hacer siempre", agent.brief_dos),
        ("No debe hacer nunca", agent.brief_donts),
    )
    lines = [f"- {label}: {value.strip()}" for label, value in fields if value.strip()]
    return "\n".join(lines)


def _is_restaurant_agent(agent: Agent) -> bool:
    industry = (agent.client.industry or "").strip().casefold()
    return industry == "restaurante"


def _normalized_payment_value(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _grouped_payment_methods(payments: list[RestaurantPaymentMethod]) -> list[dict]:
    groups: dict[str, dict] = {}
    for payment in payments:
        account_number = (payment.account_number or "").strip()
        account_key = _normalized_payment_value(account_number)
        # A missing account cannot safely be merged with another payment.
        group_key = f"account:{account_key}" if account_key else f"payment:{payment.id or id(payment)}"
        group = groups.setdefault(
            group_key,
            {
                "account_number": account_number,
                "account_names": {},
                "instructions": {},
                "receipt_required": False,
            },
        )
        account_name = (payment.account_name or "").strip()
        account_name_key = _normalized_payment_value(account_name)
        if account_name_key and account_name_key not in group["account_names"]:
            group["account_names"][account_name_key] = account_name
        instructions = (payment.instructions or "").strip()
        instructions_key = _normalized_payment_value(instructions)
        if instructions_key and instructions_key not in group["instructions"]:
            group["instructions"][instructions_key] = instructions
        group["receipt_required"] = group["receipt_required"] or payment.receipt_required
    return list(groups.values())


def _canonical_payment_method(payments: list[RestaurantPaymentMethod]) -> RestaurantPaymentMethod | None:
    """Return the one customer-facing payment destination.

    Legacy rows remain in the database for historical orders, but the agent
    must expose only the canonical ``Datos para pagos`` destination.
    """
    return next(
        (
            row for row in payments
            if (row.display_name or "").strip().casefold() == "datos para pagos"
            or row.method == "other"
        ),
        payments[0] if payments else None,
    )


def _restaurant_operational_context(db: Session, agent: Agent) -> str:
    client_id = agent.client_id
    profile = db.scalar(select(RestaurantProfile).where(RestaurantProfile.client_id == client_id))
    categories = db.scalars(
        select(MenuCategory)
        .options(selectinload(MenuCategory.products).selectinload(MenuProduct.variants), selectinload(MenuCategory.products).selectinload(MenuProduct.extras))
        .where(MenuCategory.client_id == client_id, MenuCategory.is_active.is_(True))
        .order_by(MenuCategory.position, MenuCategory.created_at)
    ).unique().all()
    modalities = db.scalar(select(RestaurantModality).where(RestaurantModality.client_id == client_id))
    payment_rows = db.scalars(
        select(RestaurantPaymentMethod)
        .where(RestaurantPaymentMethod.client_id == client_id, RestaurantPaymentMethod.is_active.is_(True))
        .order_by(RestaurantPaymentMethod.created_at)
    ).all()
    canonical_payment = _canonical_payment_method(payment_rows)
    payments = [canonical_payment] if canonical_payment else []
    staff = db.scalars(
        select(RestaurantStaff).where(RestaurantStaff.client_id == client_id, RestaurantStaff.is_active.is_(True))
    ).all()
    currency = profile.currency if profile else "PEN"
    lines = [
        "Este bloque es la fuente de verdad estructurada del restaurante. No lo sustituyas con suposiciones.",
        f"RESTAURANTE: {agent.client.name}",
        f"DESCRIPCIÓN: {(agent.client.description or '').strip() or 'No configurada'}",
        f"SALUDO CONFIGURADO: {(agent.widget_greeting or '').strip() or 'No configurado'}",
    ]
    if profile:
        lines.extend([
            f"DIRECCIÓN: {profile.address or 'No configurada'}",
            f"TELÉFONO COMERCIAL: {profile.phone or 'No configurado'}",
            f"MONEDA: {currency}",
            f"HORARIOS: {profile.opening_hours or 'No configurados'}",
        ])
    else:
        lines.append(f"MONEDA: {currency}")

    lines.append("CARTA DISPONIBLE:")
    menu_items = 0
    for category in categories:
        products = [product for product in category.products if product.is_available]
        if not products:
            continue
        lines.append(f"- CATEGORÍA: {category.name}")
        for product in products:
            menu_items += 1
            variants = ", ".join(
                f"{item.name} (+{item.price_delta} {currency})"
                for item in product.variants
                if item.is_available
            ) or "Ninguna"
            extras = ", ".join(
                f"{item.name} ({item.price} {currency})"
                for item in product.extras
                if item.is_available
            ) or "Ninguno"
            lines.append(
                f"  - {product.name}: {product.price} {currency}; "
                f"descripción: {product.description or 'No configurada'}; "
                f"variantes: {variants}; adicionales: {extras}"
            )
    if not menu_items:
        lines.append("- No hay productos disponibles configurados.")

    lines.append("MODALIDADES ACTIVAS:")
    if modalities and modalities.dine_in_enabled:
        lines.append("- MESA: no pedir dirección; el pedido seguirá posteriormente el flujo Mesero -> Cocina -> Mesa -> Caja y el pago se realiza en caja indicando el número de mesa.")
    if modalities and modalities.pickup_enabled:
        lines.append("- RECOJO: confirmar nombre y pedido, e informar las condiciones y tiempos configurados; no pedir dirección de delivery.")
    if modalities and modalities.delivery_enabled:
        lines.append(
            "- DELIVERY: solicitar nombre, teléfono, dirección exacta y referencia, además de cualquier variante pendiente. "
            "Cobrar únicamente el subtotal de productos. El costo de delivery no está incluido y el personal coordina ese importe directamente con el cliente."
        )
        lines.append(f"- WHATSAPP RESPONSABLE DE DELIVERY: {modalities.delivery_whatsapp or 'No configurado'}.")
    if not modalities or not any((modalities.dine_in_enabled, modalities.pickup_enabled, modalities.delivery_enabled)):
        lines.append("- No hay modalidades activas configuradas.")

    lines.append("DATOS PARA PAGOS:")
    if payments:
        for group in _grouped_payment_methods(payments):
            details = "; ".join(
                value for value in (
                    f"número para pagos: {group['account_number']}" if group["account_number"] else "",
                    f"titular: {', '.join(group['account_names'].values())}" if group["account_names"] else "",
                    f"instrucciones para el cliente: {'; '.join(group['instructions'].values())}" if group["instructions"] else "",
                ) if value
            )
            lines.append(f"- {details}; comprobante solicitado: {'sí' if group['receipt_required'] else 'no'}.")
    else:
        lines.append("- No hay datos para pagos configurados.")

    role_names = {"admin": "Administrador", "cashier": "Caja", "waiter": "Mesero", "kitchen": "Cocina", "delivery": "Delivery"}
    active_roles = sorted({role_names.get(item.role, item.role) for item in staff})
    lines.append(f"PERSONAL ACTIVO CONFIGURADO: {', '.join(active_roles) if active_roles else 'No configurado'}.")
    lines.extend([
        "REGLAS OPERATIVAS:",
        "- Saluda según el saludo configurado, identifica qué desea pedir el cliente y usa solo la carta disponible.",
        "- No inventes productos, precios, promociones, ingredientes, disponibilidad ni datos para pagos.",
        "- El número configurado es únicamente el destino o receptor del abono. El cliente puede pagar desde cualquier banco o billetera; el origen del pago es irrelevante y nunca debe rechazarse por no estar configurado.",
        "- Permite únicamente variantes y adicionales configurados; en delivery el total del pedido es únicamente el subtotal de productos.",
        "- En delivery solicita nombre, teléfono, dirección exacta y referencia. No pidas distrito o zona para calcular una tarifa y no inventes costos de delivery.",
        '- Comunica: "El costo del delivery no está incluido. El personal de delivery se comunicará contigo para coordinar directamente el costo de la entrega."',
        '- Para un abono anticipado de productos, comunica: "Realiza el pago de S/ XX.XX al número [número para pagos] y envíame tu comprobante." Sustituye XX.XX únicamente por el subtotal de productos.',
        "- Muestra el pedido completo y confirma antes de finalizar.",
        "- Un comprobante enviado no confirma automáticamente un pago digital; la confirmación depende de la revisión manual del administrador (monto, pedido/cliente, operación no utilizada y demás controles), no solo de lo que afirme el cliente.",
        "- Después de crear un pedido de WhatsApp en PENDING_PAYMENT, continúa obligatoriamente con el pago: informa que quedó registrado y pendiente de pago, comunica el total exacto, el Número para pagos y titular configurados, solicita el abono y pide el comprobante después del pago.",
        "- Mientras el pedido esté PENDING_PAYMENT no digas que puede recogerlo, que está listo ni que se le espera; si el cliente responde ok/gracias, recuerda de forma natural que falta realizar y verificar el pago.",
        "- Cuando el cliente informe número de operación y monto, usa report_restaurant_payment. Un voucher o imagen nunca confirma el pago: registra los datos y deja el pedido en revisión manual para un administrador. Solo la acción explícita del administrador confirma el pago y lo envía a Cocina.",
    ])
    return "\n".join(lines)


def build_system_prompt(agent: Agent, knowledge_text: str, db: Session | None = None) -> str:
    client = agent.client
    tz_name = (agent.timezone or "UTC").strip() or "UTC"
    try:
        now = datetime.now(ZoneInfo(tz_name))
    except (ZoneInfoNotFoundError, ValueError):
        tz_name = "UTC"
        now = datetime.now(ZoneInfo("UTC"))
    restaurant = db is not None and _is_restaurant_agent(agent)
    parts = [
        f"Eres {agent.name}, un agente de IA de {client.name}.",
        f"FECHA Y HORA ACTUAL ({tz_name}): {now:%Y-%m-%d %H:%M}.",
        f"PERSONALIDAD Y TONO:\n{agent.personality or 'Profesional, claro y amable.'}",
    ]
    if restaurant:
        parts.append(f"CONTEXTO OPERATIVO ESTRUCTURADO DEL RESTAURANTE:\n{_restaurant_operational_context(db, agent)}")
        if agent.instructions.strip():
            parts.append(f"INSTRUCCIONES ESPECIALES COMPLEMENTARIAS:\n{agent.instructions.strip()}")
    else:
        parts.append(f"INSTRUCCIONES PRINCIPALES:\n{agent.instructions or 'Responde de forma útil y precisa.'}")
    brief = _business_brief(agent)
    if brief:
        parts.append(f"BRIEF DEL NEGOCIO:\n{brief}")
    if client.general_context.strip():
        parts.append(f"CONTEXTO GENERAL DEL CLIENTE:\n{client.general_context}")
    if agent.manual_context.strip():
        parts.append(f"CONTEXTO MANUAL DEL AGENTE:\n{agent.manual_context}")
    if agent.qa_pairs:
        faq = "\n\n".join(f"P: {qa.question}\nR: {qa.answer}" for qa in agent.qa_pairs)
        parts.append(f"PREGUNTAS FRECUENTES:\n{faq}")
    if knowledge_text.strip():
        parts.append(
            "CONOCIMIENTO DOCUMENTAL:\n"
            f"{knowledge_text}\n\n"
            "Usa este conocimiento cuando sea relevante. No inventes información que no aparezca aquí."
        )
    return "\n\n".join(parts)

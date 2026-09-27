"""Deterministic parsers for payment-mail messages.

Parsers are deliberately small and deterministic.  A provider parser may
return ``None`` when the message is not one of its formats; the generic parser
is kept as the final fallback.  This lets us add real Yape/Plin parsers later
without changing mailbox polling or matching.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
import hashlib
import html
import re


_AMOUNT_RE = re.compile(
    r"(?im)\b(?:monto|importe|amount|abono|dep[oó]sito|total(?:\s+(?:recibido|paid))?)\b"
    r"[^\d$€£S/]{0,30}(?:S\s*/\s*|PEN\s*|USD\s*|\$\s*|€\s*|£\s*)?"
    r"(?P<amount>\d[\d.,]*)"
)
_OPERATION_RE = re.compile(
    r"(?im)\b(?:operaci[oó]n|referencia|n[uú]mero\s+de\s+operaci[oó]n|"
    r"n[°ºo]?\s*de\s*operaci[oó]n|c[oó]digo(?:\s+de)?\s+operaci[oó]n|"
    r"transaction\s+id|operation\s+id|operation\s+code|transaction)\b\s*[:#-]?\s*"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9._/-]{2,})"
)
_DATETIME_RE = re.compile(
    r"(?im)\b(?:fecha(?:\s+y\s+hora)?|hora|date|time|fecha\s+de\s+operaci[oó]n)\b"
    r"[^\d]{0,20}(?P<value>\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?)?"
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{4}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)"
)
_RECEIVER_RE = re.compile(
    r"(?im)\b(?:cuenta|billetera|wallet|receptor|destino|recipient|account)\b"
    r"\s*[:#-]?\s*(?P<value>[^\r\n]{1,120})"
)


@dataclass(frozen=True)
class ParsedPaymentEmail:
    source_message_id: str
    sender: str | None
    subject: str | None
    amount: Decimal | None
    occurred_at: datetime
    external_operation_id: str | None
    receiver: str | None
    parse_ready: bool
    metadata: dict
    provider: str | None = None
    payment_method_hint: str | None = None


def _normalize_amount(value: str) -> Decimal | None:
    raw = value.replace(" ", "")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".") if len(raw.rsplit(",", 1)[1]) == 2 else raw.replace(",", "")
    elif "." in raw and len(raw.rsplit(".", 1)[1]) == 3:
        raw = raw.replace(".", "")
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None
    return amount if amount > 0 else None


def _parse_datetime(value: str, *, default_timezone=timezone.utc) -> datetime | None:
    raw = value.strip().replace("Z", "+00:00")
    for candidate in (raw, raw.replace("/", "-")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.replace(tzinfo=default_timezone) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    formats = (
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%d/%m/%Y", "%d-%m-%Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=default_timezone).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _text_content(message: Message) -> str:
    parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() != "text" or part.get_content_disposition() == "attachment":
                continue
            try:
                content = part.get_content()
            except (LookupError, UnicodeError):
                continue
            if isinstance(content, str):
                parts.append(content)
    else:
        try:
            content = message.get_content()
        except (LookupError, UnicodeError):
            content = ""
        if isinstance(content, str):
            parts.append(content)
    text = "\n".join(parts)
    text = re.sub(r"</?(?:br|div|p|tr|li)\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


def _header_datetime(message: Message) -> datetime:
    raw = message.get("Date", "")
    try:
        parsed = parsedate_to_datetime(raw)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        return datetime.now(timezone.utc)


def _header_timezone(message: Message):
    try:
        parsed = parsedate_to_datetime(message.get("Date", ""))
        return parsed.tzinfo or timezone.utc
    except (TypeError, ValueError, IndexError):
        return timezone.utc


_LEMON_SENDER = "notificaciones@lemoncash.com.ar"
_LEMON_AMOUNT_RE = re.compile(
    r"(?i)\brecibiste\b[^\d]{0,30}(?:S\s*/\s*|PEN\s*)?(?P<amount>\d[\d.,]*)"
)
_LEMON_OPERATION_RE = re.compile(
    r"(?im)\b(?:id\s+de\s+la\s+operaci[oó]n|id\s+de\s+operaci[oó]n|c[oó]digo\s+de\s+operaci[oó]n|"
    r"operaci[oó]n\s*(?:id)?|operation\s*id)\b\s*[:#-]?\s*"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9._/-]{2,})"
)
_LEMON_EXTERNAL_ID_RE = re.compile(
    r"(?im)\b(?:id\s+externo|external\s*id)\b\s*[:#-]?\s*"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9._/-]{2,})"
)
_LEMON_DATETIME_RE = re.compile(
    r"(?im)\b(?:fecha(?:\s+y\s+hora)?|fecha\s+de\s+operaci[oó]n|hora|date|time)\b"
    r"[^\d]{0,20}(?P<value>\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?)?"
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{4}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)"
)


def _labeled_value(text: str, *labels: str) -> str | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    # Process one physical line at a time.  Using ``^\s*`` with ``re.search``
    # over the complete message lets ``\s*`` consume newlines and can cause
    # catastrophic backtracking when a label is absent from a long email.
    line_pattern = re.compile(
        rf"^[ \t]*[-•*]?[ \t]*(?:{label_pattern})[ \t]*[:#-][ \t]*(?P<value>[^\r\n]+)",
        re.IGNORECASE,
    )
    for line in text.splitlines():
        match = line_pattern.match(line)
        if match:
            value = re.sub(r"\s+", " ", match.group("value")).strip(" .;,\t")
            return value or None
    return None


def _clean_identifier(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip(" .;,\t\r\n")[:180] or None


def _channel_to_method(value: str | None) -> str | None:
    normalized = (value or "").casefold()
    if "yape" in normalized:
        return "yape"
    if "plin" in normalized:
        return "plin"
    if "transfer" in normalized or "deposito" in normalized or "depósito" in normalized:
        return "transfer"
    return None


def parse_lemon_dale_payment_email(raw_message: bytes) -> ParsedPaymentEmail | None:
    """Parse the observed Lemon/DALE deposit notification format.

    The sender is used only to select this parser.  The payment itself is
    accepted only when the body also supplies a recognizable amount and stable
    operation identifier; the subject is never sufficient by itself.
    """

    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    raw_sender = (message.get("From") or "").strip()
    sender_address = parseaddr(raw_sender)[1].casefold()
    if sender_address != _LEMON_SENDER:
        return None

    message_id = (message.get("Message-ID") or "").strip()
    if not message_id:
        message_id = f"sha256:{hashlib.sha256(raw_message).hexdigest()}"
    sender = raw_sender or None
    subject = (message.get("Subject") or "").strip() or None
    text = "\n".join(value for value in (subject or "", _text_content(message)) if value)

    amount_match = _LEMON_AMOUNT_RE.search(text)
    amount = _normalize_amount(amount_match.group("amount")) if amount_match else None
    operation_match = _LEMON_OPERATION_RE.search(text)
    external_id_match = _LEMON_EXTERNAL_ID_RE.search(text)
    operation_id = _clean_identifier(operation_match.group("value") if operation_match else None)
    external_id = _clean_identifier(external_id_match.group("value") if external_id_match else None)
    stable_operation_id = operation_id or external_id

    datetime_match = _LEMON_DATETIME_RE.search(text)
    body_datetime = (
        _parse_datetime(datetime_match.group("value"), default_timezone=_header_timezone(message))
        if datetime_match
        else None
    )
    occurred_at = body_datetime or _header_datetime(message)

    payer_name = _labeled_value(text, "nombre del pagador", "nombre", "pagador", "remitente", "origen")
    cci = _labeled_value(text, "cci")
    origin_entity = _labeled_value(text, "entidad de origen", "entidad", "banco")
    channel = _labeled_value(text, "mensaje", "canal", "medio")
    payment_method_hint = _channel_to_method(channel)
    metadata = {
        "parser": "lemon_dale",
        "provider": "lemon_dale",
        "parse_ready": bool(amount is not None and stable_operation_id),
        "occurred_at_source": "body_label" if body_datetime else "email_date_header",
    }
    if external_id:
        metadata["external_id"] = external_id
    if payer_name:
        metadata["payer_name"] = payer_name
    if cci:
        metadata["origin_cci"] = cci
    if origin_entity:
        metadata["origin_entity"] = origin_entity
    if channel:
        metadata["payment_channel"] = channel
    if payment_method_hint:
        metadata["payment_method_hint"] = payment_method_hint

    return ParsedPaymentEmail(
        source_message_id=message_id[:500],
        sender=sender[:320] if sender else None,
        subject=subject[:500] if subject else None,
        amount=amount,
        occurred_at=occurred_at,
        external_operation_id=stable_operation_id,
        receiver=None,
        parse_ready=bool(amount is not None and stable_operation_id),
        metadata=metadata,
        provider="lemon_dale",
        payment_method_hint=payment_method_hint,
    )


def parse_generic_payment_email(raw_message: bytes) -> ParsedPaymentEmail:
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    message_id = (message.get("Message-ID") or "").strip()
    if not message_id:
        message_id = f"sha256:{hashlib.sha256(raw_message).hexdigest()}"
    sender = (message.get("From") or "").strip() or None
    subject = (message.get("Subject") or "").strip() or None
    text = "\n".join(value for value in (subject or "", _text_content(message)) if value)
    amount_match = _AMOUNT_RE.search(text)
    amount = _normalize_amount(amount_match.group("amount")) if amount_match else None
    operation_id = None
    for operation_match in _OPERATION_RE.finditer(text):
        candidate = operation_match.group("value").strip()
        if re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", candidate):
            continue
        operation_id = candidate[:180]
        break
    datetime_match = _DATETIME_RE.search(text)
    occurred_at = _parse_datetime(datetime_match.group("value")) if datetime_match else _header_datetime(message)
    receiver_match = _RECEIVER_RE.search(text)
    receiver = receiver_match.group("value").strip(" .;,") if receiver_match else None
    explicit_operation_time = datetime_match is not None and _parse_datetime(datetime_match.group("value")) is not None
    metadata = {
        "parser": "generic",
        "provider": "generic",
        # The body date is useful evidence when present, but operation and
        # amount are the two mandatory identities for payment confirmation.
        # The email Date header remains a valid received/occurred fallback.
        "parse_ready": bool(amount is not None and operation_id),
        "occurred_at_source": "body_label" if explicit_operation_time else "email_date_fallback",
    }
    if receiver:
        metadata["receiver"] = receiver
    return ParsedPaymentEmail(
        source_message_id=message_id[:500],
        sender=sender[:320] if sender else None,
        subject=subject[:500] if subject else None,
        amount=amount,
        occurred_at=occurred_at,
        external_operation_id=operation_id,
        receiver=receiver,
        parse_ready=bool(amount is not None and operation_id),
        metadata=metadata,
        provider="generic",
    )


PAYMENT_EMAIL_PARSERS = (
    parse_lemon_dale_payment_email,
    parse_generic_payment_email,
)


def parse_payment_email(raw_message: bytes) -> ParsedPaymentEmail:
    """Run provider parsers in order and always retain generic fallback."""

    for parser in PAYMENT_EMAIL_PARSERS:
        parsed = parser(raw_message)
        if parsed is not None:
            return parsed
    raise ValueError("No payment email parser is configured")

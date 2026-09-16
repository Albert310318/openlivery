"""Files an HTTP tool returns, kept out of the LLM and sent as attachments.

An agent tool that returns a PDF, an image or any other file must not feed the
raw bytes back to the model: they burn tokens and the model cannot use them.
Instead the bytes are stripped from the tool result the model sees, stored as a
normal message attachment, and delivered through the conversation's channel with
the same senders human operators already use.

Detection is generic and declares no API: the tool result is inspected for a
binary body (by ``Content-Type``) or, for JSON, for a base64 field whose value
decodes to real file bytes. The value the model sees is replaced by a short note
naming the file, so it knows an attachment was sent without ever reading it.
"""

import base64
import binascii
import json
import mimetypes
import re
from dataclasses import dataclass
from pathlib import PurePath

from ..models import Agent, Conversation, Message, MessageAttachment, now_utc
from .attachments import MAX_ATTACHMENT_BYTES, attachment_kind, store_attachment

# A single tool response cannot flood the channel or the database.
MAX_TOOL_FILES = 5
MAX_TOOL_FILE_BYTES = MAX_ATTACHMENT_BYTES
# A base64 blob shorter than this is almost certainly an id or token, not a
# file, so it stays in the text the model reads.
MIN_FILE_BYTES = 1024

# JSON keys whose value may carry a base64-encoded file. Broad on purpose: the
# base64-decodes-to-real-bytes check below is what actually gates extraction, so
# a false key match on ordinary text simply fails to decode and is left alone.
_FILE_KEYS = {
    "file", "document", "data", "content", "base64", "b64", "bytes", "binary",
    "pdf", "image", "attachment", "file_base64", "filebase64", "file_content",
    "filecontent", "filedata", "file_data",
}
_FILENAME_KEYS = ("filename", "file_name", "name", "title")
_MIME_KEYS = ("mime", "mimetype", "mime_type", "content_type", "contenttype", "type")

_DATA_URL = re.compile(r"^data:([^;,]+);base64,(.+)$", re.DOTALL)
# A run of base64 characters, optionally padded. Whitespace is tolerated because
# some encoders wrap the output.
_BASE64 = re.compile(r"^[A-Za-z0-9+/\s]+={0,2}$")

# Content types that stay text: their body is handed to the model as before.
_TEXTUAL = ("text/", "application/json", "application/xml", "application/xhtml",
            "application/ld+json", "application/problem+json", "application/x-www-form-urlencoded")


@dataclass
class ToolFile:
    data: bytes
    mime: str
    filename: str | None


def _filename_for(mime: str, given: str | None) -> str:
    """A filename that carries the extension the mime implies.

    Tools often name a file without one (``cotizacion``); the extension is what
    the chat UI and WhatsApp use to classify the document, and Meta refuses an
    upload whose name does not match its type, so a missing one is added here
    rather than at every sender."""
    extension = mimetypes.guess_extension(mime.split(";")[0].strip()) or ".bin"
    given = (given or "").strip()
    if not given:
        return f"file{extension}"
    if not PurePath(given).suffix:
        return f"{given}{extension}"
    return given


def _decode_base64(value: str) -> bytes | None:
    """Decode a base64 string to bytes, or None when it is not real base64 or is
    too small to be a file."""
    cleaned = "".join(value.split())
    if len(cleaned) < 4 or not _BASE64.match(value):
        return None
    try:
        data = base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(data) < MIN_FILE_BYTES:
        return None
    return data


def _sibling(parent: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        for actual, value in parent.items():
            if actual.lower() == key and isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _harvest(node, files: list[ToolFile], parent: dict | None) -> object:
    """Walk a decoded JSON value, pulling out base64 files. Returns the node with
    each extracted value replaced by a short placeholder the model can read."""
    if len(files) >= MAX_TOOL_FILES:
        return node
    if isinstance(node, dict):
        return {key: _harvest_field(key, value, files, node) for key, value in node.items()}
    if isinstance(node, list):
        return [_harvest(item, files, parent) for item in node]
    return node


def _harvest_field(key: str, value, files: list[ToolFile], parent: dict):
    if isinstance(value, (dict, list)):
        return _harvest(value, files, parent)
    if not isinstance(value, str) or len(files) >= MAX_TOOL_FILES:
        return value
    mime = None
    payload = value
    data_url = _DATA_URL.match(value.strip())
    if data_url:
        mime, payload = data_url.group(1).strip() or None, data_url.group(2)
    elif key.lower() not in _FILE_KEYS:
        return value
    data = _decode_base64(payload)
    if data is None:
        return value
    if len(data) > MAX_TOOL_FILE_BYTES:
        return f"[file omitted: larger than {MAX_TOOL_FILE_BYTES // (1024 * 1024)} MB]"
    mime = mime or _sibling(parent, _MIME_KEYS) or "application/octet-stream"
    filename = _filename_for(mime, _sibling(parent, _FILENAME_KEYS))
    files.append(ToolFile(data=data, mime=mime.split(";")[0].strip(), filename=filename))
    return f"[file '{filename}' sent to the user as an attachment]"


def _is_textual(content_type: str) -> bool:
    content_type = content_type.split(";")[0].strip().lower()
    return not content_type or content_type.startswith(_TEXTUAL)


def _note(files: list[ToolFile]) -> str:
    names = ", ".join(f"{f.filename} ({f.mime}, {len(f.data)} bytes)" for f in files)
    plural = "files were" if len(files) > 1 else "file was"
    return (f" [OpenLivery: {len(files)} {plural} delivered to the user as an attachment on this channel: "
            f"{names}. The bytes were removed from this result. Confirm the file was sent; do not try to "
            f"read or reproduce its contents.]")


def extract_tool_files(status_code: int, content_type: str, content: bytes, text: str) -> tuple[str, list[ToolFile]]:
    """Split a tool response into (text the model sees, files to send).

    The returned text never contains file bytes. When nothing file-like is
    found, the text is the original body and the file list is empty.
    """
    files: list[ToolFile] = []
    ctype = (content_type or "").lower()
    if not _is_textual(ctype):
        if len(content) > MAX_TOOL_FILE_BYTES or not content:
            return text, []
        mime = ctype.split(";")[0].strip() or "application/octet-stream"
        files.append(ToolFile(data=content, mime=mime, filename=_filename_for(mime, None)))
        return _note(files).strip(), files
    if ctype.split(";")[0].strip() == "application/json" or (text.lstrip()[:1] in ("{", "[")):
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return text, []
        stripped = _harvest(parsed, files, None)
        if not files:
            return text, []
        return json.dumps(stripped, ensure_ascii=False) + _note(files), files
    return text, []


def _reply_file_marker(filename: str | None) -> str:
    """LLM-visible note stored on the message, in the customer's language like
    the other conversation markers."""
    return f"[El asistente envió un archivo: {filename}]" if filename else "[El asistente envió un archivo]"


def persist_reply_files(
    db, conversation: Conversation, agent: Agent, files: list[ToolFile]
) -> list[tuple[Message, MessageAttachment]]:
    """Store each tool-produced file as its own assistant message + attachment.

    One message per file keeps every attachment renderable in the inbox, the
    portal and the widget through the same serialization operator media uses, and
    lets the social outbox queue each on its own. The bytes are already out of the
    LLM context; the message carries only a short marker for future turns."""
    stored: list[tuple[Message, MessageAttachment]] = []
    for tool_file in files[:MAX_TOOL_FILES]:
        kind = attachment_kind(tool_file.mime)
        message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="",
            sender_type="ai",
            sender_name=agent.name,
        )
        message.llm_content = _reply_file_marker(tool_file.filename)
        db.add(message)
        db.flush()
        attachment = store_attachment(db, message, data=tool_file.data, mime=tool_file.mime, filename=tool_file.filename, kind=kind)
        conversation.updated_at = now_utc()
        stored.append((message, attachment))
    return stored

import base64

import httpx
from fastapi import HTTPException

from .ai import Completion, _safe_provider_error, auth_headers, chat_url, completion_from, extract_chat_text, read_usage


# Transcription providers sniff the container from the file name, so a browser
# recording sent as "audio.ogg" is rejected as corrupted. Map the mime to the
# extension it actually is; ogg stays the default for WhatsApp voice notes.
_AUDIO_EXTENSIONS = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/aac": "aac",
    "audio/webm": "webm",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/flac": "flac",
    "video/mp4": "mp4",
    "video/webm": "webm",
}


def audio_filename(mime: str | None) -> str:
    """A file name whose extension matches the audio's mime type."""
    base = (mime or "").split(";")[0].strip().lower()
    return f"audio.{_AUDIO_EXTENSIONS.get(base, 'ogg')}"


async def transcribe_audio(
    base_url: str,
    api_key: str,
    model: str,
    audio: bytes,
    filename: str = "audio.ogg",
    content_type: str = "audio/ogg",
) -> Completion:
    """Transcribe an audio clip via an OpenAI-compatible /audio/transcriptions
    endpoint. The transcript is ``.text``; the rest is what the call used, so
    the caller can record it like any other reply."""
    url = f"{base_url.rstrip('/')}/audio/transcriptions"
    files = {"file": (filename, audio, content_type), "model": (None, model)}
    headers = {key: value for key, value in auth_headers(api_key).items() if key != "Content-Type"}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, headers=headers, files=files)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach the transcription provider.") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Transcription failed: {_safe_provider_error(response)}")
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Invalid transcription response.") from exc
    return completion_from((data.get("text") or "").strip(), read_usage(data), data)


async def describe_image(
    base_url: str,
    api_key: str,
    model: str,
    image: bytes,
    content_type: str,
    instruction: str,
) -> Completion:
    """Describe an image with a vision model via chat completions. The
    description is ``.text``; the rest is what the call used."""
    data_url = f"data:{content_type};base64,{base64.b64encode(image).decode()}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(chat_url(base_url), headers=auth_headers(api_key), json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach the vision provider.") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Image analysis failed: {_safe_provider_error(response)}")
    try:
        data = response.json()
        return completion_from(extract_chat_text(data), read_usage(data), data)
    except (ValueError, KeyError, IndexError) as exc:
        raise HTTPException(status_code=502, detail="Invalid image analysis response.") from exc

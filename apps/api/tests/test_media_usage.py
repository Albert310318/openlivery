"""Transcription usage: the audio endpoint answers with tokens but no vendor
charge, so the call's cost and the vendor that served it are read from the
router's generation record, which appears a moment later."""

import asyncio

import pytest

from app.services import media


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    """Answers the transcription, then the generation record after ``misses`` 404s."""

    def __init__(self, generation: dict | None, misses: int = 1):
        self.generation = generation
        self.misses = misses
        self.gets = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers, files):
        assert url.endswith("/audio/transcriptions") and files["model"] == (None, "openai/gpt-4o-mini-transcribe")
        return _Response(200, {"id": "gen-stt-1", "text": " quiero reservar ", "usage": {"input_tokens": 105, "output_tokens": 30, "cost": 0}})

    async def get(self, url, headers, params):
        assert url.endswith("/generation") and params == {"id": "gen-stt-1"}
        self.gets += 1
        if self.gets <= self.misses or self.generation is None:
            return _Response(404, {"error": {"message": "not found"}})
        return _Response(200, {"data": self.generation})


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    async def sleep(_seconds):
        return None

    monkeypatch.setattr(media.asyncio, "sleep", sleep)


def test_transcription_cost_and_vendor_come_from_the_generation_record(monkeypatch):
    client = _Client({"total_cost": 0, "upstream_inference_cost": 0.000281, "provider_name": "OpenAI", "is_byok": True})
    monkeypatch.setattr(media.httpx, "AsyncClient", lambda timeout: client)
    result = asyncio.run(media.transcribe_audio("https://openrouter.ai/api/v1", "key", "openai/gpt-4o-mini-transcribe", b"ogg"))
    assert result.text == "quiero reservar"
    assert (result.input_tokens, result.output_tokens) == (105, 30)
    assert result.cost_usd == 0.000281 and result.served_by == "OpenAI"
    assert client.gets == 2  # one miss, then the record


def test_transcription_stays_unpriced_when_the_record_never_appears(monkeypatch):
    client = _Client(None)
    monkeypatch.setattr(media.httpx, "AsyncClient", lambda timeout: client)
    result = asyncio.run(media.transcribe_audio("https://openrouter.ai/api/v1", "key", "openai/gpt-4o-mini-transcribe", b"ogg"))
    assert result.cost_usd is None and result.served_by == ""
    assert client.gets == 1 + len(media._GENERATION_RETRY_DELAYS)

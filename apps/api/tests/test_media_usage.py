"""Transcription usage: the audio endpoint answers with tokens but neither
the vendor's charge nor the vendor. The router names the call in a response
header, and its record (with both) exists some seconds later, so the usage
row is written at once and reconciled in the background."""

import asyncio
import uuid

import pytest

from conftest import TestingSession

from app.models import Agency, UsageRecord
from app.services import media, usage


class _Response:
    def __init__(self, status_code: int, payload: dict, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class _TranscriptionClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers, files):
        assert url.endswith("/audio/transcriptions") and files["model"] == (None, "openai/gpt-4o-mini-transcribe")
        return _Response(
            200,
            {"text": " quiero reservar ", "usage": {"input_tokens": 105, "output_tokens": 30, "cost": 0}},
            {"x-generation-id": "gen-stt-1", "x-provider-name": ""},
        )


def test_transcription_carries_tokens_and_the_generation_id_but_no_cost(monkeypatch):
    monkeypatch.setattr(media.httpx, "AsyncClient", lambda timeout: _TranscriptionClient())
    result = asyncio.run(media.transcribe_audio("https://openrouter.ai/api/v1", "key", "openai/gpt-4o-mini-transcribe", b"ogg"))
    assert result.text == "quiero reservar"
    assert (result.input_tokens, result.output_tokens) == (105, 30)
    # A zero router charge with no vendor charge is unknown, not free.
    assert result.cost_usd is None and result.served_by == "" and result.generation_id == "gen-stt-1"


class _GenerationClient:
    """404 until ``ready`` reads have happened, then the record."""

    calls = 0

    def __init__(self, ready: int, record: dict):
        self.ready = ready
        self.record = record

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers, params):
        assert url.endswith("/generation") and params == {"id": "gen-stt-1"}
        _GenerationClient.calls += 1
        if _GenerationClient.calls < self.ready:
            return _Response(404, {"error": {"message": "not found"}})
        return _Response(200, {"data": self.record})


@pytest.fixture
def unpriced_record(monkeypatch):
    # The background reconcile opens its own session; point it at the test database.
    monkeypatch.setattr(usage, "new_session", TestingSession)
    with TestingSession() as db:
        agency = Agency(name="Agencia", slug="agencia")
        db.add(agency)
        db.flush()
        record = UsageRecord(id=uuid.uuid4(), agency_id=agency.id, provider="openrouter", model="openai/gpt-4o-mini-transcribe",
                             input_tokens=105, output_tokens=30, cost_usd=None)
        db.add(record)
        db.commit()
        return record.id


def test_reconcile_fills_cost_and_vendor_from_the_generation_record(monkeypatch, unpriced_record):
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(usage.asyncio, "sleep", sleep)
    _GenerationClient.calls = 0
    client = _GenerationClient(2, {"total_cost": 0, "upstream_inference_cost": 0.000231, "provider_name": "OpenAI", "is_byok": True})
    monkeypatch.setattr(usage.httpx, "AsyncClient", lambda timeout: client)
    asyncio.run(usage.reconcile_generation(unpriced_record, "https://openrouter.ai/api/v1", "key", "gen-stt-1"))
    assert slept == list(usage.RECONCILE_DELAYS[:2])  # one miss, then the record
    with TestingSession() as db:
        record = db.get(UsageRecord, unpriced_record)
        assert float(record.cost_usd) == 0.000231 and record.served_by == "OpenAI"


def test_reconcile_gives_up_and_leaves_the_row_unpriced(monkeypatch, unpriced_record):
    async def sleep(_seconds):
        return None

    monkeypatch.setattr(usage.asyncio, "sleep", sleep)
    _GenerationClient.calls = 0
    monkeypatch.setattr(usage.httpx, "AsyncClient", lambda timeout: _GenerationClient(99, {}))
    asyncio.run(usage.reconcile_generation(unpriced_record, "https://openrouter.ai/api/v1", "key", "gen-stt-1"))
    assert _GenerationClient.calls == len(usage.RECONCILE_DELAYS)
    with TestingSession() as db:
        assert db.get(UsageRecord, unpriced_record).cost_usd is None


def test_schedule_is_a_no_op_without_a_loop_or_with_a_priced_completion():
    from app.services.ai import Completion

    record = UsageRecord(id=uuid.uuid4())
    usage.schedule_generation_reconcile(record, Completion(text="x", input_tokens=1, generation_id="gen-1"), "u", "k")  # no loop
    usage.schedule_generation_reconcile(record, Completion(text="x", input_tokens=1, cost_usd=0.1, generation_id="gen-1"), "u", "k")
    usage.schedule_generation_reconcile(None, Completion(text="x", input_tokens=1, generation_id="gen-1"), "u", "k")
    assert not usage._reconciling

"""Embeddings for semantic retrieval over an agent's knowledge base.

Uses the agency's provider key (`{base_url}/embeddings`). Any failure (no key,
network, bad response) returns None so the caller can fall back to keyword
search — embeddings never block a document upload or a reply.
"""

import math

import httpx

from .ai import auth_headers
from .model_catalog import DEFAULT_EMBEDDING_MODEL

__all__ = ["DEFAULT_EMBEDDING_MODEL", "embed_texts", "embed_query", "cosine_similarity"]


async def embed_texts(base_url: str, api_key: str, texts: list[str], model: str = DEFAULT_EMBEDDING_MODEL) -> list[list[float]] | None:
    if not texts:
        return []
    url = f"{base_url.rstrip('/')}/embeddings"
    headers = auth_headers(api_key)
    payload = {"model": model or DEFAULT_EMBEDDING_MODEL, "input": texts}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            return None
        rows = response.json().get("data", [])
        if len(rows) != len(texts):
            return None
        ordered = sorted(rows, key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") for item in ordered]
        if any(not isinstance(vec, list) or not vec for vec in vectors):
            return None
        return vectors
    except (httpx.HTTPError, ValueError, KeyError, AttributeError):
        return None


async def embed_query(base_url: str, api_key: str, query: str, model: str = DEFAULT_EMBEDDING_MODEL) -> list[float] | None:
    result = await embed_texts(base_url, api_key, [query], model)
    return result[0] if result else None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

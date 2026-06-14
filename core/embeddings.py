"""Google Gemini embeddings (free tier).

Only used by the worker; the webhook never embeds, so `google-genai` is lazily
imported and lives only in the worker's requirements.txt.

Source posts are embedded as RETRIEVAL_DOCUMENT and match profiles as
RETRIEVAL_QUERY so Gemini optimizes each side of the asymmetric search.
"""
from __future__ import annotations

from functools import lru_cache

from core import config


@lru_cache(maxsize=1)
def _client():
    from google import genai

    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    return genai.Client(api_key=config.GEMINI_API_KEY)


# Gemini caps the number of contents per embed request; chunk to stay under it.
_BATCH = 100


def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    from google.genai import types

    if not texts:
        return []
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _BATCH):
        batch = texts[start:start + _BATCH]
        result = _client().models.embed_content(
            model=config.EMBEDDING_MODEL,
            contents=batch,
            config=types.EmbedContentConfig(task_type=task_type),
        )
        vectors.extend(list(e.values) for e in result.embeddings)
    return vectors


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed source post contents for storage."""
    return _embed(texts, "RETRIEVAL_DOCUMENT")


def embed_document(text: str) -> list[float]:
    return embed_documents([text])[0]


def embed_query(text: str) -> list[float]:
    """Embed a match profile for semantic search."""
    return _embed([text], "RETRIEVAL_QUERY")[0]

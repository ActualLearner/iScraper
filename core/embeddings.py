"""Google Gemini embeddings (free tier).

Only used by the worker; the webhook never embeds, so `google-genai` is lazily
imported and lives only in the worker's requirements.txt.

The default model is `gemini-embedding-2`, which supports 768-dimensional output
and requires retrieval intent to be expressed as text prefixes rather than the
older `task_type` field. `gemini-embedding-001` remains supported for overrides.
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


# Gemini Embedding 001 supports multiple inputs per request; Embedding 2 returns
# one aggregate embedding for multiple inputs, so it must be called per item.
_BATCH = 100


def _is_embedding_2() -> bool:
    return config.EMBEDDING_MODEL == "gemini-embedding-2"


def _document_text(text: str) -> str:
    if _is_embedding_2():
        return f"title: none | text: {text}"
    return text


def _query_text(text: str) -> str:
    if _is_embedding_2():
        return f"task: search result | query: {text}"
    return text


def _config(task_type: str | None = None):
    from google.genai import types

    kwargs = {"output_dimensionality": config.EMBEDDING_DIM}
    if task_type and not _is_embedding_2():
        kwargs["task_type"] = task_type
    return types.EmbedContentConfig(**kwargs)


def _single(text: str, task_type: str | None = None) -> list[float]:
    result = _client().models.embed_content(
        model=config.EMBEDDING_MODEL,
        contents=text,
        config=_config(task_type),
    )
    return list(result.embeddings[0].values)


def _many_embedding_001(texts: list[str], task_type: str) -> list[list[float]]:
    if not texts:
        return []
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _BATCH):
        batch = texts[start:start + _BATCH]
        result = _client().models.embed_content(
            model=config.EMBEDDING_MODEL,
            contents=batch,
            config=_config(task_type),
        )
        vectors.extend(list(e.values) for e in result.embeddings)
    return vectors


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed source post contents for storage."""
    docs = [_document_text(t) for t in texts]
    if _is_embedding_2():
        return [_single(t) for t in docs]
    return _many_embedding_001(docs, "RETRIEVAL_DOCUMENT")


def embed_document(text: str) -> list[float]:
    return embed_documents([text])[0]


def embed_query(text: str) -> list[float]:
    """Embed a match profile for semantic search."""
    query = _query_text(text)
    if _is_embedding_2():
        return _single(query)
    return _many_embedding_001([query], "RETRIEVAL_QUERY")[0]

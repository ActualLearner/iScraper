"""Local text embeddings via fastembed (ONNX, CPU).

Only the worker embeds; the webhook never does. fastembed + onnxruntime are heavy
imports, so they live only in requirements-worker.txt and are imported lazily.

The model is selected by `config.EMBEDDING_MODEL` and runs in-process on CPU, so it
holds RAM for the life of the worker. Switching models is a config change: set
`EMBEDDING_MODEL`, `EMBEDDING_DIM`, and the prefix settings in core/config.py (and
change the `vector(...)` column + match function in scripts/init_db.sql if the new
model's native dimension differs).

The default model, BAAI/bge-small-en-v1.5, is 384-dim (light on a small worker
dyno) and wants a query prefix but no document prefix. fastembed's `.embed()` does
not add prefixes itself, so we prepend `EMBEDDING_QUERY_PREFIX` /
`EMBEDDING_DOCUMENT_PREFIX` here. `_truncate` additionally supports Matryoshka
models (e.g. nomic-embed-text) where a larger native vector is sliced to a smaller
`EMBEDDING_DIM` and re-normalized; it is a no-op when `EMBEDDING_DIM` already
matches the model's native dimension.
"""
from __future__ import annotations

from functools import lru_cache

from core import config


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding

    kwargs: dict = {"model_name": config.EMBEDDING_MODEL}
    if config.EMBEDDING_CACHE_DIR:
        kwargs["cache_dir"] = config.EMBEDDING_CACHE_DIR
    if config.EMBEDDING_THREADS > 0:
        kwargs["threads"] = config.EMBEDDING_THREADS
    return TextEmbedding(**kwargs)


def _truncate(vector: list[float]) -> list[float]:
    """Matryoshka truncation: slice to EMBEDDING_DIM and L2-renormalize.

    A no-op when EMBEDDING_DIM matches (or exceeds) the model's native dimension.
    """
    dim = config.EMBEDDING_DIM
    if dim <= 0 or dim >= len(vector):
        return vector
    sliced = vector[:dim]
    norm = sum(v * v for v in sliced) ** 0.5
    if norm == 0:
        return sliced
    return [v / norm for v in sliced]


def _embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    raw = _model().embed(texts, batch_size=config.EMBEDDING_BATCH)
    return [_truncate(vec.tolist()) for vec in raw]


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed source-post contents for storage."""
    prefixed = [config.EMBEDDING_DOCUMENT_PREFIX + (t or "") for t in texts]
    return _embed(prefixed)


def embed_document(text: str) -> list[float]:
    return embed_documents([text])[0]


def embed_query(text: str) -> list[float]:
    """Embed a match profile for semantic search."""
    return _embed([config.EMBEDDING_QUERY_PREFIX + (text or "")])[0]

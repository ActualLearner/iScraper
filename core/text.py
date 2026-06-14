"""Text helpers: content normalization and word counting.

Normalized content is used both as the text that gets embedded and as the basis
for detecting whether a stored source post's content changed (see architecture.md).
"""
from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")


def normalize_content(text: str) -> str:
    """Normalize message content for storage comparison and embedding.

    Collapses all runs of whitespace to single spaces, strips ends, and
    lowercases. Two messages with the same visible text but different incidental
    whitespace/case normalize to the same value, so unchanged posts are cheaply
    skipped on re-scrape.
    """
    if not text:
        return ""
    return _WHITESPACE.sub(" ", text).strip().lower()


def word_count(text: str) -> int:
    if not text:
        return 0
    return len(text.split())

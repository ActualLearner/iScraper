"""Offline OCR helpers for image-based Telegram posts.

The worker uses Tesseract through pytesseract. Imports stay lazy so the webhook
surface never needs OCR dependencies.
"""
from __future__ import annotations

import re

from core import config

_WHITESPACE = re.compile(r"[ \t\r\f\v]+")


def clean_ocr_text(text: str) -> str:
    """Make raw OCR output stable enough for storage comparison and embedding."""
    if not text:
        return ""

    lines: list[str] = []
    for raw in text.replace("\x0c", "\n").splitlines():
        line = _WHITESPACE.sub(" ", raw).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def image_to_text(path: str) -> str:
    """Run OCR for one image path and return cleaned text.

    Raises RuntimeError when the OCR stack is unavailable or the image times out;
    callers should catch that and continue with the rest of the post.
    """
    try:
        import pytesseract
    except Exception as exc:  # pragma: no cover - depends on worker deps
        raise RuntimeError("pytesseract is not installed") from exc

    try:
        raw = pytesseract.image_to_string(
            path,
            lang=config.OCR_LANGS,
            config=config.OCR_TESSERACT_CONFIG,
            timeout=config.OCR_TIMEOUT_SECONDS,
        )
    except RuntimeError:
        raise
    except Exception as exc:  # pragma: no cover - external binary failures
        raise RuntimeError(f"tesseract OCR failed: {exc}") from exc

    return clean_ocr_text(raw)

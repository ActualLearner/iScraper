"""Offline OCR helpers for image-based Telegram posts.

The worker uses Tesseract through pytesseract. Imports stay lazy so the webhook
surface never needs OCR dependencies.
"""
from __future__ import annotations

import os
import re
import shutil
from functools import lru_cache
from pathlib import Path

from core import config

_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BYTES_PER_MB = 1024 * 1024


class OcrUnavailable(RuntimeError):
    """The OCR stack is not installed or cannot be executed."""


class OcrSkipped(RuntimeError):
    """The image is intentionally skipped by OCR safety limits."""


def _positive_int(value: int, fallback: int) -> int:
    return value if value > 0 else fallback


def _limit_tesseract_threads() -> None:
    limit = str(_positive_int(config.OCR_THREAD_LIMIT, 1))
    os.environ["OMP_THREAD_LIMIT"] = limit
    os.environ["OMP_NUM_THREADS"] = limit


def clean_ocr_text(text: str) -> str:
    """Make raw OCR output stable enough for storage comparison and embedding."""
    if not text:
        return ""

    lines: list[str] = []
    for raw in text.replace("\x0c", "\n").splitlines():
        line = _WHITESPACE.sub(" ", raw).strip()
        if line:
            lines.append(line)
    cleaned = "\n".join(lines).strip()
    max_chars = config.OCR_MAX_TEXT_CHARS
    if max_chars > 0 and len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip()
    return cleaned


@lru_cache(maxsize=1)
def availability_error() -> str | None:
    """Return a human-readable OCR setup problem, or None when OCR can run."""
    if not config.OCR_ENABLED:
        return "OCR is disabled"

    if shutil.which("tesseract") is None:
        return "tesseract binary is not installed or not on PATH"

    try:
        import pytesseract
    except Exception as exc:  # pragma: no cover - depends on worker deps
        return f"pytesseract is not installed: {exc}"

    _limit_tesseract_threads()
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:  # pragma: no cover - external binary failures
        return f"tesseract is not executable: {exc}"
    return None


def is_available() -> bool:
    return availability_error() is None


def _check_file_size(path: Path) -> None:
    max_mb = config.OCR_MAX_IMAGE_MB
    if max_mb <= 0:
        return
    max_bytes = int(max_mb * _BYTES_PER_MB)
    size = path.stat().st_size
    if size > max_bytes:
        actual = size / _BYTES_PER_MB
        raise OcrSkipped(f"image file is {actual:.2f} MB; limit is {max_mb:.2f} MB")


def _prepare_image(path: Path):
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except Exception as exc:  # pragma: no cover - depends on worker deps
        raise OcrUnavailable("Pillow is not installed") from exc

    try:
        with Image.open(path) as image:
            width, height = image.size
            pixels = width * height
            if pixels <= 0:
                raise OcrSkipped("image has invalid dimensions")
            max_pixels = config.OCR_MAX_IMAGE_PIXELS
            if max_pixels > 0 and pixels > max_pixels:
                raise OcrSkipped(
                    f"image has {pixels} pixels; limit is {max_pixels} pixels"
                )

            image = ImageOps.exif_transpose(image)
            max_dimension = config.OCR_MAX_IMAGE_DIMENSION
            if max_dimension > 0 and max(width, height) > max_dimension:
                image.thumbnail((max_dimension, max_dimension))

            return image.convert("L")
    except UnidentifiedImageError as exc:
        raise OcrSkipped("downloaded media is not a readable image") from exc


def image_to_text(path: str) -> str:
    """Run OCR for one image path and return cleaned text.

    Raises OcrUnavailable when the OCR stack cannot run, OcrSkipped when the
    image exceeds configured safety limits, and RuntimeError for Tesseract
    execution failures. Callers should catch those and continue with the post.
    """
    image_path = Path(path)
    _check_file_size(image_path)

    setup_error = availability_error()
    if setup_error:
        raise OcrUnavailable(setup_error)

    try:
        import pytesseract
    except Exception as exc:  # pragma: no cover - guarded by availability check
        raise OcrUnavailable("pytesseract is not installed") from exc

    image = _prepare_image(image_path)
    _limit_tesseract_threads()
    try:
        raw = pytesseract.image_to_string(
            image,
            lang=config.OCR_LANGS,
            config=config.OCR_TESSERACT_CONFIG,
            timeout=config.OCR_TIMEOUT_SECONDS,
        )
    except RuntimeError:
        raise
    except Exception as exc:  # pragma: no cover - external binary failures
        raise RuntimeError(f"tesseract OCR failed: {exc}") from exc

    return clean_ocr_text(raw)

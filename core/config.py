"""Central configuration: environment variables and backend-tunable constants.

Tunables that the product spec calls out as "easily changed backend configuration"
live here and can be overridden via environment variables.
"""
from __future__ import annotations

import os

try:  # local development convenience; absent/no-op in production
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


def _get(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _int(name: str, default: int) -> int:
    raw = _get(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = _get(name)
    try:
        return float(raw) if raw is not None else default
    except ValueError:
        return default


# --- Secrets / connection ---
BOT_TOKEN = _get("BOT_TOKEN")
WEBHOOK_SECRET = _get("WEBHOOK_SECRET")

TELEGRAM_API_ID = _int("TELEGRAM_API_ID", 0)
TELEGRAM_API_HASH = _get("TELEGRAM_API_HASH")
TELETHON_SESSION = _get("TELETHON_SESSION")

SUPABASE_URL = _get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = _get("SUPABASE_SERVICE_KEY")

GEMINI_API_KEY = _get("GEMINI_API_KEY")

# --- Embeddings ---
EMBEDDING_MODEL = _get("EMBEDDING_MODEL", "gemini-embedding-2")
EMBEDDING_DIM = _int("EMBEDDING_DIM", 768)
# Gemini's active RPM/RPD limits vary by project and are shown in AI Studio.
# Keep worker calls paced and checkpointed so large Past Searches finish across
# multiple runs instead of bursting hundreds of embedding requests at once.
EMBEDDING_REQUESTS_PER_MINUTE = _int("EMBEDDING_REQUESTS_PER_MINUTE", 15)
EMBEDDING_MAX_PER_RUN = _int("EMBEDDING_MAX_PER_RUN", 75)
EMBEDDING_QUOTA_RETRY_MINUTES = _int("EMBEDDING_QUOTA_RETRY_MINUTES", 60)

# --- Matching ---
# Cosine similarity in [0, 1]; tune for the embedding model in use. The default
# is intentionally stricter because v1 uses threshold-only relevance.
SIMILARITY_THRESHOLD = _float("SIMILARITY_THRESHOLD", 0.70)

# --- OCR ---
OCR_LANGS = _get("OCR_LANGS", "eng") or "eng"
OCR_TIMEOUT_SECONDS = _float("OCR_TIMEOUT_SECONDS", 8.0)
OCR_TESSERACT_CONFIG = _get("OCR_TESSERACT_CONFIG", "--psm 6") or ""

# --- Limits / defaults (from product-spec.md) ---
BETA_MAX_USERS = _int("BETA_MAX_USERS", 5)
MAX_MATCH_PROFILE_WORDS = _int("MAX_MATCH_PROFILE_WORDS", 35)
MAX_SOURCE_CHANNELS = _int("MAX_SOURCE_CHANNELS", 30)

# 0 means scrape until the lookback boundary. Set only as an emergency brake for
# very large sources if a worker starts exceeding its timeout/budget.
SCRAPE_MAX_MESSAGES = _int("SCRAPE_MAX_MESSAGES", 0)
SCRAPE_PROGRESS_EVERY = _int("SCRAPE_PROGRESS_EVERY", 250)
JOB_STALE_MINUTES = _int("JOB_STALE_MINUTES", 20)

DEFAULT_LOOKBACK_DAYS = _int("DEFAULT_LOOKBACK_DAYS", 15)
MAX_LOOKBACK_DAYS = _int("MAX_LOOKBACK_DAYS", 90)

MIN_INTERVAL_DAYS = _int("MIN_INTERVAL_DAYS", 1)
MAX_INTERVAL_DAYS = _int("MAX_INTERVAL_DAYS", 30)

# Minimum near-live interval. The GitHub Actions worker runs on a ~5-minute
# cadence, so intervals shorter than this cannot be honored.
MIN_NEAR_LIVE_MINUTES = _int("MIN_NEAR_LIVE_MINUTES", 5)
MAX_NEAR_LIVE_MINUTES = _int("MAX_NEAR_LIVE_MINUTES", 1440)

DEFAULT_TIMEZONE = _get("DEFAULT_TIMEZONE", "Africa/Addis_Ababa")

# How far back near-live scraping looks per channel as a safety net when a user
# has no recorded last-check time yet (e.g. just enabled). Kept small so near-live
# never floods history.
NEAR_LIVE_SCRAPE_WINDOW_MINUTES = _int("NEAR_LIVE_SCRAPE_WINDOW_MINUTES", 180)

# Telegram message hard limit; longer match lists are split across messages.
TELEGRAM_MAX_MESSAGE_CHARS = 4096


def require(*names: str) -> None:
    """Raise if any named module-level setting is missing. Use at process start."""
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise RuntimeError(
            "Missing required configuration: " + ", ".join(missing)
        )

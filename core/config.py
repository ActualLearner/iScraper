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


def _bool(name: str, default: bool) -> bool:
    raw = _get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- Secrets / connection ---
BOT_TOKEN = _get("BOT_TOKEN")
WEBHOOK_SECRET = _get("WEBHOOK_SECRET")

TELEGRAM_API_ID = _int("TELEGRAM_API_ID", 0)
TELEGRAM_API_HASH = _get("TELEGRAM_API_HASH")
TELETHON_SESSION = _get("TELETHON_SESSION")

SUPABASE_URL = _get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = _get("SUPABASE_SERVICE_KEY")
SUPABASE_DB_RETRIES = _int("SUPABASE_DB_RETRIES", 3)
SUPABASE_DB_RETRY_BACKOFF_SECONDS = _float("SUPABASE_DB_RETRY_BACKOFF_SECONDS", 1.0)
SUPABASE_DB_RETRY_JOB_MINUTES = _int("SUPABASE_DB_RETRY_JOB_MINUTES", 2)

# --- Embeddings (local, via fastembed / ONNX on CPU) ---
# The worker is the only process that embeds; the model runs in-process and holds
# RAM for the worker's lifetime, so keep the model + batch small enough to stay
# under the dyno's memory cap. Swapping models is a config change here (plus a
# vector(...) column change in scripts/init_db.sql if the native dim differs).
EMBEDDING_MODEL = _get("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
# Dimension actually stored. Defaults to nomic's native 768. nomic supports
# Matryoshka truncation (512/256/128) for smaller vectors; to use it, set this
# lower AND change the vector(...) column + match function to match.
EMBEDDING_DIM = _int("EMBEDDING_DIM", 768)
# Task instruction prefixes prepended before embedding. nomic REQUIRES these.
# For a model that needs none (e.g. all-MiniLM-L6-v2), set both to "" in code.
# For bge-small-en-v1.5: query prefix
# "Represent this sentence for searching relevant passages: ", document "".
EMBEDDING_QUERY_PREFIX = _get("EMBEDDING_QUERY_PREFIX", "search_query: ") or ""
EMBEDDING_DOCUMENT_PREFIX = _get("EMBEDDING_DOCUMENT_PREFIX", "search_document: ") or ""
# Texts per fastembed batch — the main lever on peak RAM during backfill.
EMBEDDING_BATCH = _int("EMBEDDING_BATCH", 16)
# Unembedded posts pulled from the DB per page while backfilling. Bounds row
# memory so the backlog size never drives memory; the loop pages until drained.
EMBEDDING_DB_PAGE = _int("EMBEDDING_DB_PAGE", 200)
# Short cool-off before a Past Search resumes a backlog left unfinished (e.g. if a
# worker run was killed mid-way). No longer tied to any provider rate limit.
EMBEDDING_BACKLOG_RETRY_MINUTES = _int("EMBEDDING_BACKLOG_RETRY_MINUTES", 5)
# Where fastembed stores the downloaded model. Set in the Docker image so the model
# is baked at build time and not re-downloaded onto the dyno's ephemeral disk.
EMBEDDING_CACHE_DIR = _get("EMBEDDING_CACHE_DIR")
# Cap onnxruntime CPU threads (lower = less peak RAM/CPU on a small dyno). Unset
# lets fastembed/onnxruntime decide.
EMBEDDING_THREADS = _int("EMBEDDING_THREADS", 0)

# --- Matching ---
# Cosine similarity in [0, 1]; MUST be tuned for the embedding model in use since
# v1 uses threshold-only relevance. This default is a starting point for
# nomic-embed-text-v1.5 (whose relevant-pair similarities run lower than Gemini's);
# measure against real matches and adjust before relying on it.
SIMILARITY_THRESHOLD = _float("SIMILARITY_THRESHOLD", 0.55)

# --- OCR ---
OCR_ENABLED = _bool("OCR_ENABLED", True)
OCR_LANGS = _get("OCR_LANGS", "eng") or "eng"
OCR_TIMEOUT_SECONDS = _float("OCR_TIMEOUT_SECONDS", 10.0)
OCR_DOWNLOAD_TIMEOUT_SECONDS = _float("OCR_DOWNLOAD_TIMEOUT_SECONDS", 30.0)
OCR_TESSERACT_CONFIG = _get("OCR_TESSERACT_CONFIG", "--psm 6") or ""
OCR_MAX_IMAGES_PER_POST = _int("OCR_MAX_IMAGES_PER_POST", 3)
OCR_MAX_IMAGE_MB = _float("OCR_MAX_IMAGE_MB", 5.0)
OCR_MAX_IMAGE_PIXELS = _int("OCR_MAX_IMAGE_PIXELS", 6_000_000)
OCR_MAX_IMAGE_DIMENSION = _int("OCR_MAX_IMAGE_DIMENSION", 1600)
OCR_MAX_TEXT_CHARS = _int("OCR_MAX_TEXT_CHARS", 6000)
OCR_SKIP_WHEN_CAPTION_PRESENT = _bool("OCR_SKIP_WHEN_CAPTION_PRESENT", False)
OCR_THREAD_LIMIT = _int("OCR_THREAD_LIMIT", 1)

# --- Worker runtime ---
PAST_SEARCH_JOBS_PER_RUN = _int("PAST_SEARCH_JOBS_PER_RUN", 1)
WORKER_RUN_TIMEOUT_SECONDS = _float("WORKER_RUN_TIMEOUT_SECONDS", 0.0)
WORKER_STAGE_TIMEOUT_SECONDS = _float("WORKER_STAGE_TIMEOUT_SECONDS", 0.0)
WORKER_LOOP_INTERVAL_SECONDS = _float("WORKER_LOOP_INTERVAL_SECONDS", 300.0)

# --- Limits / defaults (from product-spec.md) ---
BETA_MAX_USERS = _int("BETA_MAX_USERS", 5)
MAX_MATCH_PROFILE_WORDS = _int("MAX_MATCH_PROFILE_WORDS", 500)
MAX_SOURCE_CHANNELS = _int("MAX_SOURCE_CHANNELS", 30)

# 0 means scrape until the lookback boundary. Set only as an emergency brake for
# very large sources if a worker starts exceeding its timeout/budget.
SCRAPE_MAX_MESSAGES = _int("SCRAPE_MAX_MESSAGES", 0)
SCRAPE_PROGRESS_EVERY = _int("SCRAPE_PROGRESS_EVERY", 250)
SCRAPE_GROUP_BATCH_SIZE = _int("SCRAPE_GROUP_BATCH_SIZE", 50)
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

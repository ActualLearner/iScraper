"""Supabase data-access layer. The single source of truth for all surfaces.

Everything is keyed by Telegram user id. The Supabase service-role key is used,
so this module must only ever run server-side (webhook + worker), never client.
"""
from __future__ import annotations

import time
from datetime import timedelta
from functools import lru_cache
from typing import Any, Callable, TypeVar

from core import config, logs, timeutil

T = TypeVar("T")


@lru_cache(maxsize=1)
def client():
    from supabase import create_client

    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY are not configured")
    return create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)


def _rows(resp) -> list[dict]:
    return resp.data or []


def _one(resp) -> dict | None:
    rows = _rows(resp)
    return rows[0] if rows else None


def is_transient_error(exc: Exception) -> bool:
    """True for temporary HTTP transport failures from the Supabase client."""
    try:
        import httpx
    except Exception:  # pragma: no cover - httpx is a runtime dependency
        return False
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


def _reset_client() -> None:
    try:
        client.cache_clear()
    except Exception:
        pass


def _execute(operation: Callable[[], T], *, label: str) -> T:
    attempts = max(config.SUPABASE_DB_RETRIES, 1)
    backoff = max(config.SUPABASE_DB_RETRY_BACKOFF_SECONDS, 0.0)
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt >= attempts or not is_transient_error(exc):
                raise
            delay = backoff * (2 ** (attempt - 1))
            logs.warning(
                "db.retry",
                operation=label,
                attempt=attempt,
                next_attempt=attempt + 1,
                max_attempts=attempts,
                sleep_seconds=round(delay, 3),
                error_type=type(exc).__name__,
                error=repr(exc),
            )
            _reset_client()
            if delay > 0:
                time.sleep(delay)
    raise RuntimeError(f"unreachable retry state for {label}")


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
def count_users() -> int:
    resp = _execute(
        lambda: client().table("users").select("id", count="exact").execute(),
        label="count_users",
    )
    return resp.count or 0


def get_user(user_id: int) -> dict | None:
    return _one(
        _execute(
            lambda: client().table("users").select("*").eq("id", user_id).execute(),
            label="get_user",
        )
    )


def create_user(user_id: int) -> dict:
    row = {
        "id": user_id,
        "timezone": config.DEFAULT_TIMEZONE,
        "past_search_lookback": config.DEFAULT_LOOKBACK_DAYS,
    }
    return _one(
        _execute(lambda: client().table("users").insert(row).execute(), label="create_user")
    ) or row


def update_user(user_id: int, **fields: Any) -> None:
    if not fields:
        return
    _execute(
        lambda: client().table("users").update(fields).eq("id", user_id).execute(),
        label="update_user",
    )


def users_with_mode(mode: str) -> list[dict]:
    return _rows(
        _execute(
            lambda: client().table("users").select("*").eq("alert_mode", mode).execute(),
            label="users_with_mode",
        )
    )


# --------------------------------------------------------------------------- #
# Conversation state (webhook bot)
# --------------------------------------------------------------------------- #
def get_state(user_id: int) -> dict:
    row = _one(
        _execute(
            lambda: client()
            .table("conversation_state")
            .select("*")
            .eq("user_id", user_id)
            .execute(),
            label="get_state",
        )
    )
    if not row:
        return {"state": None, "data": {}}
    return {"state": row.get("state"), "data": row.get("data") or {}}


def set_state(user_id: int, state: str | None, data: dict | None = None) -> None:
    _execute(
        lambda: client()
        .table("conversation_state")
        .upsert(
            {"user_id": user_id, "state": state, "data": data or {}},
            on_conflict="user_id",
        )
        .execute(),
        label="set_state",
    )


def clear_state(user_id: int) -> None:
    set_state(user_id, None, {})


# --------------------------------------------------------------------------- #
# Source channels
# --------------------------------------------------------------------------- #
def list_channels(user_id: int) -> list[dict]:
    return _rows(
        _execute(
            lambda: client()
            .table("source_channels")
            .select("*")
            .eq("user_id", user_id)
            .order("username")
            .execute(),
            label="list_channels",
        )
    )


def channel_usernames(user_id: int) -> list[str]:
    return [c["username"] for c in list_channels(user_id)]


def count_channels(user_id: int) -> int:
    resp = _execute(
        lambda: client()
        .table("source_channels")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .execute(),
        label="count_channels",
    )
    return resp.count or 0


def has_channel(user_id: int, username: str) -> bool:
    return _one(
        _execute(
            lambda: client()
            .table("source_channels")
            .select("id")
            .eq("user_id", user_id)
            .eq("username", username)
            .execute(),
            label="has_channel",
        )
    ) is not None


def add_channel(user_id: int, username: str) -> None:
    _execute(
        lambda: client()
        .table("source_channels")
        .insert({"user_id": user_id, "username": username})
        .execute(),
        label="add_channel",
    )


def remove_channel(user_id: int, username: str) -> None:
    _execute(
        lambda: client()
        .table("source_channels")
        .delete()
        .eq("user_id", user_id)
        .eq("username", username)
        .execute(),
        label="remove_channel",
    )


# --------------------------------------------------------------------------- #
# Source posts
# --------------------------------------------------------------------------- #
def get_post(channel_username: str, message_id: int) -> dict | None:
    return _one(
        _execute(
            lambda: client()
            .table("source_posts")
            .select("*")
            .eq("channel_username", channel_username)
            .eq("message_id", message_id)
            .execute(),
            label="get_post",
        )
    )


def posts_by_message_ids(channel_username: str, message_ids: list[int]) -> dict[int, dict]:
    if not message_ids:
        return {}
    ids = list(dict.fromkeys(message_ids))
    rows = _rows(
        _execute(
            lambda: client()
            .table("source_posts")
            .select("*")
            .eq("channel_username", channel_username)
            .in_("message_id", ids)
            .execute(),
            label="posts_by_message_ids",
        )
    )
    return {int(row["message_id"]): row for row in rows}


def insert_post(post: dict) -> dict | None:
    return _one(
        _execute(
            lambda: client().table("source_posts").insert(post).execute(),
            label="insert_post",
        )
    )


def insert_posts(posts: list[dict]) -> list[dict]:
    if not posts:
        return []
    return _rows(
        _execute(
            lambda: client().table("source_posts").insert(posts).execute(),
            label="insert_posts",
        )
    )


def update_post(post_id: int, **fields: Any) -> None:
    if not fields:
        return
    _execute(
        lambda: client().table("source_posts").update(fields).eq("id", post_id).execute(),
        label="update_post",
    )


def _unembedded_query(channel_usernames: list[str], posted_after: str | None = None):
    query = (
        client()
        .table("source_posts")
        .select("id,channel_username,message_id,posted_at,normalized_content")
        .in_("channel_username", channel_usernames)
        .is_("embedding", "null")
    )
    if posted_after:
        query = query.gte("posted_at", posted_after)
    return query


def list_unembedded_posts(
    channel_usernames: list[str],
    posted_after: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    if not channel_usernames:
        return []
    query = _unembedded_query(channel_usernames, posted_after).order("posted_at", desc=True)
    if limit and limit > 0:
        query = query.limit(limit)
    return _rows(_execute(lambda: query.execute(), label="list_unembedded_posts"))


def count_unembedded_posts(
    channel_usernames: list[str], posted_after: str | None = None
) -> int:
    if not channel_usernames:
        return 0
    query = (
        client()
        .table("source_posts")
        .select("id", count="exact")
        .in_("channel_username", channel_usernames)
        .is_("embedding", "null")
    )
    if posted_after:
        query = query.gte("posted_at", posted_after)
    resp = _execute(lambda: query.execute(), label="count_unembedded_posts")
    return resp.count or 0


# --------------------------------------------------------------------------- #
# Matches (near-live dedup + record keeping)
# --------------------------------------------------------------------------- #
def matched_post_ids(user_id: int, context: str) -> set[int]:
    rows = _rows(
        _execute(
            lambda: client()
            .table("matches")
            .select("source_post_id")
            .eq("user_id", user_id)
            .eq("context", context)
            .execute(),
            label="matched_post_ids",
        )
    )
    return {r["source_post_id"] for r in rows}


def record_match(user_id: int, source_post_id: int, score: float, context: str) -> None:
    """Insert a match, ignoring the duplicate if it already exists."""
    try:
        # on_conflict -> DO UPDATE keeps the row idempotent and present, which is
        # all the near-live dedup needs (re-touching score/matched_at is harmless).
        _execute(
            lambda: client()
            .table("matches")
            .upsert(
                {
                    "user_id": user_id,
                    "source_post_id": source_post_id,
                    "score": score,
                    "context": context,
                },
                on_conflict="user_id,source_post_id,context",
            )
            .execute(),
            label="record_match",
        )
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Jobs (Past Search queue)
# --------------------------------------------------------------------------- #
def enqueue_job(user_id: int, type_: str, payload: dict) -> dict | None:
    return _one(
        _execute(
            lambda: client()
            .table("jobs")
            .insert({"user_id": user_id, "type": type_, "payload": payload})
            .execute(),
            label="enqueue_job",
        )
    )


def latest_job(user_id: int, type_: str | None = None) -> dict | None:
    query = client().table("jobs").select("*").eq("user_id", user_id)
    if type_:
        query = query.eq("type", type_)
    return _one(
        _execute(
            lambda: query.order("created_at", desc=True).limit(1).execute(),
            label="latest_job",
        )
    )


def get_job(job_id: int) -> dict | None:
    return _one(
        _execute(
            lambda: client().table("jobs").select("*").eq("id", job_id).execute(),
            label="get_job",
        )
    )


def update_job_progress(job_id: int, **progress: Any) -> None:
    job = get_job(job_id)
    if not job:
        return
    payload = dict(job.get("payload") or {})
    current = dict(payload.get("_progress") or {})
    current.update(progress)
    current["updated_at"] = timeutil.now_iso()
    payload["_progress"] = current
    _execute(
        lambda: client().table("jobs").update({"payload": payload}).eq("id", job_id).execute(),
        label="update_job_progress",
    )


def _job_ready_to_claim(job: dict) -> bool:
    payload = job.get("payload") or {}
    progress = payload.get("_progress") or {}
    next_attempt = timeutil.parse(progress.get("next_attempt_after"))
    return next_attempt is None or next_attempt <= timeutil.now_utc()


def claim_pending_jobs(limit: int = 10) -> list[dict]:
    """Claim pending jobs and recover jobs abandoned by canceled workers.

    GitHub can cancel a run at the workflow timeout, which prevents the process
    from marking its active job as error. A stale running job is safe to retry
    because scraping is idempotent and unchanged posts are skipped. Pending jobs
    may also carry a retry timestamp when Gemini quota is temporarily exhausted.
    """
    pending_rows = _rows(
        _execute(
            lambda: client()
            .table("jobs")
            .select("*")
            .eq("status", "pending")
            .order("created_at")
            .limit(max(limit * 5, limit))
            .execute(),
            label="claim_pending_jobs.pending",
        )
    )
    pending: list[dict] = []
    for job in pending_rows:
        if _job_ready_to_claim(job):
            pending.append(job)
        if len(pending) >= limit:
            break

    stale_before = timeutil.iso(
        timeutil.now_utc() - timedelta(minutes=config.JOB_STALE_MINUTES)
    )
    stale_running = _rows(
        _execute(
            lambda: client()
            .table("jobs")
            .select("*")
            .eq("status", "running")
            .lt("started_at", stale_before)
            .order("started_at")
            .limit(max(limit - len(pending), 0))
            .execute(),
            label="claim_pending_jobs.stale",
        )
    )

    claimed = []
    for job in pending + stale_running:
        mark_job_started(job["id"])
        claimed.append(job)
    return claimed


def mark_job_started(job_id: int) -> None:
    _execute(
        lambda: client()
        .table("jobs")
        .update(
            {
                "status": "running",
                "started_at": timeutil.now_iso(),
                "finished_at": None,
                "error": None,
            }
        )
        .eq("id", job_id)
        .execute(),
        label="mark_job_started",
    )


def defer_job(job_id: int, error: str | None = None) -> None:
    fields: dict[str, Any] = {
        "status": "pending",
        "started_at": None,
        "finished_at": None,
        "error": error[:2000] if error else None,
    }
    _execute(
        lambda: client().table("jobs").update(fields).eq("id", job_id).execute(),
        label="defer_job",
    )


def finish_job(job_id: int, status: str, error: str | None = None) -> None:
    fields: dict[str, Any] = {"status": status, "finished_at": timeutil.now_iso()}
    if error:
        fields["error"] = error[:2000]
    _execute(
        lambda: client().table("jobs").update(fields).eq("id", job_id).execute(),
        label="finish_job",
    )


# --------------------------------------------------------------------------- #
# Semantic search
# --------------------------------------------------------------------------- #
def match_source_posts(
    query_embedding: list[float],
    channel_usernames: list[str],
    threshold: float,
    posted_after: str | None = None,
) -> list[dict]:
    if not channel_usernames:
        return []
    resp = _execute(
        lambda: client()
        .rpc(
            "match_source_posts",
            {
                "query_embedding": query_embedding,
                "channel_usernames": channel_usernames,
                "match_threshold": threshold,
                "posted_after": posted_after,
            },
        )
        .execute(),
        label="match_source_posts",
    )
    return _rows(resp)

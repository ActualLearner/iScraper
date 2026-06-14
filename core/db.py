"""Supabase data-access layer. The single source of truth for all surfaces.

Everything is keyed by Telegram user id. The Supabase service-role key is used,
so this module must only ever run server-side (webhook + worker), never client.
"""
from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from typing import Any

from core import config, timeutil


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


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
def count_users() -> int:
    resp = client().table("users").select("id", count="exact").execute()
    return resp.count or 0


def get_user(user_id: int) -> dict | None:
    return _one(client().table("users").select("*").eq("id", user_id).execute())


def create_user(user_id: int) -> dict:
    row = {"id": user_id, "timezone": config.DEFAULT_TIMEZONE,
           "past_search_lookback": config.DEFAULT_LOOKBACK_DAYS}
    return _one(client().table("users").insert(row).execute()) or row


def update_user(user_id: int, **fields: Any) -> None:
    if not fields:
        return
    client().table("users").update(fields).eq("id", user_id).execute()


def users_with_mode(mode: str) -> list[dict]:
    return _rows(client().table("users").select("*").eq("alert_mode", mode).execute())


# --------------------------------------------------------------------------- #
# Conversation state (webhook bot)
# --------------------------------------------------------------------------- #
def get_state(user_id: int) -> dict:
    row = _one(
        client().table("conversation_state").select("*").eq("user_id", user_id).execute()
    )
    if not row:
        return {"state": None, "data": {}}
    return {"state": row.get("state"), "data": row.get("data") or {}}


def set_state(user_id: int, state: str | None, data: dict | None = None) -> None:
    client().table("conversation_state").upsert(
        {"user_id": user_id, "state": state, "data": data or {}},
        on_conflict="user_id",
    ).execute()


def clear_state(user_id: int) -> None:
    set_state(user_id, None, {})


# --------------------------------------------------------------------------- #
# Source channels
# --------------------------------------------------------------------------- #
def list_channels(user_id: int) -> list[dict]:
    return _rows(
        client()
        .table("source_channels")
        .select("*")
        .eq("user_id", user_id)
        .order("username")
        .execute()
    )


def channel_usernames(user_id: int) -> list[str]:
    return [c["username"] for c in list_channels(user_id)]


def count_channels(user_id: int) -> int:
    resp = (
        client()
        .table("source_channels")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .execute()
    )
    return resp.count or 0


def has_channel(user_id: int, username: str) -> bool:
    return (
        _one(
            client()
            .table("source_channels")
            .select("id")
            .eq("user_id", user_id)
            .eq("username", username)
            .execute()
        )
        is not None
    )


def add_channel(user_id: int, username: str) -> None:
    client().table("source_channels").insert(
        {"user_id": user_id, "username": username}
    ).execute()


def remove_channel(user_id: int, username: str) -> None:
    client().table("source_channels").delete().eq("user_id", user_id).eq(
        "username", username
    ).execute()


# --------------------------------------------------------------------------- #
# Source posts
# --------------------------------------------------------------------------- #
def get_post(channel_username: str, message_id: int) -> dict | None:
    return _one(
        client()
        .table("source_posts")
        .select("*")
        .eq("channel_username", channel_username)
        .eq("message_id", message_id)
        .execute()
    )


def insert_post(post: dict) -> dict | None:
    return _one(client().table("source_posts").insert(post).execute())


def update_post(post_id: int, **fields: Any) -> None:
    if not fields:
        return
    client().table("source_posts").update(fields).eq("id", post_id).execute()


# --------------------------------------------------------------------------- #
# Matches (near-live dedup + record keeping)
# --------------------------------------------------------------------------- #
def matched_post_ids(user_id: int, context: str) -> set[int]:
    rows = _rows(
        client()
        .table("matches")
        .select("source_post_id")
        .eq("user_id", user_id)
        .eq("context", context)
        .execute()
    )
    return {r["source_post_id"] for r in rows}


def record_match(user_id: int, source_post_id: int, score: float, context: str) -> None:
    """Insert a match, ignoring the duplicate if it already exists."""
    try:
        # on_conflict -> DO UPDATE keeps the row idempotent and present, which is
        # all the near-live dedup needs (re-touching score/matched_at is harmless).
        client().table("matches").upsert(
            {
                "user_id": user_id,
                "source_post_id": source_post_id,
                "score": score,
                "context": context,
            },
            on_conflict="user_id,source_post_id,context",
        ).execute()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Jobs (Past Search queue)
# --------------------------------------------------------------------------- #
def enqueue_job(user_id: int, type_: str, payload: dict) -> dict | None:
    return _one(
        client()
        .table("jobs")
        .insert({"user_id": user_id, "type": type_, "payload": payload})
        .execute()
    )


def latest_job(user_id: int, type_: str | None = None) -> dict | None:
    query = client().table("jobs").select("*").eq("user_id", user_id)
    if type_:
        query = query.eq("type", type_)
    return _one(query.order("created_at", desc=True).limit(1).execute())


def get_job(job_id: int) -> dict | None:
    return _one(client().table("jobs").select("*").eq("id", job_id).execute())


def update_job_progress(job_id: int, **progress: Any) -> None:
    job = get_job(job_id)
    if not job:
        return
    payload = dict(job.get("payload") or {})
    current = dict(payload.get("_progress") or {})
    current.update(progress)
    current["updated_at"] = timeutil.now_iso()
    payload["_progress"] = current
    client().table("jobs").update({"payload": payload}).eq("id", job_id).execute()


def claim_pending_jobs(limit: int = 10) -> list[dict]:
    """Claim pending jobs and recover jobs abandoned by canceled workers.

    GitHub can cancel a run at the workflow timeout, which prevents the process
    from marking its active job as error. A stale running job is safe to retry
    because scraping is idempotent and unchanged posts are skipped.
    """
    pending = _rows(
        client()
        .table("jobs")
        .select("*")
        .eq("status", "pending")
        .order("created_at")
        .limit(limit)
        .execute()
    )
    stale_before = timeutil.iso(
        timeutil.now_utc() - timedelta(minutes=config.JOB_STALE_MINUTES)
    )
    stale_running = _rows(
        client()
        .table("jobs")
        .select("*")
        .eq("status", "running")
        .lt("started_at", stale_before)
        .order("started_at")
        .limit(max(limit - len(pending), 0))
        .execute()
    )

    claimed = []
    for job in pending + stale_running:
        mark_job_started(job["id"])
        claimed.append(job)
    return claimed


def mark_job_started(job_id: int) -> None:
    client().table("jobs").update(
        {
            "status": "running",
            "started_at": timeutil.now_iso(),
            "finished_at": None,
            "error": None,
        }
    ).eq("id", job_id).execute()


def finish_job(job_id: int, status: str, error: str | None = None) -> None:
    fields: dict[str, Any] = {"status": status, "finished_at": timeutil.now_iso()}
    if error:
        fields["error"] = error[:2000]
    client().table("jobs").update(fields).eq("id", job_id).execute()


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
    resp = client().rpc(
        "match_source_posts",
        {
            "query_embedding": query_embedding,
            "channel_usernames": channel_usernames,
            "match_threshold": threshold,
            "posted_after": posted_after,
        },
    ).execute()
    return _rows(resp)

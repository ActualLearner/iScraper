"""Past Search job processor (queued by the bot, drained by the worker).

A one-time semantic search over the user's source channels within a lookback
window. Scrapes the window up to date, then runs the search using the match
profile chosen for this run. Does not touch the interval-alert cursor.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from telethon import TelegramClient

from core import config, db, embeddings, logs, telegram_api, timeutil
from jobs import common
from scraper.scrape import embed_pending_posts, scrape_user_channels


class DeferredPastSearch(Exception):
    """The job is healthy but needs another worker run to continue."""

    def __init__(self, message: str, *, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


async def run_pending(client: TelegramClient) -> None:
    failures: list[int] = []
    for job in db.claim_pending_jobs(limit=config.PAST_SEARCH_JOBS_PER_RUN):
        if job.get("type") != "past_search":
            db.finish_job(job["id"], "done")
            continue
        try:
            logs.info("past_search.job_start", job_id=job["id"], user_id=job.get("user_id"))
            status = await _run_one(client, job)
            db.finish_job(job["id"], status)
            logs.info("past_search.job_done", job_id=job["id"], user_id=job.get("user_id"), status=status)
        except DeferredPastSearch as exc:
            db.defer_job(job["id"])
            logs.info(
                "past_search.job_deferred",
                job_id=job["id"],
                user_id=job.get("user_id"),
                reason=str(exc),
                retry_after=exc.retry_after,
            )
        except Exception as exc:
            if db.is_transient_error(exc):
                retry_after = timeutil.iso(
                    timeutil.now_utc()
                    + timedelta(minutes=config.SUPABASE_DB_RETRY_JOB_MINUTES)
                )
                db.update_job_progress(
                    job["id"],
                    stage="retry_wait",
                    next_attempt_after=retry_after,
                    message="Temporary database connection error; retrying later.",
                )
                db.defer_job(job["id"], repr(exc))
                logs.info(
                    "past_search.job_deferred",
                    job_id=job["id"],
                    user_id=job.get("user_id"),
                    reason="temporary database connection error",
                    retry_after=retry_after,
                )
                continue
            logs.exception("past_search.job_failed", exc, job_id=job["id"], user_id=job.get("user_id"))
            db.update_job_progress(job["id"], stage="error")
            db.finish_job(job["id"], "error", repr(exc))
            telegram_api.send_message(
                job.get("user_id"),
                f"Past Search #{job['id']} failed. Use /search_status for details.",
            )
            failures.append(job["id"])
    if failures:
        raise RuntimeError(f"Past Search job(s) failed: {failures}")


def _job_boundary(job: dict, lookback: int):
    created_at = timeutil.parse(job.get("created_at")) or timeutil.now_utc()
    return created_at - timedelta(days=lookback)


def _progress(payload: dict[str, Any]) -> dict[str, Any]:
    return dict((payload or {}).get("_progress") or {})


async def _run_one(client: TelegramClient, job: dict) -> str:
    user_id = job["user_id"]
    payload = job.get("payload") or {}
    lookback = int(payload.get("lookback_days") or config.DEFAULT_LOOKBACK_DAYS)
    profile = (payload.get("match_profile") or "").strip()

    # Past Search results go to the user's direct messages.
    chat_id = user_id
    channels = db.channel_usernames(user_id)
    if not channels:
        db.update_job_progress(job["id"], stage="error", message="no source channels")
        telegram_api.send_message(chat_id, "You have no source channels/groups to search.")
        return "error"
    if not profile:
        db.update_job_progress(job["id"], stage="error", message="empty match profile")
        telegram_api.send_message(
            chat_id,
            "Your match profile was empty, so I couldn't search. Save one in /settings.",
        )
        return "error"

    boundary = _job_boundary(job, lookback)
    boundary_iso = timeutil.iso(boundary)
    existing_progress = _progress(payload)
    if existing_progress.get("scrape_complete"):
        posts_written = int(existing_progress.get("posts_written") or 0)
        logs.info("past_search.scrape_skipped", job_id=job["id"], reason="already_complete")
    else:
        db.update_job_progress(
            job["id"],
            stage="scraping",
            sources_total=len(channels),
            sources_done=0,
            posts_written=0,
            matches_found=0,
            boundary=boundary_iso,
        )
        posts_written = await scrape_user_channels(client, channels, boundary, job_id=job["id"])
        db.update_job_progress(
            job["id"],
            stage="scraping",
            posts_written=posts_written,
            scrape_complete=True,
            boundary=boundary_iso,
        )

    # Embed a bounded chunk of the backlog this pass, then match and deliver what is
    # already indexed. Matches stream out across worker passes instead of blocking on
    # the whole backlog; a per-job delivered-id set keeps the same post from being
    # sent twice. If posts remain unindexed, defer and continue on the next run.
    embed_cap = config.PAST_SEARCH_EMBED_PER_RUN or None
    stats = await embed_pending_posts(
        channels, boundary_iso, max_count=embed_cap, job_id=job["id"]
    )
    remaining = int(stats.get("remaining") or 0)

    db.update_job_progress(job["id"], stage="embedding_query", posts_written=posts_written)
    query_vec = embeddings.embed_query(profile)
    db.update_job_progress(job["id"], stage="matching")
    results = db.match_source_posts(
        query_vec, channels, common.threshold(), posted_after=boundary_iso
    )

    delivered = set(existing_progress.get("delivered_post_ids") or [])
    fresh = [r for r in results if r["id"] not in delivered]
    db.update_job_progress(job["id"], stage="delivering", matches_found=len(results))
    for r in fresh:
        db.record_match(user_id, r["id"], r.get("similarity", 0.0), "past_search")
    if fresh:
        common.deliver_batch(chat_id, fresh)
        delivered.update(r["id"] for r in fresh)
        db.update_job_progress(job["id"], delivered_post_ids=sorted(delivered))

    if remaining > 0:
        retry_after = timeutil.iso(
            timeutil.now_utc()
            + timedelta(minutes=config.EMBEDDING_BACKLOG_RETRY_MINUTES)
        )
        db.update_job_progress(
            job["id"],
            stage="embedding_posts",
            next_attempt_after=retry_after,
            message="Indexing more posts; further matches will follow.",
        )
        raise DeferredPastSearch(
            "Embedding backlog remains; continuing later.", retry_after=retry_after
        )

    if not results:
        telegram_api.send_message(chat_id, "No matches found.")
    db.update_job_progress(
        job["id"], stage="done", matches_found=len(results), posts_written=posts_written
    )
    return "done"

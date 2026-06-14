"""Past Search job processor (queued by the bot, drained by the worker).

A one-time semantic search over the user's source channels within a lookback
window. Scrapes the window up to date, then runs the search using the match
profile chosen for this run. Does not touch the interval-alert cursor.
"""
from __future__ import annotations

from datetime import timedelta

from telethon import TelegramClient

from core import config, db, embeddings, logs, telegram_api, timeutil
from jobs import common
from scraper.scrape import scrape_user_channels


async def run_pending(client: TelegramClient) -> None:
    for job in db.claim_pending_jobs():
        if job.get("type") != "past_search":
            db.finish_job(job["id"], "done")
            continue
        try:
            logs.info("past_search.job_start", job_id=job["id"], user_id=job.get("user_id"))
            status = await _run_one(client, job)
            db.finish_job(job["id"], status)
            logs.info("past_search.job_done", job_id=job["id"], user_id=job.get("user_id"), status=status)
        except Exception as exc:
            logs.exception("past_search.job_failed", exc, job_id=job["id"], user_id=job.get("user_id"))
            db.update_job_progress(job["id"], stage="error")
            db.finish_job(job["id"], "error", repr(exc))
            telegram_api.send_message(
                job.get("user_id"),
                f"Past Search #{job['id']} failed. Use /search_status for details.",
            )


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

    boundary = timeutil.now_utc() - timedelta(days=lookback)
    db.update_job_progress(
        job["id"],
        stage="scraping",
        sources_total=len(channels),
        sources_done=0,
        posts_written=0,
        matches_found=0,
    )
    posts_written = await scrape_user_channels(client, channels, boundary, job_id=job["id"])

    db.update_job_progress(job["id"], stage="embedding_query", posts_written=posts_written)
    query_vec = embeddings.embed_query(profile)
    db.update_job_progress(job["id"], stage="matching")
    results = db.match_source_posts(
        query_vec, channels, common.threshold(), posted_after=timeutil.iso(boundary)
    )
    db.update_job_progress(job["id"], stage="delivering", matches_found=len(results))

    for r in results:
        db.record_match(user_id, r["id"], r.get("similarity", 0.0), "past_search")

    if results:
        common.deliver_batch(chat_id, results)
    else:
        telegram_api.send_message(chat_id, "No matches found.")
    db.update_job_progress(job["id"], stage="done", matches_found=len(results), posts_written=posts_written)
    return "done"

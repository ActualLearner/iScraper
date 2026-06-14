"""Past Search job processor (queued by the bot, drained by the worker).

A one-time semantic search over the user's source channels within a lookback
window. Scrapes the window up to date, then runs the search using the match
profile chosen for this run. Does not touch the interval-alert cursor.
"""
from __future__ import annotations

from datetime import timedelta

from telethon import TelegramClient

from core import config, db, embeddings, telegram_api, timeutil
from jobs import common
from scraper.scrape import scrape_user_channels


async def run_pending(client: TelegramClient) -> None:
    for job in db.claim_pending_jobs():
        if job.get("type") != "past_search":
            db.finish_job(job["id"], "done")
            continue
        try:
            await _run_one(client, job)
            db.finish_job(job["id"], "done")
        except Exception as exc:
            print(f"[past_search] job {job['id']} failed: {exc!r}")
            db.finish_job(job["id"], "error", repr(exc))


async def _run_one(client: TelegramClient, job: dict) -> None:
    user_id = job["user_id"]
    payload = job.get("payload") or {}
    lookback = int(payload.get("lookback_days") or config.DEFAULT_LOOKBACK_DAYS)
    profile = (payload.get("match_profile") or "").strip()

    # Past Search results go to the user's direct messages.
    chat_id = user_id
    channels = db.channel_usernames(user_id)
    if not channels:
        telegram_api.send_message(chat_id, "You have no source channels to search.")
        return
    if not profile:
        telegram_api.send_message(
            chat_id,
            "Your match profile was empty, so I couldn't search. Save one in /settings.",
        )
        return

    boundary = timeutil.now_utc() - timedelta(days=lookback)
    await scrape_user_channels(client, channels, boundary)

    query_vec = embeddings.embed_query(profile)
    results = db.match_source_posts(
        query_vec, channels, common.threshold(), posted_after=timeutil.iso(boundary)
    )

    for r in results:
        db.record_match(user_id, r["id"], r.get("similarity", 0.0), "past_search")

    if results:
        common.deliver_batch(chat_id, results)
    else:
        telegram_api.send_message(chat_id, "No matches found.")

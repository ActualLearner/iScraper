"""Near-Live Alert processor: Ongoing Alerts in 'Every N minutes' mode.

For each due user, scrape posts observed since the last check, match against the
alert profile, and deliver each new match as its own single-link message. Only
posts at/after the moment the alert was enabled are eligible, and any post already
delivered to that user is skipped. No "No matches found" messages.
"""
from __future__ import annotations

from datetime import timedelta

from telethon import TelegramClient

from core import db, embeddings, telegram_api, timeutil
from jobs import common


def _is_due(user: dict) -> bool:
    minutes = user.get("alert_interval_minutes")
    if not minutes:
        return False
    last = timeutil.parse(user.get("near_live_last_checked_at"))
    if last is None:
        return True
    elapsed = (timeutil.now_utc() - last).total_seconds()
    # 60s slack so a 5-minute interval still fires on a ~5-minute, jittery schedule.
    return elapsed >= minutes * 60 - 60


async def run_due(client: TelegramClient) -> None:
    for user in db.users_with_mode("near_live"):
        try:
            if not _is_due(user):
                continue
            await _run(client, user)
        except Exception as exc:
            print(f"[near_live] user {user.get('id')} failed: {exc!r}")


async def _run(client: TelegramClient, user: dict) -> None:
    from scraper.scrape import scrape_user_channels

    user_id = user["id"]
    profile = common.resolve_alert_profile(user)
    chat_id = common.delivery_chat_id(user)
    channels = db.channel_usernames(user_id)

    started = timeutil.parse(user.get("near_live_started_at")) or timeutil.now_utc()
    last_checked = timeutil.parse(user.get("near_live_last_checked_at")) or started

    if not channels or not profile:
        db.update_user(user_id, near_live_last_checked_at=timeutil.now_iso())
        return

    # Scrape posts observed since the last check (bounded by the enable time).
    scrape_boundary = max(started, last_checked - timedelta(minutes=2))
    await scrape_user_channels(client, channels, scrape_boundary)

    query_vec = embeddings.embed_query(profile)
    results = db.match_source_posts(
        query_vec, channels, common.threshold(), posted_after=timeutil.iso(started)
    )

    already = db.matched_post_ids(user_id, "near_live")
    for r in results:
        if r["id"] in already:
            continue
        db.record_match(user_id, r["id"], r.get("similarity", 0.0), "near_live")
        telegram_api.send_message(chat_id, common.match_line(r), disable_preview=True)

    db.update_user(user_id, near_live_last_checked_at=timeutil.now_iso())

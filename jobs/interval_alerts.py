"""Interval Alert processor: Ongoing Alerts in 'Every N days' mode.

Each due user gets one combined message with the links matched since their last
interval-alert sent time, or a short "No matches found" message. The sent time is
updated only after the run finishes.
"""
from __future__ import annotations

from datetime import timedelta

from telethon import TelegramClient

from core import db, embeddings, telegram_api, timeutil
from jobs import common
from scraper.scrape import scrape_user_channels

# How wide a window after the configured delivery time still counts as "due",
# to tolerate the worker's coarse, jittery schedule.
_DELIVERY_WINDOW_MIN = 20


def _is_due(user: dict) -> bool:
    days = user.get("alert_interval_days")
    delivery_time = user.get("alert_delivery_time")
    if not days or not delivery_time:
        return False

    now = timeutil.now_utc()
    last = timeutil.parse(user.get("alert_last_sent_at"))
    # Require roughly N days since the last send (30 min slack for schedule jitter).
    if last is not None and (now - last) < timedelta(days=days, minutes=-30):
        return False

    local = timeutil.local_now(user.get("timezone", "UTC"))
    try:
        th, tm = (int(x) for x in delivery_time.split(":"))
    except (ValueError, AttributeError):
        return False
    target = th * 60 + tm
    current = local.hour * 60 + local.minute
    return target <= current < target + _DELIVERY_WINDOW_MIN


async def run_due(client: TelegramClient) -> None:
    for user in db.users_with_mode("interval"):
        try:
            if not _is_due(user):
                continue
            await _run(client, user)
        except Exception as exc:
            print(f"[interval] user {user.get('id')} failed: {exc!r}")


async def _run(client: TelegramClient, user: dict) -> None:
    user_id = user["id"]
    profile = common.resolve_alert_profile(user)
    chat_id = common.delivery_chat_id(user)
    channels = db.channel_usernames(user_id)
    if not channels or not profile:
        return

    last = timeutil.parse(user.get("alert_last_sent_at")) or (
        timeutil.now_utc() - timedelta(days=user["alert_interval_days"])
    )
    await scrape_user_channels(client, channels, last)

    query_vec = embeddings.embed_query(profile)
    results = db.match_source_posts(
        query_vec, channels, common.threshold(), posted_after=timeutil.iso(last)
    )

    for r in results:
        db.record_match(user_id, r["id"], r.get("similarity", 0.0), "interval")

    if results:
        common.deliver_batch(chat_id, results)
    else:
        telegram_api.send_message(chat_id, "No matches found.")

    # Update the cursor only after the run finishes.
    db.update_user(user_id, alert_last_sent_at=timeutil.now_iso())

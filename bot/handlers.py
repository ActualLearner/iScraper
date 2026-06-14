"""Telegram update router + conversation flows for the interactive bot.

Stateless between calls: every step reads and writes conversation state in
Supabase, so it runs fine inside a Vercel serverless invocation. Uses the Bot
API only (no Telethon / embeddings here).

State keys (conversation_state.state):
    onb_profile        awaiting onboarding match profile text (or Skip)
    onb_channels       awaiting onboarding channel input
    profile_set        awaiting new saved match profile text
    channels_add       awaiting channel input (from settings/onboarding add)
    past_lookback      awaiting Past Search lookback (days)
    settings_lookback  awaiting new default Past Search lookback (days)
    await_profile      awaiting "use saved / enter new" choice (buttons)
    mp_new             awaiting new match profile text for the active run
    alerts_days        awaiting interval N days
    alerts_time        awaiting interval delivery time HH:MM
    alerts_minutes     awaiting near-live N minutes
    await_delivery     awaiting delivery destination choice (buttons)
    tz_set             awaiting timezone

data carries flow scratch: {"flow": "past"|"alert", "past": {...}, "alert": {...}}
"""
from __future__ import annotations

import re

from bot import copy, keyboards
from core import channels as ch
from core import config, db, logs, telegram_api, text as textutil, timeutil

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def handle_update(update: dict) -> None:
    try:
        if "message" in update:
            _handle_message(update["message"])
        elif "callback_query" in update:
            _handle_callback(update["callback_query"])
    except Exception as exc:  # never let the webhook 500 on a single bad update
        logs.exception("bot.handle_update_error", exc, update_id=update.get("update_id"))
        chat_id = _chat_id_from_update(update)
        if chat_id is not None:
            telegram_api.send_message(
                chat_id,
                "Something went wrong while handling that. The error was logged; try /settings or /cancel.",
            )


def _chat_id_from_update(update: dict) -> int | None:
    if "message" in update:
        return update["message"].get("chat", {}).get("id")
    if "callback_query" in update:
        return update["callback_query"].get("message", {}).get("chat", {}).get("id")
    return None


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
def _handle_message(msg: dict) -> None:
    chat = msg.get("chat", {})
    chat_type = chat.get("type")
    from_user = msg.get("from", {})
    user_id = from_user.get("id")
    text = (msg.get("text") or "").strip()
    if user_id is None:
        return

    # Group messages: only the /here delivery-registration command is handled.
    if chat_type in ("group", "supergroup"):
        if text.split("@")[0] == "/here":
            _register_group_delivery(user_id, chat)
        return

    chat_id = chat.get("id", user_id)
    user = db.get_user(user_id)

    # Beta gating: a user is created on first contact, if a slot is free.
    if user is None:
        if db.count_users() >= config.BETA_MAX_USERS:
            telegram_api.send_message(chat_id, copy.BETA_FULL)
            return
        user = db.create_user(user_id)
        _begin_onboarding(chat_id, user_id)
        return

    if text.startswith("/"):
        _handle_command(chat_id, user_id, text)
        return

    _route_state(chat_id, user, text)


def _handle_command(chat_id: int, user_id: int, text: str) -> None:
    cmd = text.split()[0].split("@")[0].lower()
    if cmd == "/cancel":
        db.clear_state(user_id)
        telegram_api.send_message(chat_id, copy.CANCELLED)
    elif cmd == "/start":
        db.clear_state(user_id)
        if db.count_channels(user_id) == 0:
            _begin_onboarding(chat_id, user_id, returning=True)
        else:
            telegram_api.send_message(
                chat_id, copy.MAIN_MENU, reply_markup=keyboards.main_menu()
            )
    elif cmd == "/settings":
        db.clear_state(user_id)
        telegram_api.send_message(
            chat_id, copy.MAIN_MENU, reply_markup=keyboards.main_menu()
        )
    elif cmd == "/search_past":
        _start_past_search(chat_id, user_id)
    elif cmd == "/search_status":
        _show_search_status(chat_id, user_id)
    elif cmd == "/alerts":
        _show_alerts_menu(chat_id, user_id)
    elif cmd == "/help":
        telegram_api.send_message(chat_id, _HELP)
    else:
        telegram_api.send_message(
            chat_id,
            "Unknown command. Try /settings, /search_past, /search_status, or /alerts.",
        )


def _route_state(chat_id: int, user: dict, text: str) -> None:
    user_id = user["id"]
    st = db.get_state(user_id)
    state, data = st["state"], st["data"]

    if state == "onb_profile":
        _save_profile_then(chat_id, user_id, text, data, onboarding=True)
    elif state == "profile_set":
        _save_profile_then(chat_id, user_id, text, data, onboarding=False)
    elif state in ("onb_channels", "channels_add"):
        _process_channel_input(chat_id, user_id, text, onboarding=(state == "onb_channels"))
    elif state == "past_lookback":
        _receive_past_lookback(chat_id, user_id, text, data)
    elif state == "settings_lookback":
        _receive_default_lookback(chat_id, user_id, text)
    elif state == "mp_new":
        _receive_new_profile_for_run(chat_id, user, text, data)
    elif state == "alerts_days":
        _receive_alert_days(chat_id, user_id, text, data)
    elif state == "alerts_time":
        _receive_alert_time(chat_id, user_id, text, data)
    elif state == "alerts_minutes":
        _receive_alert_minutes(chat_id, user_id, text, data)
    elif state == "tz_set":
        _receive_timezone(chat_id, user_id, text)
    else:
        telegram_api.send_message(
            chat_id,
            "Use /settings to manage iScraper, /search_past, or /alerts.",
        )


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #
def _handle_callback(cq: dict) -> None:
    data_str = cq.get("data", "")
    cq_id = cq.get("id")
    from_user = cq.get("from", {})
    user_id = from_user.get("id")
    message = cq.get("message", {})
    chat_id = message.get("chat", {}).get("id", user_id)
    telegram_api.answer_callback_query(cq_id)
    if user_id is None or db.get_user(user_id) is None:
        return

    if data_str == "menu:main":
        db.clear_state(user_id)
        telegram_api.send_message(chat_id, copy.MAIN_MENU, reply_markup=keyboards.main_menu())
    elif data_str == "menu:profile":
        _show_profile_menu(chat_id, user_id)
    elif data_str == "menu:channels":
        _show_channels_menu(chat_id, user_id)
    elif data_str == "menu:past":
        _show_past_menu(chat_id, user_id)
    elif data_str == "past:status":
        _show_search_status(chat_id, user_id)
    elif data_str == "menu:alerts":
        _show_alerts_menu(chat_id, user_id)
    elif data_str == "menu:delivery":
        _show_delivery_menu(chat_id, user_id)
    elif data_str == "menu:tz":
        db.set_state(user_id, "tz_set", {})
        telegram_api.send_message(
            chat_id,
            "Send your timezone as an IANA name, e.g. <code>Africa/Addis_Ababa</code> "
            "or <code>Europe/London</code>.",
        )

    elif data_str == "onb:profile_skip":
        _goto_onboarding_channels(chat_id, user_id)

    elif data_str == "profile:set":
        db.set_state(user_id, "profile_set", {})
        telegram_api.send_message(chat_id, copy.MATCH_PROFILE_PROMPT)
    elif data_str == "profile:clear":
        db.update_user(user_id, match_profile=None)
        db.clear_state(user_id)
        telegram_api.send_message(chat_id, "🗑 Cleared your saved match profile.")
        _show_profile_menu(chat_id, user_id)

    elif data_str in ("channels:add", "channels:another"):
        db.set_state(user_id, "channels_add", {})
        telegram_api.send_message(chat_id, copy.ADD_CHANNELS_PROMPT)
    elif data_str == "channels:done":
        db.clear_state(user_id)
        if db.count_channels(user_id) == 0:
            telegram_api.send_message(chat_id, copy.NEED_CHANNEL_BEFORE_DONE)
        else:
            telegram_api.send_message(chat_id, copy.ONBOARDING_DONE)
    elif data_str.startswith("channels:remove:"):
        _remove_channel(chat_id, user_id, data_str.split(":", 2)[2])

    elif data_str == "past:run":
        _start_past_search(chat_id, user_id)
    elif data_str == "past:setdefault":
        db.set_state(user_id, "settings_lookback", {})
        telegram_api.send_message(
            chat_id,
            f"Send a new default lookback in days (max {config.MAX_LOOKBACK_DAYS}). "
            "I'll save it and run a Past Search with it now.",
        )

    elif data_str == "alerts:off":
        db.update_user(user_id, alert_mode="off")
        db.clear_state(user_id)
        telegram_api.send_message(chat_id, "🔕 Ongoing Alerts are now off.")
        _show_alerts_menu(chat_id, user_id)
    elif data_str == "alerts:interval":
        db.set_state(user_id, "alerts_days", {"alert": {"mode": "interval"}})
        telegram_api.send_message(
            chat_id,
            f"How many days between alerts? Send a number "
            f"({config.MIN_INTERVAL_DAYS}–{config.MAX_INTERVAL_DAYS}).",
        )
    elif data_str == "alerts:near":
        db.set_state(user_id, "alerts_minutes", {"alert": {"mode": "near_live"}})
        telegram_api.send_message(
            chat_id,
            copy.NEAR_LIVE_EXPLAINER
            + f"\n\nHow many minutes between checks? Send a number "
            f"({config.MIN_NEAR_LIVE_MINUTES}–{config.MAX_NEAR_LIVE_MINUTES}).",
        )

    elif data_str == "mp:saved":
        _choose_saved_profile(chat_id, user_id)
    elif data_str == "mp:new":
        st = db.get_state(user_id)
        db.set_state(user_id, "mp_new", st["data"])
        telegram_api.send_message(chat_id, copy.MATCH_PROFILE_PROMPT)

    elif data_str == "delivery:dm":
        _choose_delivery(chat_id, user_id, to_group=False)
    elif data_str == "delivery:group":
        _choose_delivery(chat_id, user_id, to_group=True)


# --------------------------------------------------------------------------- #
# Onboarding
# --------------------------------------------------------------------------- #
def _begin_onboarding(chat_id: int, user_id: int, returning: bool = False) -> None:
    if not returning:
        telegram_api.send_message(chat_id, copy.WELCOME)
    db.set_state(user_id, "onb_profile", {})
    telegram_api.send_message(
        chat_id, copy.MATCH_PROFILE_PROMPT, reply_markup=keyboards.onboarding_profile()
    )


def _goto_onboarding_channels(chat_id: int, user_id: int) -> None:
    db.set_state(user_id, "onb_channels", {})
    telegram_api.send_message(chat_id, copy.ONBOARDING_CHANNELS_PROMPT)


# --------------------------------------------------------------------------- #
# Match profile
# --------------------------------------------------------------------------- #
def _validate_profile(text: str) -> tuple[bool, str | None]:
    if not text:
        return False, copy.PROFILE_EMPTY
    words = textutil.word_count(text)
    if words > config.MAX_MATCH_PROFILE_WORDS:
        return False, copy.PROFILE_TOO_LONG.format(words=words)
    return True, None


def _save_profile_then(chat_id, user_id, text, data, onboarding: bool) -> None:
    ok, err = _validate_profile(text)
    if not ok:
        telegram_api.send_message(chat_id, err)
        return
    db.update_user(user_id, match_profile=text)
    telegram_api.send_message(chat_id, copy.PROFILE_SAVED)
    if onboarding:
        _goto_onboarding_channels(chat_id, user_id)
    else:
        db.clear_state(user_id)
        _show_profile_menu(chat_id, user_id)


def _show_profile_menu(chat_id: int, user_id: int) -> None:
    user = db.get_user(user_id)
    profile = (user or {}).get("match_profile")
    if profile:
        body = f"📝 <b>Your match profile</b>\n\n{profile}"
    else:
        body = "📝 <b>Match profile</b>\n\n" + copy.PROFILE_NONE
    telegram_api.send_message(
        chat_id, body, reply_markup=keyboards.profile_menu(bool(profile))
    )


# --------------------------------------------------------------------------- #
# Source channels
# --------------------------------------------------------------------------- #
def _process_channel_input(chat_id, user_id, text, onboarding: bool) -> None:
    tokens = text.split()
    if not tokens:
        telegram_api.send_message(chat_id, copy.INVALID_FORMAT_REJECT)
        return

    existing = set(db.channel_usernames(user_id))
    seen_this_msg: set[str] = set()
    added, already, private, invalid, full = [], [], [], [], []

    for token in tokens:
        result = ch.validate_channel_input(token)
        if not result.ok:
            if result.reason == ch.PARSE_PRIVATE:
                private.append(token)
            else:
                invalid.append(token)
            continue
        uname = result.username
        if uname in existing or uname in seen_this_msg:
            already.append(uname)
            continue
        if len(existing) >= config.MAX_SOURCE_CHANNELS:
            full.append(uname)
            continue
        db.add_channel(user_id, uname)
        existing.add(uname)
        seen_this_msg.add(uname)
        added.append(uname)

    lines: list[str] = []
    if added:
        lines.append("✅ Added: " + ", ".join(f"@{u}" for u in added))
    if already:
        lines.append("ℹ️ Already added: " + ", ".join(f"@{u}" for u in already))
    if full:
        lines.append(
            f"⚠️ Not added (at {config.MAX_SOURCE_CHANNELS}-channel limit): "
            + ", ".join(f"@{u}" for u in full)
        )
    if private:
        lines.append(copy.PRIVATE_LINK_REJECT)
    if invalid:
        lines.append("❌ Couldn't add: " + ", ".join(invalid) + "\n" + copy.INVALID_FORMAT_REJECT)
    telegram_api.send_message(chat_id, "\n\n".join(lines) or copy.INVALID_FORMAT_REJECT)

    if db.count_channels(user_id) == 0:
        # Onboarding must collect at least one valid channel before proceeding.
        telegram_api.send_message(chat_id, copy.ONBOARDING_CHANNELS_PROMPT)
        db.set_state(user_id, "onb_channels", {})
        return

    db.set_state(user_id, "onb_channels" if onboarding else "channels_add", {})
    telegram_api.send_message(
        chat_id, "Add more channels, or you're done.",
        reply_markup=keyboards.add_more_or_done(),
    )


def _show_channels_menu(chat_id: int, user_id: int) -> None:
    usernames = db.channel_usernames(user_id)
    if usernames:
        body = "📡 <b>Source channels</b>\n\n" + "\n".join(f"• @{u}" for u in usernames)
    else:
        body = "📡 <b>Source channels</b>\n\nNo channels yet."
    telegram_api.send_message(
        chat_id, body, reply_markup=keyboards.channels_menu(usernames)
    )


def _remove_channel(chat_id: int, user_id: int, username: str) -> None:
    if db.count_channels(user_id) <= 1:
        telegram_api.send_message(chat_id, copy.CANNOT_REMOVE_LAST)
        _show_channels_menu(chat_id, user_id)
        return
    db.remove_channel(user_id, username)
    telegram_api.send_message(chat_id, f"🗑 Removed @{username}.")
    _show_channels_menu(chat_id, user_id)


# --------------------------------------------------------------------------- #
# Past Search
# --------------------------------------------------------------------------- #
def _show_past_menu(chat_id: int, user_id: int) -> None:
    user = db.get_user(user_id) or {}
    default = user.get("past_search_lookback", config.DEFAULT_LOOKBACK_DAYS)
    kb = {
        "inline_keyboard": [
            [{"text": "🔍 Run a Past Search", "callback_data": "past:run"}],
            [{"text": "📊 Latest search status", "callback_data": "past:status"}],
            [{"text": f"🗓 Default lookback: {default}d — change", "callback_data": "past:setdefault"}],
            [{"text": "⬅️ Back", "callback_data": "menu:main"}],
        ]
    }
    telegram_api.send_message(
        chat_id,
        "🔍 <b>Past Search</b>\n\nA one-time search over recent posts from your "
        "source channels.",
        reply_markup=kb,
    )


def _start_past_search(chat_id: int, user_id: int) -> None:
    if db.count_channels(user_id) == 0:
        telegram_api.send_message(chat_id, "Add a source channel first (/settings).")
        return
    user = db.get_user(user_id) or {}
    default = user.get("past_search_lookback", config.DEFAULT_LOOKBACK_DAYS)
    db.set_state(user_id, "past_lookback", {})
    telegram_api.send_message(
        chat_id,
        f"How many days back should I search? Send a number "
        f"(default {default}, max {config.MAX_LOOKBACK_DAYS}).",
    )


def _parse_int(text: str) -> int | None:
    try:
        return int(text.strip())
    except ValueError:
        return None


def _receive_past_lookback(chat_id, user_id, text, data) -> None:
    days = _parse_int(text)
    if days is None or days < 1:
        telegram_api.send_message(chat_id, "Please send a whole number of days.")
        return
    if days > config.MAX_LOOKBACK_DAYS:
        telegram_api.send_message(
            chat_id,
            f"That's longer than the maximum of {config.MAX_LOOKBACK_DAYS} days. "
            "Please send a shorter period.",
        )
        return
    db.set_state(user_id, "await_profile", {"flow": "past", "past": {"lookback": days}})
    telegram_api.send_message(
        chat_id, copy.PROFILE_CHOICE, reply_markup=keyboards.profile_choice()
    )


def _receive_default_lookback(chat_id, user_id, text) -> None:
    days = _parse_int(text)
    if days is None or days < 1:
        telegram_api.send_message(chat_id, "Please send a whole number of days.")
        return
    if days > config.MAX_LOOKBACK_DAYS:
        telegram_api.send_message(
            chat_id,
            f"That's longer than the maximum of {config.MAX_LOOKBACK_DAYS} days. "
            "Please send a shorter period.",
        )
        return
    db.update_user(user_id, past_search_lookback=days)
    db.clear_state(user_id)
    telegram_api.send_message(chat_id, f"✅ Default lookback set to {days} days.")
    # Saving a new default applies it immediately by running a Past Search.
    user = db.get_user(user_id) or {}
    saved = (user.get("match_profile") or "").strip()
    if not saved:
        telegram_api.send_message(
            chat_id,
            "I couldn't run it now because your saved match profile is empty. "
            "Save one in /settings, then use /search_past.",
        )
        return
    _enqueue_past_search(chat_id, user_id, days, saved)


def _friendly_job_time(value: str | None, tz_name: str | None) -> str:
    dt = timeutil.parse(value)
    if not dt:
        return "unknown"
    zone_name = tz_name or config.DEFAULT_TIMEZONE
    local = dt.astimezone(timeutil.get_zone(zone_name))
    return local.strftime("%b %-d, %Y %H:%M") + f" ({zone_name})"


def _enqueue_past_search(chat_id, user_id, lookback, profile_text) -> None:
    job = db.enqueue_job(user_id, "past_search", {"lookback_days": lookback, "match_profile": profile_text})
    job_id = (job or {}).get("id", "?")
    n = db.count_channels(user_id)
    db.clear_state(user_id)
    logs.info("past_search.enqueued", user_id=user_id, job_id=job_id, lookback_days=lookback, sources=n)
    telegram_api.send_message(chat_id, copy.PAST_SEARCH_QUEUED.format(job_id=job_id, days=lookback, n=n))


def _show_search_status(chat_id: int, user_id: int) -> None:
    job = db.latest_job(user_id, "past_search")
    if not job:
        telegram_api.send_message(chat_id, "No Past Search jobs yet. Use /search_past to start one.")
        return

    payload = job.get("payload") or {}
    progress = payload.get("_progress") or {}
    user = db.get_user(user_id) or {}
    timezone = user.get("timezone") or config.DEFAULT_TIMEZONE
    status = job.get("status", "unknown")
    lookback = payload.get("lookback_days", "?")
    lines = [
        f"📊 <b>Latest Past Search #{job.get('id')}</b>",
        "",
        f"Status: <b>{status}</b>",
        f"Lookback: {lookback} day(s)",
        f"Created: {_friendly_job_time(job.get('created_at'), timezone)}",
    ]
    if job.get("started_at"):
        lines.append(f"Started: {_friendly_job_time(job.get('started_at'), timezone)}")
    if job.get("finished_at"):
        lines.append(f"Finished: {_friendly_job_time(job.get('finished_at'), timezone)}")
    if progress:
        stage = progress.get("stage")
        if stage:
            lines.append(f"Stage: {stage}")
        source = progress.get("current_source")
        done = progress.get("sources_done")
        total = progress.get("sources_total")
        if source or total is not None:
            lines.append(f"Sources: {done or 0}/{total or '?'}" + (f" (@{source})" if source else ""))
        scraped = progress.get("posts_written")
        if scraped is not None:
            lines.append(f"New/updated posts: {scraped}")
        matches = progress.get("matches_found")
        if matches is not None:
            lines.append(f"Matches found: {matches}")
        if progress.get("updated_at"):
            lines.append(f"Updated: {_friendly_job_time(progress.get('updated_at'), timezone)}")
    if job.get("error"):
        lines.extend(["", f"Error: <code>{job.get('error')}</code>"])

    telegram_api.send_message(chat_id, "\n".join(lines))


# --------------------------------------------------------------------------- #
# Shared "use saved / enter new" match-profile choice
# --------------------------------------------------------------------------- #
def _choose_saved_profile(chat_id: int, user_id: int) -> None:
    st = db.get_state(user_id)
    data = st["data"]
    user = db.get_user(user_id) or {}
    saved = (user.get("match_profile") or "").strip()
    if not saved:
        telegram_api.send_message(chat_id, copy.NEED_PROFILE_FOR_RUN)
        telegram_api.send_message(
            chat_id, copy.PROFILE_CHOICE, reply_markup=keyboards.profile_choice()
        )
        return
    _continue_after_profile(chat_id, user_id, data, profile_text=saved, used_saved=True)


def _receive_new_profile_for_run(chat_id, user, text, data) -> None:
    ok, err = _validate_profile(text)
    if not ok:
        telegram_api.send_message(chat_id, err)
        return
    _continue_after_profile(chat_id, user["id"], data, profile_text=text, used_saved=False)


def _continue_after_profile(chat_id, user_id, data, profile_text, used_saved: bool) -> None:
    """Resolve the run's match profile and advance the active flow."""
    flow = data.get("flow")
    if flow == "past":
        lookback = data.get("past", {}).get("lookback", config.DEFAULT_LOOKBACK_DAYS)
        _enqueue_past_search(chat_id, user_id, lookback, profile_text)
    elif flow == "alert":
        alert = data.get("alert", {})
        # used_saved -> store None so the alert tracks the saved profile; else freeze text.
        alert["profile"] = None if used_saved else profile_text
        db.set_state(user_id, "await_delivery", {"flow": "alert", "alert": alert})
        user = db.get_user(user_id) or {}
        is_dm = user.get("alert_delivery_chat_id") in (None, user_id)
        telegram_api.send_message(
            chat_id,
            "Where should I deliver matches?",
            reply_markup=keyboards.delivery_menu(is_dm),
        )
    else:
        db.clear_state(user_id)


# --------------------------------------------------------------------------- #
# Ongoing Alerts
# --------------------------------------------------------------------------- #
def _show_alerts_menu(chat_id: int, user_id: int) -> None:
    user = db.get_user(user_id) or {}
    mode = user.get("alert_mode", "off")
    desc = {
        "off": "Off",
        "interval": f"Every {user.get('alert_interval_days')} day(s) at "
                    f"{user.get('alert_delivery_time')} ({user.get('timezone')})",
        "near_live": f"Every {user.get('alert_interval_minutes')} minute(s) (Near-Live)",
    }.get(mode, "Off")
    telegram_api.send_message(
        chat_id,
        f"🔔 <b>Ongoing Alerts</b>\n\nCurrent: <b>{desc}</b>\n\nChoose a mode:",
        reply_markup=keyboards.alerts_menu(mode),
    )


def _receive_alert_days(chat_id, user_id, text, data) -> None:
    n = _parse_int(text)
    if n is None or not (config.MIN_INTERVAL_DAYS <= n <= config.MAX_INTERVAL_DAYS):
        telegram_api.send_message(
            chat_id,
            f"Please send a number between {config.MIN_INTERVAL_DAYS} and "
            f"{config.MAX_INTERVAL_DAYS}.",
        )
        return
    alert = data.get("alert", {})
    alert["days"] = n
    db.set_state(user_id, "alerts_time", {"alert": alert})
    telegram_api.send_message(
        chat_id, "What time of day should I deliver? Send it as <code>HH:MM</code> (24-hour)."
    )


def _receive_alert_time(chat_id, user_id, text, data) -> None:
    if not _TIME_RE.match(text.strip()):
        telegram_api.send_message(chat_id, "Please send a time as <code>HH:MM</code>, e.g. 09:30.")
        return
    h, m = text.strip().split(":")
    alert = data.get("alert", {})
    alert["time"] = f"{int(h):02d}:{int(m):02d}"
    db.set_state(user_id, "await_profile", {"flow": "alert", "alert": alert})
    telegram_api.send_message(
        chat_id, copy.PROFILE_CHOICE, reply_markup=keyboards.profile_choice()
    )


def _receive_alert_minutes(chat_id, user_id, text, data) -> None:
    n = _parse_int(text)
    if n is None or not (config.MIN_NEAR_LIVE_MINUTES <= n <= config.MAX_NEAR_LIVE_MINUTES):
        telegram_api.send_message(
            chat_id,
            f"Please send a number between {config.MIN_NEAR_LIVE_MINUTES} and "
            f"{config.MAX_NEAR_LIVE_MINUTES}.",
        )
        return
    alert = data.get("alert", {})
    alert["minutes"] = n
    db.set_state(user_id, "await_profile", {"flow": "alert", "alert": alert})
    telegram_api.send_message(
        chat_id, copy.PROFILE_CHOICE, reply_markup=keyboards.profile_choice()
    )


def _choose_delivery(chat_id: int, user_id: int, to_group: bool) -> None:
    st = db.get_state(user_id)
    data = st["data"]
    user = db.get_user(user_id) or {}

    if to_group:
        group_id = user.get("delivery_group_chat_id")
        if not group_id:
            telegram_api.send_message(
                chat_id,
                "To deliver to a group: add this bot to your group, then send "
                "<code>/here</code> in that group. After that, choose "
                "“A Telegram group” again. For now I'll use direct messages.",
            )
            delivery_chat = None
        else:
            delivery_chat = group_id
    else:
        delivery_chat = None  # direct message

    # Persist the delivery destination setting.
    db.update_user(user_id, alert_delivery_chat_id=delivery_chat)

    if data.get("flow") == "alert":
        _finalize_alert(chat_id, user_id, data.get("alert", {}), delivery_chat)
    else:
        db.clear_state(user_id)
        where = "this group" if delivery_chat else "direct messages"
        telegram_api.send_message(chat_id, f"📬 Delivery destination set to {where}.")


def _finalize_alert(chat_id, user_id, alert, delivery_chat) -> None:
    mode = alert.get("mode")
    now = timeutil.now_iso()
    fields: dict = {
        "alert_mode": mode,
        "alert_match_profile": alert.get("profile"),
        "alert_delivery_chat_id": delivery_chat,
    }
    if mode == "interval":
        fields.update(
            alert_interval_days=alert.get("days"),
            alert_delivery_time=alert.get("time"),
            alert_last_sent_at=now,
            alert_interval_minutes=None,
            near_live_started_at=None,
            near_live_last_checked_at=None,
        )
        summary = (
            f"every {alert.get('days')} day(s) at {alert.get('time')} "
            f"({(db.get_user(user_id) or {}).get('timezone')})"
        )
    else:  # near_live
        fields.update(
            alert_interval_minutes=alert.get("minutes"),
            near_live_started_at=now,
            near_live_last_checked_at=now,
            alert_interval_days=None,
            alert_delivery_time=None,
        )
        summary = f"every {alert.get('minutes')} minute(s) (Near-Live)"

    db.update_user(user_id, **fields)
    db.clear_state(user_id)
    where = "this group" if delivery_chat else "direct messages"
    prof = "a new profile for this alert" if alert.get("profile") else "your saved match profile"
    telegram_api.send_message(
        chat_id,
        f"🔔 Ongoing Alerts enabled: <b>{summary}</b>, using {prof}, delivered to {where}.",
    )


# --------------------------------------------------------------------------- #
# Delivery / timezone / groups
# --------------------------------------------------------------------------- #
def _show_delivery_menu(chat_id: int, user_id: int) -> None:
    user = db.get_user(user_id) or {}
    chat = user.get("alert_delivery_chat_id")
    is_dm = chat in (None, user_id)
    current = "direct messages" if is_dm else "a Telegram group"
    telegram_api.send_message(
        chat_id,
        f"📬 <b>Delivery destination</b>\n\nCurrent: <b>{current}</b>.\n\n"
        "To use a group, add this bot to it and send <code>/here</code> there first.",
        reply_markup=keyboards.delivery_menu(is_dm),
    )


def _register_group_delivery(user_id: int, chat: dict) -> None:
    user = db.get_user(user_id)
    if user is None:
        telegram_api.send_message(
            chat["id"], "Please start me in a direct message first (send /start to me)."
        )
        return
    group_id = chat["id"]
    db.update_user(user_id, delivery_group_chat_id=group_id, alert_delivery_chat_id=group_id)
    telegram_api.send_message(
        group_id,
        "✅ Matches will now be delivered to this group. You can switch back to "
        "direct messages anytime from /settings.",
    )


def _receive_timezone(chat_id, user_id, text) -> None:
    tz = text.strip()
    if not timeutil.valid_timezone(tz):
        telegram_api.send_message(
            chat_id,
            "I don't recognize that timezone. Use an IANA name like "
            "<code>Africa/Addis_Ababa</code> or <code>America/New_York</code>.",
        )
        return
    db.update_user(user_id, timezone=tz)
    db.clear_state(user_id)
    telegram_api.send_message(chat_id, f"🌍 Timezone set to <code>{tz}</code>.")


_HELP = (
    "<b>iScraper</b> watches public Telegram channels and sends you matching posts.\n\n"
    "• /settings — manage your match profile, channels, alerts, delivery, timezone\n"
    "• /search_past — one-time search over recent posts\n"
    "• /search_status — check the latest Past Search job\n"
    "• /alerts — set up ongoing alerts (every N days, or near-live every N minutes)\n"
    "• /cancel — stop the current step"
)

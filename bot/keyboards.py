"""Inline keyboard builders. Each returns a Bot API reply_markup dict."""
from __future__ import annotations


def _kb(rows: list[list[tuple[str, str]]]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row]
            for row in rows
        ]
    }


def onboarding_profile() -> dict:
    return _kb([[("Skip for now", "onb:profile_skip")]])


def add_more_or_done() -> dict:
    return _kb([[("➕ Add another", "channels:another"), ("✅ Done", "channels:done")]])


def main_menu() -> dict:
    return _kb(
        [
            [("📝 Match profile", "menu:profile"), ("📡 Source channels", "menu:channels")],
            [("🔍 Past Search", "menu:past"), ("📊 Search status", "past:status")],
            [("🔔 Ongoing Alerts", "menu:alerts")],
            [("📬 Delivery destination", "menu:delivery"), ("🌍 Timezone", "menu:tz")],
        ]
    )


def back_to_menu() -> dict:
    return _kb([[("⬅️ Back to settings", "menu:main")]])


def profile_menu(has_profile: bool) -> dict:
    rows = [[("✏️ Set / replace", "profile:set")]]
    if has_profile:
        rows.append([("🗑 Clear", "profile:clear")])
    rows.append([("⬅️ Back", "menu:main")])
    return _kb(rows)


def channels_menu(usernames: list[str]) -> dict:
    rows: list[list[tuple[str, str]]] = [[("➕ Add channels", "channels:add")]]
    for u in usernames:
        rows.append([(f"🗑 {u}", f"channels:remove:{u}")])
    rows.append([("⬅️ Back", "menu:main")])
    return _kb(rows)


def profile_choice() -> dict:
    return _kb(
        [
            [("Use saved profile", "mp:saved")],
            [("Enter a new one", "mp:new")],
            [("⬅️ Back", "menu:main")],
        ]
    )


def alerts_menu(current: str) -> dict:
    def label(text: str, mode: str) -> str:
        return ("✅ " if current == mode else "") + text

    return _kb(
        [
            [(label("Off", "off"), "alerts:off")],
            [(label("Every N days", "interval"), "alerts:interval")],
            [(label("Every N minutes (Near-Live)", "near_live"), "alerts:near")],
            [("⬅️ Back", "menu:main")],
        ]
    )


def delivery_menu(current_is_dm: bool) -> dict:
    return _kb(
        [
            [(("✅ " if current_is_dm else "") + "Direct message", "delivery:dm")],
            [(("✅ " if not current_is_dm else "") + "A Telegram group", "delivery:group")],
            [("⬅️ Back", "menu:main")],
        ]
    )

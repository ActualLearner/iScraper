"""Shared helpers for the alert/search job processors."""
from __future__ import annotations

import html

from core import config, telegram_api


def resolve_alert_profile(user: dict) -> str:
    """The match profile an Ongoing Alert should use: its scoped profile if set,
    otherwise the user's saved default. Empty string means 'no usable profile'."""
    scoped = (user.get("alert_match_profile") or "").strip()
    if scoped:
        return scoped
    return (user.get("match_profile") or "").strip()


def delivery_chat_id(user: dict) -> int:
    return user.get("alert_delivery_chat_id") or user["id"]


def _label(content: str) -> str:
    first = (content or "").strip().splitlines()[0] if content else ""
    first = first.strip()
    if len(first) > 60:
        first = first[:57].rstrip() + "…"
    return first


def match_line(post: dict) -> str:
    link = post["message_link"]
    label = _label(post.get("content", ""))
    if label:
        return f"• {html.escape(label)} — {link}"
    return f"• {link}"


def deliver_batch(chat_id: int, results: list[dict]) -> None:
    """Past Search / interval delivery: one combined, link-list message (split if long)."""
    if not results:
        return
    header = f"Found {len(results)} match{'es' if len(results) != 1 else ''}:"
    lines = [match_line(p) for p in results]
    telegram_api.send_chunked(chat_id, header, lines)


def threshold() -> float:
    return config.SIMILARITY_THRESHOLD

"""Thin synchronous wrapper over the Telegram Bot API.

Used by both the webhook (to reply to users) and the worker (to deliver matches).
Keeps everything sync + httpx so it works inside Vercel's serverless handler and
inside the worker's asyncio loop without special handling.
"""
from __future__ import annotations

from typing import Any

import httpx

from core import config

_TIMEOUT = httpx.Timeout(20.0)


def _base_url() -> str:
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")
    return f"https://api.telegram.org/bot{config.BOT_TOKEN}"


def _call(method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """POST to a Bot API method. Returns the `result` field, or None on failure."""
    try:
        resp = httpx.post(f"{_base_url()}/{method}", json=payload, timeout=_TIMEOUT)
        data = resp.json()
    except Exception:
        return None
    if not data.get("ok"):
        return None
    return data.get("result")


def send_message(
    chat_id: int | str,
    text: str,
    *,
    reply_markup: dict | None = None,
    disable_preview: bool = False,
) -> dict | None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _call("sendMessage", payload)


def edit_message_text(
    chat_id: int | str,
    message_id: int,
    text: str,
    *,
    reply_markup: dict | None = None,
    disable_preview: bool = False,
) -> dict | None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _call("editMessageText", payload)


def answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    _call("answerCallbackQuery", payload)


def get_chat(chat: str | int) -> dict | None:
    """getChat for a public @username or chat id. None if not resolvable."""
    return _call("getChat", {"chat_id": chat})


def send_chunked(chat_id: int | str, lines_header: str, lines: list[str]) -> None:
    """Send a header followed by a bullet list, splitting across messages if needed.

    Used for batched delivery (Past Search and interval alerts) where the full
    match list may exceed Telegram's per-message character limit.
    """
    limit = config.TELEGRAM_MAX_MESSAGE_CHARS - 100  # headroom for header/markup
    chunk: list[str] = []
    size = len(lines_header)
    first = True

    def flush() -> None:
        nonlocal chunk, size, first
        if not chunk and not first:
            return
        header = lines_header if first else ""
        body = "\n".join(chunk)
        text = (header + "\n\n" + body).strip() if header else body
        if text:
            send_message(chat_id, text, disable_preview=True)
        chunk = []
        size = 0
        first = False

    for line in lines:
        if size + len(line) + 1 > limit and chunk:
            flush()
        chunk.append(line)
        size += len(line) + 1
    flush()

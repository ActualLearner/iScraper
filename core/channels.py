"""Source channel input parsing, normalization, and validation.

Accepted input (per product-spec.md):
  - @channelusername
  - https://t.me/channelusername

Rejected:
  - Private invite links (https://t.me/+...  or  https://t.me/joinchat/...)
  - Bare usernames without '@'
  - Non-Telegram URLs
  - Anything that is not an accessible public channel
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core import telegram_api

# Telegram public usernames: 5-32 chars, start with a letter, [A-Za-z0-9_].
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")

# Reasons returned to the caller so the bot can pick the right copy.
PARSE_OK = "ok"
PARSE_PRIVATE = "private"          # private invite link
PARSE_INVALID = "invalid"         # wrong format / bare username / non-telegram


@dataclass
class ParseResult:
    reason: str                    # PARSE_OK | PARSE_PRIVATE | PARSE_INVALID
    username: str | None = None    # normalized (lowercase, no '@') when reason == PARSE_OK


def parse_channel_input(token: str) -> ParseResult:
    """Parse one whitespace-delimited channel token into a normalized username."""
    raw = (token or "").strip()
    if not raw:
        return ParseResult(PARSE_INVALID)

    lowered = raw.lower()

    # Private invite links are explicitly rejected with a dedicated message.
    if (
        lowered.startswith("https://t.me/+")
        or lowered.startswith("http://t.me/+")
        or lowered.startswith("t.me/+")
        or "t.me/joinchat/" in lowered
        or lowered.startswith("@+")
    ):
        return ParseResult(PARSE_PRIVATE)

    candidate: str | None = None

    if raw.startswith("@"):
        candidate = raw[1:]
    elif "t.me/" in lowered:
        # Tolerate http(s):// and a missing scheme; reject other hosts.
        after = raw.split("t.me/", 1)[1]
        after = after.lstrip("/")
        if after.startswith("s/"):          # web preview links: t.me/s/name
            after = after[2:]
        # Take only the first path segment, drop query/fragment.
        after = re.split(r"[/?#]", after, 1)[0]
        candidate = after
    else:
        # Bare username without '@', a non-Telegram URL, or junk: rejected.
        return ParseResult(PARSE_INVALID)

    if candidate and _USERNAME_RE.match(candidate):
        return ParseResult(PARSE_OK, username=candidate.lower())
    return ParseResult(PARSE_INVALID)


@dataclass
class ValidationResult:
    ok: bool
    username: str | None = None
    reason: str | None = None      # 'private' | 'invalid' | 'inaccessible'


def validate_channel_input(token: str) -> ValidationResult:
    """Parse, then confirm the channel is a public channel the bot can resolve.

    Uses Bot API getChat as a synchronous, responsive proxy for "the scraper
    account can access this public channel": a public channel resolvable by the
    bot is also readable by a normal user account (the Telethon scraper). Groups,
    supergroups, users, bots, and non-existent handles are rejected.
    """
    parsed = parse_channel_input(token)
    if parsed.reason != PARSE_OK or not parsed.username:
        return ValidationResult(False, reason=parsed.reason)

    chat = telegram_api.get_chat(f"@{parsed.username}")
    if chat is None:
        return ValidationResult(False, username=parsed.username, reason="inaccessible")
    if chat.get("type") != "channel":
        # Public groups resolve as 'supergroup'; only broadcast channels qualify.
        return ValidationResult(False, username=parsed.username, reason="inaccessible")
    return ValidationResult(True, username=parsed.username)


def message_link(channel_username: str, message_id: int) -> str:
    return f"https://t.me/{channel_username}/{message_id}"

"""Telethon (MTProto) client built from a string session.

The scraper account is a normal Telegram *user* account, which can read public
channel history without joining. Generate the session once with
`python scripts/login_telethon.py` and store it as the TELETHON_SESSION secret.
"""
from __future__ import annotations

from telethon import TelegramClient
from telethon.sessions import StringSession

from core import config


async def connect() -> TelegramClient:
    config.require("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELETHON_SESSION")
    client = TelegramClient(
        StringSession(config.TELETHON_SESSION),
        config.TELEGRAM_API_ID,
        config.TELEGRAM_API_HASH,
    )
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError(
            "Telethon session is not authorized. Regenerate it with "
            "scripts/login_telethon.py."
        )
    return client

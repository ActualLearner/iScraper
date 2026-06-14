"""Generate a Telethon string session for the scraper user account.

Run this ONCE, locally, on a machine where you can receive the Telegram login
code (it's an interactive login):

    pip install -r requirements.txt
    python scripts/login_telethon.py

You'll be asked for the phone number of the account that will read the public
channels, the code Telegram sends you, and your 2FA password if enabled. It
prints a TELETHON_SESSION string — copy it into your .env and into the GitHub
Actions secret. Treat it like a password: it grants full access to that account.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telethon import TelegramClient
from telethon.sessions import StringSession

from core import config


async def main() -> None:
    if not config.TELEGRAM_API_ID or not config.TELEGRAM_API_HASH:
        raise SystemExit(
            "Set TELEGRAM_API_ID and TELEGRAM_API_HASH in your .env first "
            "(get them from https://my.telegram.org)."
        )

    async with TelegramClient(
        StringSession(), config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH
    ) as client:
        await client.start()  # prompts for phone, code, and 2FA password
        session = client.session.save()
        me = await client.get_me()
        print("\nLogged in as:", getattr(me, "username", None) or me.first_name)
        print("\n=== TELETHON_SESSION (copy this whole line) ===\n")
        print(session)
        print("\n===============================================")


if __name__ == "__main__":
    asyncio.run(main())

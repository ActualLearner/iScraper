"""Register (or delete) the Telegram webhook pointing at your Vercel deployment.

Usage:
    # set the webhook (reads BOT_TOKEN + WEBHOOK_SECRET from env/.env)
    python scripts/set_webhook.py https://your-project.vercel.app

    # remove it (e.g. to switch back to local polling for debugging)
    python scripts/set_webhook.py --delete

The webhook URL is <base>/api/webhook and the WEBHOOK_SECRET is registered as
the secret_token, so the serverless function can reject anything that isn't from
Telegram.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from core import config, telegram_api


def _api(method: str, payload: dict) -> dict:
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set (check your .env).")
    resp = httpx.post(
        f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}",
        json=payload,
        timeout=30,
    )
    return resp.json()


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit(__doc__)

    if argv[0] in ("--delete", "-d"):
        print(_api("deleteWebhook", {"drop_pending_updates": False}))
        return

    base = argv[0].rstrip("/")
    url = f"{base}/api/webhook"
    payload: dict = {"url": url, "allowed_updates": ["message", "callback_query"]}
    if config.WEBHOOK_SECRET:
        payload["secret_token"] = config.WEBHOOK_SECRET
    else:
        print("WARNING: WEBHOOK_SECRET is not set; the webhook will be unauthenticated.")
    print(f"Setting webhook -> {url}")
    print(_api("setWebhook", payload))
    print("Installing command menu")
    print(_api("setMyCommands", {"commands": telegram_api.BOT_COMMANDS}))
    print(_api("setChatMenuButton", {"menu_button": {"type": "commands"}}))
    print(_api("getWebhookInfo", {}))


if __name__ == "__main__":
    main(sys.argv[1:])

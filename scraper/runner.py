"""Scheduled worker entrypoint (run by GitHub Actions every ~5 minutes).

One pass: open one Telethon connection, then
  1. drain queued Past Search jobs,
  2. deliver any due Interval Alerts,
  3. deliver any due Near-Live Alerts.

Each stage is isolated so one failure can't sink the others. Run locally with
`python -m scraper.runner` once your .env is filled in.
"""
from __future__ import annotations

import asyncio

from core import config
from jobs import interval_alerts, near_live_alerts, past_search
from scraper import telethon_client


async def main() -> None:
    config.require(
        "BOT_TOKEN",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_KEY",
        "GEMINI_API_KEY",
    )

    client = await telethon_client.connect()
    try:
        for label, coro in (
            ("past_search", past_search.run_pending(client)),
            ("interval_alerts", interval_alerts.run_due(client)),
            ("near_live_alerts", near_live_alerts.run_due(client)),
        ):
            try:
                await coro
            except Exception as exc:
                print(f"[runner] stage {label} failed: {exc!r}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

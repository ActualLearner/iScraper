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

from core import config, logs
from jobs import interval_alerts, near_live_alerts, past_search
from scraper import telethon_client


async def main() -> None:
    config.require(
        "BOT_TOKEN",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_KEY",
        "GEMINI_API_KEY",
    )

    logs.info("runner.start")
    client = await telethon_client.connect()
    try:
        for label, coro in (
            ("past_search", past_search.run_pending(client)),
            ("interval_alerts", interval_alerts.run_due(client)),
            ("near_live_alerts", near_live_alerts.run_due(client)),
        ):
            try:
                logs.info("runner.stage_start", stage=label)
                await coro
                logs.info("runner.stage_done", stage=label)
            except Exception as exc:
                logs.exception("runner.stage_failed", exc, stage=label)
    finally:
        await client.disconnect()
        logs.info("runner.done")


if __name__ == "__main__":
    asyncio.run(main())

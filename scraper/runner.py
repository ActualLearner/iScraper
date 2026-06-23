"""Scheduled worker entrypoint.

One pass: open one Telethon connection, then
  1. drain queued Past Search jobs,
  2. deliver any due Interval Alerts,
  3. deliver any due Near-Live Alerts.

Each stage is isolated so one failure can't sink the others. Run locally with
`python -m scraper.runner` once your .env is filled in.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from typing import TypeVar

from core import config, logs
from jobs import interval_alerts, near_live_alerts, past_search
from scraper import telethon_client

T = TypeVar("T")


async def _with_timeout(awaitable: Awaitable[T], timeout_seconds: float) -> T:
    if timeout_seconds and timeout_seconds > 0:
        return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
    return await awaitable


async def _run_once() -> None:
    config.require(
        "BOT_TOKEN",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_KEY",
    )

    logs.info(
        "runner.start",
        worker_run_timeout_seconds=config.WORKER_RUN_TIMEOUT_SECONDS or "none",
        worker_stage_timeout_seconds=config.WORKER_STAGE_TIMEOUT_SECONDS or "none",
        past_search_jobs_per_run=config.PAST_SEARCH_JOBS_PER_RUN,
    )
    failed_stages: list[str] = []
    client = await telethon_client.connect()
    try:
        stages = (
            ("past_search", past_search.run_pending),
            ("interval_alerts", interval_alerts.run_due),
            ("near_live_alerts", near_live_alerts.run_due),
        )
        for label, run_stage in stages:
            started = time.perf_counter()
            try:
                logs.info("runner.stage_start", stage=label)
                await _with_timeout(run_stage(client), config.WORKER_STAGE_TIMEOUT_SECONDS)
                logs.info(
                    "runner.stage_done",
                    stage=label,
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                )
            except Exception as exc:
                failed_stages.append(label)
                logs.exception(
                    "runner.stage_failed",
                    exc,
                    stage=label,
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                )
    finally:
        await client.disconnect()
        logs.info("runner.done", failed_stages=failed_stages)
    if failed_stages:
        raise RuntimeError(f"Worker stage(s) failed: {failed_stages}")


async def main() -> None:
    await _with_timeout(_run_once(), config.WORKER_RUN_TIMEOUT_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())

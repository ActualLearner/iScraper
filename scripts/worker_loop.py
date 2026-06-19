"""Long-running worker loop for platforms with persistent worker dynos.

Heroku worker dynos should keep a foreground process alive. This loop runs one
normal worker pass, sleeps, and repeats. The one-shot runner remains available
for GitHub Actions and local manual runs.
"""
from __future__ import annotations

import asyncio
import signal
import time

from core import config, logs
from scraper import runner

_STOP = False


def _request_stop(signum: int, _frame) -> None:
    global _STOP
    _STOP = True
    logs.info("worker_loop.stop_requested", signal=signum)


def _sleep_interruptibly(seconds: float) -> None:
    deadline = time.monotonic() + max(seconds, 0.0)
    while not _STOP and time.monotonic() < deadline:
        time.sleep(min(1.0, deadline - time.monotonic()))


def main() -> None:
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    interval = max(config.WORKER_LOOP_INTERVAL_SECONDS, 1.0)
    logs.info("worker_loop.start", interval_seconds=interval)
    while not _STOP:
        started = time.perf_counter()
        try:
            asyncio.run(runner.main())
            logs.info(
                "worker_loop.pass_done",
                elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            )
        except Exception as exc:
            logs.exception(
                "worker_loop.pass_failed",
                exc,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            )
        if not _STOP:
            _sleep_interruptibly(interval)
    logs.info("worker_loop.done")


if __name__ == "__main__":
    main()

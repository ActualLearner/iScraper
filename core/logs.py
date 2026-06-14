"""Small structured stdout logger for Vercel and GitHub Actions logs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def _clean(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    return str(value)


def log(level: str, event: str, **fields: Any) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "event": event,
        **{k: _clean(v) for k, v in fields.items()},
    }
    print(json.dumps(row, separators=(",", ":"), sort_keys=True), flush=True)


def info(event: str, **fields: Any) -> None:
    log("info", event, **fields)


def warning(event: str, **fields: Any) -> None:
    log("warning", event, **fields)


def error(event: str, **fields: Any) -> None:
    log("error", event, **fields)


def exception(event: str, exc: Exception, **fields: Any) -> None:
    error(event, error_type=type(exc).__name__, error=repr(exc), **fields)

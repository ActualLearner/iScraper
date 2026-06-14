"""Timezone-aware time helpers (stdlib zoneinfo, no extra deps)."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def now_iso() -> str:
    return iso(now_utc())


def parse(value: str | None) -> datetime | None:
    """Parse an ISO timestamp (as returned by Supabase) into an aware UTC datetime."""
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_zone(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, Exception):
        return timezone.utc


def local_now(tz_name: str) -> datetime:
    return now_utc().astimezone(get_zone(tz_name))


def valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False

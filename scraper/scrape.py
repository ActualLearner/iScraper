"""Walk a source channel and keep stored source posts up to date.

Storage is the side effect; matching happens afterwards via the DB search RPC.
Telegram albums are stored as one logical source post: captions and OCR text from
image messages in the album are combined, normalized, embedded, and delivered
through one canonical message link.
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any

from telethon import TelegramClient

from core import channels as ch
from core import config, db, embeddings, logs, ocr, timeutil
from core.text import normalize_content

_OCR_UNAVAILABLE_LOGGED = False


@dataclass
class _ScrapeStats:
    written: int = 0
    write_failures: int = 0
    skipped_empty: int = 0
    skipped_unchanged: int = 0
    ocr_attempted: int = 0
    ocr_skipped: int = 0
    ocr_failed: int = 0
    db_seconds: float = 0.0
    ocr_seconds: float = 0.0

    def add(self, other: "_ScrapeStats") -> None:
        for field in fields(self):
            setattr(self, field.name, getattr(self, field.name) + getattr(other, field.name))

    def log_fields(self) -> dict[str, int | float]:
        return {
            "written": self.written,
            "write_failures": self.write_failures,
            "skipped_empty": self.skipped_empty,
            "skipped_unchanged": self.skipped_unchanged,
            "ocr_attempted": self.ocr_attempted,
            "ocr_skipped": self.ocr_skipped,
            "ocr_failed": self.ocr_failed,
            "db_ms": round(self.db_seconds * 1000, 1),
            "ocr_ms": round(self.ocr_seconds * 1000, 1),
        }


def _elapsed(start: float) -> float:
    return time.perf_counter() - start


def _group_key(message: Any) -> str:
    grouped_id = getattr(message, "grouped_id", None)
    if grouped_id is not None:
        return f"album:{grouped_id}"
    return f"message:{message.id}"


def _message_text(message: Any) -> str:
    return (getattr(message, "message", None) or "").strip()


def _is_image_message(message: Any) -> bool:
    if getattr(message, "photo", None):
        return True
    document = getattr(message, "document", None)
    mime_type = getattr(document, "mime_type", "") if document else ""
    return bool(mime_type and mime_type.startswith("image/"))


def _combine_unique_text(messages: list[Any]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for message in sorted(messages, key=lambda m: m.id):
        text = _message_text(message)
        if text and text not in seen:
            parts.append(text)
            seen.add(text)
    return "\n\n".join(parts).strip()


def _latest_edit(messages: list[Any]) -> datetime | None:
    dates = [m.edit_date for m in messages if getattr(m, "edit_date", None)]
    return max(dates) if dates else None


def _log_ocr_unavailable_once(reason: str) -> None:
    global _OCR_UNAVAILABLE_LOGGED
    if _OCR_UNAVAILABLE_LOGGED:
        return
    _OCR_UNAVAILABLE_LOGGED = True
    logs.warning("scrape.ocr_unavailable", reason=reason)


def _limited_image_messages(messages: list[Any]) -> tuple[list[Any], int]:
    image_messages = [m for m in sorted(messages, key=lambda m: m.id) if _is_image_message(m)]
    limit = config.OCR_MAX_IMAGES_PER_POST
    if limit > 0:
        return image_messages[:limit], max(len(image_messages) - limit, 0)
    return image_messages, 0


async def _download_media(message: Any, tmpdir: str) -> str | None:
    timeout = config.OCR_DOWNLOAD_TIMEOUT_SECONDS
    download = message.download_media(file=tmpdir)
    if timeout and timeout > 0:
        return await asyncio.wait_for(download, timeout=timeout)
    return await download


async def _ocr_image_messages(messages: list[Any], username: str) -> tuple[str, _ScrapeStats]:
    stats = _ScrapeStats()
    selected, skipped_by_limit = _limited_image_messages(messages)
    stats.ocr_skipped += skipped_by_limit
    if not selected:
        return "", stats

    setup_error = ocr.availability_error()
    if setup_error:
        stats.ocr_skipped += len(selected)
        _log_ocr_unavailable_once(setup_error)
        return "", stats

    texts: list[str] = []
    with tempfile.TemporaryDirectory(prefix="iscraper-ocr-") as tmpdir:
        for message in selected:
            stats.ocr_attempted += 1
            started = time.perf_counter()
            try:
                path = await _download_media(message, tmpdir)
                if not path:
                    stats.ocr_skipped += 1
                    continue
                text = await asyncio.to_thread(ocr.image_to_text, path)
            except asyncio.TimeoutError as exc:
                stats.ocr_failed += 1
                logs.exception(
                    "scrape.ocr_download_timeout",
                    exc,
                    source=username,
                    message_id=getattr(message, "id", None),
                    timeout_seconds=config.OCR_DOWNLOAD_TIMEOUT_SECONDS,
                )
                continue
            except ocr.OcrSkipped as exc:
                stats.ocr_skipped += 1
                logs.warning(
                    "scrape.ocr_skipped",
                    source=username,
                    message_id=getattr(message, "id", None),
                    reason=str(exc),
                )
                continue
            except ocr.OcrUnavailable as exc:
                stats.ocr_skipped += 1
                _log_ocr_unavailable_once(str(exc))
                continue
            except Exception as exc:
                stats.ocr_failed += 1
                logs.exception(
                    "scrape.ocr_failed",
                    exc,
                    source=username,
                    message_id=getattr(message, "id", None),
                )
                continue
            finally:
                stats.ocr_seconds += _elapsed(started)

            if text:
                texts.append(text)

    return "\n\n".join(texts).strip(), stats


def _build_source_post_base(username: str, messages: list[Any]) -> dict[str, Any]:
    ordered = sorted(messages, key=lambda m: m.id)
    canonical = next((m for m in ordered if _message_text(m)), ordered[0])
    caption = _combine_unique_text(ordered)
    image_count = sum(1 for m in ordered if _is_image_message(m))
    grouped_id = getattr(canonical, "grouped_id", None)
    edited_at = _latest_edit(ordered)

    return {
        "ordered": ordered,
        "channel_username": username,
        "message_id": canonical.id,
        "message_link": ch.message_link(username, canonical.id),
        "album_grouped_id": str(grouped_id) if grouped_id is not None else None,
        "posted_at": timeutil.iso(canonical.date) if canonical.date else None,
        "edited_at": timeutil.iso(edited_at) if edited_at else None,
        "caption": caption or None,
        "image_count": image_count,
    }


def _same_stored_post(existing: dict, base: dict[str, Any]) -> bool:
    same_caption = normalize_content(existing.get("caption") or "") == normalize_content(base["caption"] or "")
    same_image_count = int(existing.get("image_count") or 0) == base["image_count"]
    same_group = (existing.get("album_grouped_id") or None) == base["album_grouped_id"]
    same_edited = timeutil.parse(existing.get("edited_at")) == timeutil.parse(base["edited_at"])
    return same_caption and same_image_count and same_group and same_edited


def _should_ocr(base: dict[str, Any]) -> bool:
    if not base["image_count"]:
        return False
    if not config.OCR_ENABLED:
        return False
    if config.OCR_SKIP_WHEN_CAPTION_PRESENT and base.get("caption"):
        return False
    return True


def _post_payload(base: dict[str, Any], content: str, normalized: str, image_text: str) -> dict[str, Any]:
    return {
        "channel_username": base["channel_username"],
        "message_id": base["message_id"],
        "message_link": base["message_link"],
        "album_grouped_id": base["album_grouped_id"],
        "posted_at": base["posted_at"],
        "edited_at": base["edited_at"],
        "caption": base["caption"],
        "image_text": image_text or None,
        "image_count": base["image_count"],
        "content": content,
        "normalized_content": normalized,
    }


async def _process_groups(username: str, grouped_messages: list[list[Any]]) -> _ScrapeStats:
    stats = _ScrapeStats()
    bases: list[dict[str, Any]] = []
    for messages in grouped_messages:
        base = _build_source_post_base(username, messages)
        if not base["caption"] and base["image_count"] == 0:
            stats.skipped_empty += 1
            continue
        bases.append(base)

    if not bases:
        return stats

    db_started = time.perf_counter()
    existing_by_id = db.posts_by_message_ids(username, [b["message_id"] for b in bases])
    stats.db_seconds += _elapsed(db_started)

    inserts: list[dict[str, Any]] = []
    for base in bases:
        existing = existing_by_id.get(int(base["message_id"]))
        if existing is not None and _same_stored_post(existing, base):
            stats.skipped_unchanged += 1
            continue

        image_text = ""
        if _should_ocr(base):
            image_text, ocr_stats = await _ocr_image_messages(base["ordered"], username)
            stats.add(ocr_stats)
            if not image_text and existing is not None:
                image_text = existing.get("image_text") or ""
        elif existing is not None:
            image_text = existing.get("image_text") or ""

        parts = [part for part in (image_text, base["caption"]) if part]
        if not parts:
            stats.skipped_empty += 1
            continue

        content = "\n\n".join(parts)
        normalized = normalize_content(content)
        payload = _post_payload(base, content, normalized, image_text)

        if existing is None:
            inserts.append({**payload, "embedding": None})
            continue

        try:
            db_started = time.perf_counter()
            payload["scraped_at"] = timeutil.now_iso()
            if existing["normalized_content"] != normalized:
                payload["embedding"] = None
            db.update_post(existing["id"], **payload)
            stats.db_seconds += _elapsed(db_started)
            stats.written += 1
        except Exception as exc:
            stats.write_failures += 1
            logs.exception("scrape.write_failed", exc, source=username)

    if inserts:
        db_started = time.perf_counter()
        try:
            db.insert_posts(inserts)
            stats.written += len(inserts)
        except Exception as exc:
            logs.exception("scrape.batch_insert_failed", exc, source=username, posts=len(inserts))
            for post in inserts:
                try:
                    db.insert_post(post)
                    stats.written += 1
                except Exception as single_exc:
                    stats.write_failures += 1
                    logs.exception("scrape.write_failed", single_exc, source=username)
        finally:
            stats.db_seconds += _elapsed(db_started)

    return stats


def _batch_size() -> int:
    return max(config.SCRAPE_GROUP_BATCH_SIZE, 1)


async def scrape_channel(
    client: TelegramClient,
    username: str,
    boundary: datetime,
    max_messages: int | None = None,
    *,
    job_id: int | None = None,
) -> int:
    """Bring `username` up to date for posts at/after `boundary` (UTC).

    Returns the number of posts inserted or refreshed. Channels that can't be
    accessed are skipped (logged), so one bad channel never fails a whole run.
    """
    groups: dict[str, list[Any]] = {}
    active_key: str | None = None
    cap = max_messages if max_messages is not None else config.SCRAPE_MAX_MESSAGES
    limit = cap if cap and cap > 0 else None
    scanned = 0
    processed_groups = 0
    hit_boundary = False
    stats = _ScrapeStats()
    started = time.perf_counter()
    processing_seconds = 0.0

    async def flush(final: bool = False) -> None:
        nonlocal active_key, groups, processed_groups, processing_seconds, stats
        if not groups:
            return
        keys = list(groups.keys()) if final else [k for k in list(groups.keys()) if k != active_key]
        if not keys:
            return
        batch = [groups.pop(k) for k in keys]
        processed_groups += len(batch)
        process_started = time.perf_counter()
        batch_stats = await _process_groups(username, batch)
        processing_seconds += _elapsed(process_started)
        stats.add(batch_stats)
        logs.info(
            "scrape.batch_done",
            source=username,
            groups=len(batch),
            processed_groups=processed_groups,
            elapsed_ms=round(_elapsed(process_started) * 1000, 1),
            **batch_stats.log_fields(),
        )
        if job_id is not None:
            db.update_job_progress(
                job_id,
                stage="scraping",
                current_source=username,
                messages_scanned=scanned,
                source_groups=processed_groups,
                posts_written=stats.written,
            )

    logs.info(
        "scrape.channel_start",
        source=username,
        boundary=timeutil.iso(boundary),
        max_messages=limit or "boundary",
        group_batch_size=_batch_size(),
        ocr_enabled=config.OCR_ENABLED,
        ocr_max_images_per_post=config.OCR_MAX_IMAGES_PER_POST,
    )
    iterator = client.iter_messages(username, limit=limit).__aiter__()
    while True:
        try:
            message = await iterator.__anext__()
        except StopAsyncIteration:
            break
        except Exception as exc:
            logs.exception("scrape.channel_failed", exc, source=username)
            return 0

        scanned += 1
        if config.SCRAPE_PROGRESS_EVERY and scanned % config.SCRAPE_PROGRESS_EVERY == 0:
            progress = {
                "source": username,
                "messages_scanned": scanned,
                "pending_groups": len(groups),
                "processed_groups": processed_groups,
                "posts_written": stats.written,
                "latest_message_id": getattr(message, "id", None),
            }
            logs.info("scrape.channel_progress", **progress)
            if job_id is not None:
                db.update_job_progress(
                    job_id,
                    stage="scraping",
                    current_source=username,
                    messages_scanned=scanned,
                    source_groups=processed_groups + len(groups),
                    posts_written=stats.written,
                )
        posted_at = message.date  # aware UTC
        if posted_at and posted_at < boundary:
            hit_boundary = True
            break  # reached the requested window boundary

        key = _group_key(message)
        groups.setdefault(key, []).append(message)
        if active_key is None or key != active_key:
            active_key = key
        if len(groups) > _batch_size():
            await flush()

    await flush(final=True)
    if stats.write_failures:
        raise RuntimeError(f"{stats.write_failures} source post write(s) failed for @{username}")

    db_started = time.perf_counter()
    backlog = db.count_unembedded_posts([username], timeutil.iso(boundary))
    stats.db_seconds += _elapsed(db_started)
    total_seconds = _elapsed(started)
    telegram_seconds = max(total_seconds - processing_seconds, 0.0)
    logs.info(
        "scrape.channel_done",
        source=username,
        messages_scanned=scanned,
        hit_boundary=hit_boundary,
        groups=processed_groups,
        embedding_backlog=backlog,
        elapsed_ms=round(total_seconds * 1000, 1),
        telegram_scan_ms=round(telegram_seconds * 1000, 1),
        processing_ms=round(processing_seconds * 1000, 1),
        **stats.log_fields(),
    )
    return stats.written


async def scrape_user_channels(
    client: TelegramClient,
    usernames: list[str],
    boundary: datetime,
    *,
    job_id: int | None = None,
) -> int:
    total_written = 0
    total = len(usernames)
    for index, username in enumerate(usernames, start=1):
        if job_id is not None:
            db.update_job_progress(
                job_id,
                stage="scraping",
                current_source=username,
                sources_done=index - 1,
                sources_total=total,
                posts_written=total_written,
            )
        written = await scrape_channel(client, username, boundary, job_id=job_id)
        total_written += written
        if job_id is not None:
            db.update_job_progress(
                job_id,
                stage="scraping",
                current_source=username,
                sources_done=index,
                sources_total=total,
                posts_written=total_written,
            )
    return total_written


def _estimated_input_tokens(text: str) -> int:
    # Conservative approximation for quota pacing: English text is commonly
    # around 4 chars/token, but OCR output can be noisy, so use 3 chars/token.
    return max(1, (len(text or "") + 2) // 3)


async def embed_pending_posts(
    channel_usernames: list[str],
    posted_after: str | None = None,
    *,
    max_count: int | None = None,
    job_id: int | None = None,
) -> dict[str, object]:
    """Fill missing source-post embeddings gradually.

    Scraping stores posts first with `embedding = null`; this function turns that
    durable backlog into vectors at a controlled pace so large Past Searches can
    resume across worker runs when Gemini quota is tight.
    """
    if not channel_usernames:
        return {"embedded": 0, "remaining": 0, "quota_exhausted": False}

    limit_value = config.EMBEDDING_MAX_PER_RUN if max_count is None else max_count
    daily_cap = config.EMBEDDING_REQUESTS_PER_DAY - config.EMBEDDING_DAILY_REQUEST_RESERVE
    if daily_cap > 0:
        limit_value = min(limit_value, daily_cap) if limit_value and limit_value > 0 else daily_cap
    limit = limit_value if limit_value and limit_value > 0 else None
    remaining_before = db.count_unembedded_posts(channel_usernames, posted_after)
    if remaining_before <= 0:
        return {"embedded": 0, "remaining": 0, "quota_exhausted": False}

    rows = db.list_unembedded_posts(channel_usernames, posted_after, limit=limit)
    request_delay = 60.0 / config.EMBEDDING_REQUESTS_PER_MINUTE if config.EMBEDDING_REQUESTS_PER_MINUTE > 0 else 0.0
    token_budget = max(config.EMBEDDING_INPUT_TOKENS_PER_MINUTE, 0)
    started = time.perf_counter()
    logs.info(
        "embedding.backlog_start",
        remaining=remaining_before,
        selected=len(rows),
        limit=limit or "none",
        rpm=config.EMBEDDING_REQUESTS_PER_MINUTE or "unlimited",
        tpm=token_budget or "unlimited",
        rpd=config.EMBEDDING_REQUESTS_PER_DAY or "unlimited",
        daily_reserve=config.EMBEDDING_DAILY_REQUEST_RESERVE,
    )
    if job_id is not None:
        db.update_job_progress(
            job_id,
            stage="embedding_posts",
            embedding_backlog=remaining_before,
            embeddings_selected=len(rows),
        )

    embedded = 0
    quota_exhausted = False
    retry_after: str | None = None
    window_started = timeutil.now_utc()
    window_tokens = 0
    for index, row in enumerate(rows, start=1):
        content = row.get("normalized_content") or ""
        estimated_tokens = _estimated_input_tokens(content)
        if token_budget and window_tokens + estimated_tokens > token_budget:
            elapsed = (timeutil.now_utc() - window_started).total_seconds()
            sleep_for = max(60.0 - elapsed, 0.0)
            logs.info(
                "embedding.token_cooldown",
                sleep_seconds=round(sleep_for, 3),
                window_tokens=window_tokens,
                next_tokens=estimated_tokens,
                tpm=token_budget,
            )
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            window_started = timeutil.now_utc()
            window_tokens = 0

        embed_started = time.perf_counter()
        try:
            vector = embeddings.embed_document(content)
        except Exception as exc:
            if embeddings.is_quota_error(exc):
                quota_exhausted = True
                retry_after = embeddings.quota_retry_after_iso(exc)
                logs.exception(
                    "embedding.quota_exhausted",
                    exc,
                    post_id=row.get("id"),
                    source=row.get("channel_username"),
                    embedded=embedded,
                    retry_after=retry_after,
                )
                break
            logs.exception(
                "embedding.post_failed",
                exc,
                post_id=row.get("id"),
                source=row.get("channel_username"),
            )
            raise

        db.update_post(row["id"], embedding=vector)
        embedded += 1
        window_tokens += estimated_tokens
        if embedded == 1 or embedded % 10 == 0:
            progress_remaining = max(remaining_before - embedded, 0)
            logs.info(
                "embedding.progress",
                embedded=embedded,
                remaining_estimate=progress_remaining,
                source=row.get("channel_username"),
                post_id=row.get("id"),
                embed_ms=round(_elapsed(embed_started) * 1000, 1),
            )
            if job_id is not None:
                db.update_job_progress(
                    job_id,
                    stage="embedding_posts",
                    embedded_posts=embedded,
                    embedding_backlog=progress_remaining,
                    current_source=row.get("channel_username"),
                )
        if request_delay > 0 and index < len(rows):
            await asyncio.sleep(request_delay)

    remaining_after = db.count_unembedded_posts(channel_usernames, posted_after)
    stats: dict[str, object] = {
        "embedded": embedded,
        "remaining": remaining_after,
        "quota_exhausted": quota_exhausted,
        "elapsed_ms": round(_elapsed(started) * 1000, 1),
    }
    if retry_after:
        stats["retry_after"] = retry_after
    logs.info("embedding.backlog_done", **stats)
    if job_id is not None:
        progress = {
            "stage": "embedding_posts",
            "embedded_posts": embedded,
            "embedding_backlog": remaining_after,
        }
        if retry_after:
            progress["next_attempt_after"] = retry_after
            progress["message"] = "Gemini quota exhausted; waiting to retry."
        db.update_job_progress(job_id, **progress)
    return stats

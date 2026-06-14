"""Walk a source channel and keep stored source posts up to date.

Storage is the side effect; matching happens afterwards via the DB search RPC.
Telegram albums are stored as one logical source post: captions and OCR text from
all image messages in the album are combined, normalized, embedded, and delivered
through one canonical message link.
"""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime
from typing import Any

from telethon import TelegramClient

from core import channels as ch
from core import db, embeddings, ocr, timeutil
from core.text import normalize_content


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


async def _ocr_image_messages(messages: list[Any], username: str) -> tuple[str, int]:
    image_messages = [m for m in sorted(messages, key=lambda m: m.id) if _is_image_message(m)]
    if not image_messages:
        return "", 0

    texts: list[str] = []
    with tempfile.TemporaryDirectory(prefix="iscraper-ocr-") as tmpdir:
        for message in image_messages:
            try:
                path = await message.download_media(file=tmpdir)
                if not path:
                    continue
                text = await asyncio.to_thread(ocr.image_to_text, path)
            except Exception as exc:
                print(f"[scrape] OCR failed for @{username}/{message.id}: {exc!r}")
                continue
            if text:
                texts.append(text)

    return "\n\n".join(texts).strip(), len(image_messages)


async def _build_source_post_base(username: str, messages: list[Any]) -> dict[str, Any]:
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


async def scrape_channel(
    client: TelegramClient,
    username: str,
    boundary: datetime,
    max_messages: int = 1000,
) -> int:
    """Bring `username` up to date for posts at/after `boundary` (UTC).

    Returns the number of posts inserted or refreshed. Channels that can't be
    accessed are skipped (logged), so one bad channel never fails a whole run.
    """
    embed_texts: list[str] = []
    embed_targets: list[tuple[str, object]] = []  # ("insert", dict) | ("update", (id, fields))
    groups: dict[str, list[Any]] = {}

    try:
        async for message in client.iter_messages(username, limit=max_messages):
            posted_at = message.date  # aware UTC
            if posted_at and posted_at < boundary:
                break  # reached the requested window boundary

            groups.setdefault(_group_key(message), []).append(message)

        for messages in groups.values():
            base = await _build_source_post_base(username, messages)
            if not base["caption"] and base["image_count"] == 0:
                continue

            existing = db.get_post(username, base["message_id"])
            if existing is not None:
                same_caption = normalize_content(existing.get("caption") or "") == normalize_content(base["caption"] or "")
                same_image_count = int(existing.get("image_count") or 0) == base["image_count"]
                same_group = (existing.get("album_grouped_id") or None) == base["album_grouped_id"]
                same_edited = timeutil.parse(existing.get("edited_at")) == timeutil.parse(base["edited_at"])
                if same_caption and same_image_count and same_group and same_edited:
                    continue

            image_text = ""
            if base["image_count"]:
                image_text, _ = await _ocr_image_messages(messages, username)
                if not image_text and existing is not None:
                    image_text = existing.get("image_text") or ""

            parts = [part for part in (image_text, base["caption"]) if part]
            if not parts:
                continue

            content = "\n\n".join(parts)
            normalized = normalize_content(content)

            if existing is None:
                row = {
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
                embed_texts.append(normalized)
                embed_targets.append(("insert", row))
            else:
                fields = {
                    "content": content,
                    "normalized_content": normalized,
                    "caption": base["caption"],
                    "image_text": image_text or None,
                    "image_count": base["image_count"],
                    "album_grouped_id": base["album_grouped_id"],
                    "posted_at": base["posted_at"],
                    "message_link": base["message_link"],
                    "edited_at": base["edited_at"],
                    "scraped_at": timeutil.now_iso(),
                }
                if existing["normalized_content"] != normalized:
                    embed_texts.append(normalized)
                    embed_targets.append(("update", (existing["id"], fields)))
                else:
                    db.update_post(existing["id"], **fields)
            # else: unchanged -> skip re-embedding (cheap direct comparison)
    except Exception as exc:
        print(f"[scrape] skipping @{username}: {exc!r}")
        return 0

    if not embed_texts:
        return 0

    vectors = embeddings.embed_documents(embed_texts)

    written = 0
    for (kind, target), vector in zip(embed_targets, vectors):
        try:
            if kind == "insert":
                target["embedding"] = vector
                db.insert_post(target)
            else:
                post_id, fields = target
                fields["embedding"] = vector
                db.update_post(post_id, **fields)
            written += 1
        except Exception as exc:
            print(f"[scrape] write failed for @{username}: {exc!r}")
    return written


async def scrape_user_channels(
    client: TelegramClient, usernames: list[str], boundary: datetime
) -> None:
    for username in usernames:
        await scrape_channel(client, username, boundary)

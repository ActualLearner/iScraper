from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from types import ModuleType
from unittest import TestCase, mock

telethon = ModuleType("telethon")
telethon.TelegramClient = object
sys.modules.setdefault("telethon", telethon)

httpx = ModuleType("httpx")
httpx.Timeout = lambda *args, **kwargs: None
sys.modules.setdefault("httpx", httpx)

from scraper import scrape


class _Message:
    def __init__(self, id: int, text: str = "", *, photo: bool = False) -> None:
        self.id = id
        self.message = text
        self.photo = object() if photo else None
        self.document = None
        self.grouped_id = None
        self.date = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.edit_date = None


class ScrapeProcessingTests(TestCase):
    def test_existing_unchanged_post_skips_ocr_and_writes(self) -> None:
        message = _Message(10, "caption", photo=True)
        existing = {
            "id": 1,
            "message_id": 10,
            "caption": "caption",
            "image_count": 1,
            "album_grouped_id": None,
            "edited_at": None,
            "normalized_content": "caption",
        }

        with mock.patch.object(scrape.db, "posts_by_message_ids", return_value={10: existing}), \
             mock.patch.object(scrape.db, "insert_posts") as insert_posts, \
             mock.patch.object(scrape.db, "update_post") as update_post, \
             mock.patch.object(scrape, "_ocr_image_messages") as ocr_messages:
            stats = asyncio.run(scrape._process_groups("source", [[message]]))

        self.assertEqual(stats.written, 0)
        self.assertEqual(stats.skipped_unchanged, 1)
        ocr_messages.assert_not_called()
        insert_posts.assert_not_called()
        update_post.assert_not_called()

    def test_caption_can_skip_ocr_for_new_image_post(self) -> None:
        message = _Message(11, "caption", photo=True)

        with mock.patch.object(scrape.config, "OCR_SKIP_WHEN_CAPTION_PRESENT", True), \
             mock.patch.object(scrape.db, "posts_by_message_ids", return_value={}), \
             mock.patch.object(scrape.db, "insert_posts", return_value=[]) as insert_posts, \
             mock.patch.object(scrape, "_ocr_image_messages") as ocr_messages:
            stats = asyncio.run(scrape._process_groups("source", [[message]]))

        self.assertEqual(stats.written, 1)
        ocr_messages.assert_not_called()
        inserted = insert_posts.call_args.args[0]
        self.assertEqual(len(inserted), 1)
        self.assertEqual(inserted[0]["content"], "caption")
        self.assertIsNone(inserted[0]["image_text"])

    def test_new_posts_are_batch_inserted(self) -> None:
        messages = [_Message(12, "one"), _Message(13, "two")]

        with mock.patch.object(scrape.db, "posts_by_message_ids", return_value={}) as existing, \
             mock.patch.object(scrape.db, "insert_posts", return_value=[]) as insert_posts:
            stats = asyncio.run(scrape._process_groups("source", [[m] for m in messages]))

        self.assertEqual(stats.written, 2)
        existing.assert_called_once_with("source", [12, 13])
        inserted = insert_posts.call_args.args[0]
        self.assertEqual([row["message_id"] for row in inserted], [12, 13])
        self.assertEqual([row["content"] for row in inserted], ["one", "two"])

    def test_ocr_image_limit_counts_skipped_images(self) -> None:
        messages = [_Message(i, photo=True) for i in range(1, 5)]

        with mock.patch.object(scrape.config, "OCR_MAX_IMAGES_PER_POST", 2):
            selected, skipped = scrape._limited_image_messages(messages)

        self.assertEqual([m.id for m in selected], [1, 2])
        self.assertEqual(skipped, 2)

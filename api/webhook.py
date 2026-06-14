"""Vercel serverless entrypoint for the Telegram webhook.

Telegram POSTs each update here. We verify the secret token header, hand the
update to the bot router, and always return 200 quickly so Telegram does not
retry. All state lives in Supabase, so nothing is kept between invocations.

Set the webhook to: https://<your-project>.vercel.app/api/webhook
(use scripts/set_webhook.py, which also registers the secret token).
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

# Make the repo root importable (core/, bot/) regardless of Vercel's cwd.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bot import handlers  # noqa: E402
from core import config  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _respond(self, status: int = 200, body: str = "ok") -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self) -> None:  # simple health check
        self._respond(200, "iScraper webhook up")

    def do_POST(self) -> None:
        # Verify the shared secret Telegram echoes back in this header.
        if config.WEBHOOK_SECRET:
            token = self.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if token != config.WEBHOOK_SECRET:
                self._respond(401, "unauthorized")
                return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            update = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._respond(400, "bad request")
            return

        try:
            handlers.handle_update(update)
        except Exception as exc:  # always ack so Telegram doesn't hammer retries
            print(f"[webhook] unhandled: {exc!r}")
        self._respond(200, "ok")

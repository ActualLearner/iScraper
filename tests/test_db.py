from __future__ import annotations

from unittest import TestCase, mock

import httpx

from core import db


class DbRetryTests(TestCase):
    def test_execute_retries_transient_transport_error_and_resets_client(self) -> None:
        calls = {"count": 0}

        def operation():
            calls["count"] += 1
            if calls["count"] == 1:
                raise httpx.RemoteProtocolError("connection dropped")
            return "ok"

        with mock.patch.object(db.config, "SUPABASE_DB_RETRIES", 2), \
             mock.patch.object(db.config, "SUPABASE_DB_RETRY_BACKOFF_SECONDS", 0), \
             mock.patch.object(db, "_reset_client") as reset_client, \
             mock.patch.object(db.logs, "warning") as warning:
            result = db._execute(operation, label="unit")

        self.assertEqual(result, "ok")
        self.assertEqual(calls["count"], 2)
        reset_client.assert_called_once()
        warning.assert_called_once()

    def test_execute_does_not_retry_non_transient_error(self) -> None:
        with mock.patch.object(db.config, "SUPABASE_DB_RETRIES", 3), \
             mock.patch.object(db, "_reset_client") as reset_client:
            with self.assertRaises(ValueError):
                db._execute(lambda: (_ for _ in ()).throw(ValueError("bad")), label="unit")

        reset_client.assert_not_called()

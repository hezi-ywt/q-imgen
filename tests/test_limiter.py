"""Tests for limiter.py: local per-key concurrency control."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from q_imgen import limiter


class LimiterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "state.db"
        self.state_patch = patch.object(limiter, "STATE_DB", self.db_path)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def test_different_keys_do_not_block_each_other(self):
        with limiter.acquire(
            api_key="sk-a",
            channel="a",
            prompt="cat",
            poll_interval=0.01,
            heartbeat_interval=0.1,
            stale_after_seconds=60,
            max_concurrent=1,
        ):
            with limiter.acquire(
                api_key="sk-b",
                channel="b",
                prompt="dog",
                poll_interval=0.01,
                heartbeat_interval=0.1,
                stale_after_seconds=60,
                max_concurrent=1,
            ):
                rows = limiter.status_rows()

        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["running"] == 1 for row in rows))

    def test_same_key_blocks_until_slot_released(self):
        first = limiter.acquire(
            api_key="sk-shared",
            channel="a",
            prompt="cat",
            poll_interval=0.01,
            heartbeat_interval=0.1,
            stale_after_seconds=60,
            max_concurrent=1,
        )
        first.__enter__()
        self.addCleanup(first.__exit__, None, None, None)

        events: list[str] = []

        def worker():
            events.append("waiting")
            with limiter.acquire(
                api_key="sk-shared",
                channel="b",
                prompt="dog",
                poll_interval=0.01,
                heartbeat_interval=0.1,
                stale_after_seconds=60,
                max_concurrent=1,
            ):
                events.append("running")

        thread = threading.Thread(target=worker)
        thread.start()
        time.sleep(0.1)

        rows = limiter.status_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["running"], 1)
        self.assertEqual(rows[0]["waiting"], 1)
        self.assertEqual(events, ["waiting"])

        first.__exit__(None, None, None)
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(events, ["waiting", "running"])

    def test_status_rows_include_running_lease_details(self):
        with limiter.acquire(
            api_key="sk-shared",
            channel="openai-a",
            prompt="cat in shrine with glowing eyes and lanterns",
            poll_interval=0.01,
            heartbeat_interval=0.1,
            stale_after_seconds=60,
            max_concurrent=2,
        ):
            rows = limiter.status_rows()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["running"], 1)
        self.assertEqual(row["waiting"], 0)
        self.assertEqual(len(row["leases"]), 1)
        self.assertEqual(row["leases"][0]["channel"], "openai-a")
        self.assertIn("cat in shrine", row["leases"][0]["prompt_preview"])

    def test_default_max_concurrent_is_10(self):
        with limiter.acquire(
            api_key="sk-default-cap",
            channel="openai-a",
            prompt="cat",
            poll_interval=0.01,
            heartbeat_interval=0.1,
            stale_after_seconds=60,
        ):
            rows = limiter.status_rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["max_concurrent"], 10)

    def test_pid_exists_handles_systemerror_without_crashing(self):
        with patch("q_imgen.limiter.os.kill", side_effect=SystemError("winerror 87")):
            self.assertFalse(limiter._pid_exists(12345))

    def test_cleanup_stale_reclaims_old_waiting_and_running_rows(self):
        limiter._init_db()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO leases
                (id, resource_key, status, channel, pid, hostname, prompt_preview,
                 created_at, started_at, heartbeat_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "lease-1",
                    "deadbeef",
                    "running",
                    "openai-a",
                    999999,
                    "host",
                    "prompt",
                    "2026-04-20T10:00:00+08:00",
                    "2026-04-20T10:00:00+08:00",
                    "2026-04-20T10:00:00+08:00",
                ),
            )
            conn.commit()

        deleted = limiter.cleanup_stale(
            resource_key="deadbeef",
            stale_after_seconds=1,
            pid_exists=lambda pid: False,
        )

        self.assertEqual(deleted, 1)
        self.assertEqual(limiter.status_rows(), [])


if __name__ == "__main__":
    unittest.main()

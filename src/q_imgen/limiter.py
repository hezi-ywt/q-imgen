"""Lightweight local concurrency limiter keyed by API key hash.

This module coordinates concurrent q-imgen CLI calls on one machine using a
single SQLite file. It intentionally does not know anything about remote
provider queues or outputs.
"""

from __future__ import annotations

import datetime as _dt
import errno
import hashlib
import os
import socket
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

STATE_DB = Path.home() / ".q-imgen" / "state.db"
DEFAULT_MAX_CONCURRENT = 10
DEFAULT_POLL_INTERVAL = 0.2
DEFAULT_HEARTBEAT_INTERVAL = 30.0
DEFAULT_STALE_AFTER_SECONDS = 600
PROMPT_PREVIEW_LIMIT = 80
SQLITE_TIMEOUT_SECONDS = 5.0


class LimiterError(Exception):
    """Raised when local limiter state cannot be read or updated."""


def _format_ts() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def _parse_ts(value: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(value)


def resource_key_for_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def _prompt_preview(prompt: str) -> str:
    compact = " ".join(prompt.split())
    if len(compact) <= PROMPT_PREVIEW_LIMIT:
        return compact
    return compact[: PROMPT_PREVIEW_LIMIT - 3] + "..."


def _ensure_parent_dir() -> None:
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_parent_dir()
    conn = sqlite3.connect(STATE_DB, timeout=SQLITE_TIMEOUT_SECONDS, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with closing(_connect()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS limits (
                resource_key TEXT PRIMARY KEY,
                max_concurrent INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leases (
                id TEXT PRIMARY KEY,
                resource_key TEXT NOT NULL,
                status TEXT NOT NULL,
                channel TEXT NOT NULL,
                pid INTEGER NOT NULL,
                hostname TEXT NOT NULL,
                prompt_preview TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                heartbeat_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_leases_resource_status
            ON leases(resource_key, status)
            """
        )


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        return True
    except SystemError:
        # Windows can surface invalid/finished pid probes as SystemError
        # instead of OSError; treat those as "pid is gone" so cleanup_stale
        # can reclaim the row instead of crashing status/acquire.
        return False
    return True


def _limit_for(
    conn: sqlite3.Connection, resource_key: str, override: int | None
) -> int:
    if override is not None:
        now = _format_ts()
        conn.execute(
            """
            INSERT INTO limits(resource_key, max_concurrent, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(resource_key) DO UPDATE
            SET max_concurrent = excluded.max_concurrent,
                updated_at = excluded.updated_at
            """,
            (resource_key, override, now),
        )
        return override

    row = conn.execute(
        "SELECT max_concurrent FROM limits WHERE resource_key = ?",
        (resource_key,),
    ).fetchone()
    if row is None:
        return DEFAULT_MAX_CONCURRENT
    return int(row["max_concurrent"])


def cleanup_stale(
    *,
    resource_key: str | None = None,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    pid_exists: Callable[[int], bool] = _pid_exists,
) -> int:
    _init_db()
    now = _dt.datetime.now().astimezone()
    deleted_ids: list[str] = []
    with closing(_connect()) as conn:
        if resource_key is None:
            rows = conn.execute(
                "SELECT id, pid, heartbeat_at FROM leases"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, pid, heartbeat_at FROM leases WHERE resource_key = ?",
                (resource_key,),
            ).fetchall()

        for row in rows:
            heartbeat = _parse_ts(str(row["heartbeat_at"]))
            age = (now - heartbeat).total_seconds()
            if age > stale_after_seconds or not pid_exists(int(row["pid"])):
                deleted_ids.append(str(row["id"]))

        if deleted_ids:
            conn.executemany("DELETE FROM leases WHERE id = ?", [(x,) for x in deleted_ids])
    return len(deleted_ids)


@dataclass
class Lease:
    api_key: str
    channel: str
    prompt: str
    poll_interval: float = DEFAULT_POLL_INTERVAL
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS
    max_concurrent: int | None = None

    def __post_init__(self) -> None:
        self.id = uuid.uuid4().hex
        self.resource_key = resource_key_for_api_key(self.api_key)
        self.pid = os.getpid()
        self.hostname = socket.gethostname()
        self.prompt_preview = _prompt_preview(self.prompt)
        self._stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def __enter__(self) -> "Lease":
        _init_db()
        created_at = _format_ts()
        while True:
            cleanup_stale(
                resource_key=self.resource_key,
                stale_after_seconds=self.stale_after_seconds,
            )
            try:
                with closing(_connect()) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    cap = _limit_for(conn, self.resource_key, self.max_concurrent)
                    existing = conn.execute(
                        "SELECT id FROM leases WHERE id = ?",
                        (self.id,),
                    ).fetchone()
                    if existing is None:
                        conn.execute(
                            """
                            INSERT INTO leases(
                                id, resource_key, status, channel, pid, hostname,
                                prompt_preview, created_at, started_at, heartbeat_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                self.id,
                                self.resource_key,
                                "waiting",
                                self.channel,
                                self.pid,
                                self.hostname,
                                self.prompt_preview,
                                created_at,
                                None,
                                created_at,
                            ),
                        )
                    else:
                        conn.execute(
                            "UPDATE leases SET heartbeat_at = ? WHERE id = ?",
                            (_format_ts(), self.id),
                        )

                    running = conn.execute(
                        """
                        SELECT COUNT(*) AS n
                        FROM leases
                        WHERE resource_key = ? AND status = 'running'
                        """,
                        (self.resource_key,),
                    ).fetchone()
                    if int(running["n"]) < cap:
                        started_at = _format_ts()
                        conn.execute(
                            """
                            UPDATE leases
                            SET status = 'running', started_at = ?, heartbeat_at = ?
                            WHERE id = ?
                            """,
                            (started_at, started_at, self.id),
                        )
                        conn.commit()
                        self._start_heartbeat()
                        return self
                    conn.commit()
            except sqlite3.Error as exc:
                raise LimiterError(f"local limiter unavailable: {exc}") from exc
            time.sleep(self.poll_interval)

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=max(0.2, self.heartbeat_interval))
        try:
            with closing(_connect()) as conn:
                conn.execute("DELETE FROM leases WHERE id = ?", (self.id,))
        except sqlite3.Error as exc:
            raise LimiterError(f"local limiter cleanup failed: {exc}") from exc

    def _start_heartbeat(self) -> None:
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"q-imgen-lease-{self.id[:8]}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_interval):
            try:
                with closing(_connect()) as conn:
                    conn.execute(
                        "UPDATE leases SET heartbeat_at = ? WHERE id = ?",
                        (_format_ts(), self.id),
                    )
            except sqlite3.Error:
                return


def acquire(
    *,
    api_key: str,
    channel: str,
    prompt: str,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    max_concurrent: int | None = None,
) -> Lease:
    return Lease(
        api_key=api_key,
        channel=channel,
        prompt=prompt,
        poll_interval=poll_interval,
        heartbeat_interval=heartbeat_interval,
        stale_after_seconds=stale_after_seconds,
        max_concurrent=max_concurrent,
    )


def status_rows() -> list[dict[str, object]]:
    _init_db()
    cleanup_stale()
    with closing(_connect()) as conn:
        limit_rows = conn.execute(
            "SELECT resource_key, max_concurrent FROM limits ORDER BY resource_key"
        ).fetchall()
        lease_rows = conn.execute(
            """
            SELECT resource_key, status, channel, pid, started_at, prompt_preview
            FROM leases
            ORDER BY resource_key, started_at, created_at
            """
        ).fetchall()

    caps = {str(row["resource_key"]): int(row["max_concurrent"]) for row in limit_rows}
    grouped: dict[str, dict[str, object]] = {}
    for row in lease_rows:
        key = str(row["resource_key"])
        bucket = grouped.setdefault(
            key,
            {
                "resource_key": key,
                "max_concurrent": caps.get(key, DEFAULT_MAX_CONCURRENT),
                "running": 0,
                "waiting": 0,
                "leases": [],
            },
        )
        if row["status"] == "running":
            bucket["running"] = int(bucket["running"]) + 1
            bucket["leases"].append(
                {
                    "channel": str(row["channel"]),
                    "pid": int(row["pid"]),
                    "started_at": str(row["started_at"]),
                    "prompt_preview": str(row["prompt_preview"]),
                }
            )
        else:
            bucket["waiting"] = int(bucket["waiting"]) + 1

    for key, cap in caps.items():
        grouped.setdefault(
            key,
            {
                "resource_key": key,
                "max_concurrent": cap,
                "running": 0,
                "waiting": 0,
                "leases": [],
            },
        )

    return [grouped[key] for key in sorted(grouped)]

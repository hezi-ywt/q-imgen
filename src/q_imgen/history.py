"""Append-only audit log of every q-imgen generation.

Records go to ``~/.q-imgen/history/YYYY-MM-DD.jsonl`` — one JSON object per
line, partitioned by **local date**. Field order is preserved (Python dict
insertion order) so a human scanning the JSONL with ``cat`` or ``less`` sees
the most useful fields (``ts``, ``prompt``) first.

Spec / query manual: ``skills/q-imgen/references/history-queries.md``.

Design choices baked in here:
- **Best-effort writes**. If anything fails (disk full, permission, race),
  we print a ``[q-imgen] warning: ...`` line to stderr and return; we never
  raise out, because by the time we're called the user's image has already
  been generated and the log is secondary state.
- **Cross-platform exclusive lock for concurrency**. Multiple ``q-imgen``
  processes can append to the same file at the same time (``xargs -P``,
  asyncio fanout, etc.) without interleaving. POSIX uses ``fcntl.flock``,
  Windows uses ``msvcrt.locking``. Each writer holds an exclusive lock for
  the microseconds it takes to ``write()`` + ``flush()``.
- **Binary append mode**. The file is opened ``"ab"`` so the JSONL stays
  LF-only on every platform — Windows text mode would otherwise translate
  ``\\n`` to ``\\r\\n`` and confuse downstream ``jq``/``cat`` on shared
  cross-platform logs.
- **Workdir = git root if available, else cwd**. Same git repo from
  different subdirectories logs to the same workdir value, which makes
  ``jq 'select(.workdir == ...)'`` filtering more useful.
- **No rotation, no TTL**. JSONL records are tiny (~300 bytes); 10k calls is
  ~3 MB. If anyone hits a real wall the answer is ``find ~/.q-imgen/history
  -mtime +90 -delete``, not in-process logic.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

HISTORY_DIR = Path.home() / ".q-imgen" / "history"

# In-process serialization. The OS-level lock below covers the cross-process
# case (multiple ``q-imgen`` invocations writing the same log); this Python
# lock makes sure same-process threads never race on the OS lock either.
# Without it, ``msvcrt.locking`` on Windows would back off (LK_LOCK retries
# 10×1s then raises) under burst contention from e.g. asyncio fanout, and
# best-effort writes would silently get dropped.
_PROCESS_LOCK = threading.Lock()


# Cross-platform exclusive file lock. POSIX uses fcntl.flock (whole-file
# advisory lock); Windows uses msvcrt.locking, which is byte-range and
# mandatory but still meets our single-writer-at-a-time guarantee for the
# few bytes we append.
if os.name == "nt":
    import msvcrt

    def _lock_exclusive(fd: int) -> None:
        # LK_LOCK retries for ~10 seconds before raising; sufficient given
        # we hold the lock only for the duration of write()+flush().
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

    def _unlock(fd: int) -> None:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            # Best-effort unlock — closing the file releases it anyway.
            pass

else:
    import fcntl

    def _lock_exclusive(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def resolve_workdir() -> str:
    """Return the project root for the current call.

    Walks up from cwd looking for a ``.git`` directory; returns its parent if
    found, otherwise the literal cwd. Always an absolute path string. Same
    git repo from different subdirectories returns the same value.
    """
    cwd = Path.cwd().resolve()
    cur = cwd
    while cur != cur.parent:
        if (cur / ".git").exists():
            return str(cur)
        cur = cur.parent
    return str(cwd)


def today_log_path() -> Path:
    """Path of the log file for today's local date.

    May not exist yet — ``append`` creates the parent directory and the file
    on first write.
    """
    today = _dt.datetime.now().strftime("%Y-%m-%d")
    return HISTORY_DIR / f"{today}.jsonl"


def _format_ts() -> str:
    """Local-time ISO8601 with timezone offset, e.g. ``2026-04-15T19:09:23+08:00``.

    Microseconds dropped because nobody scanning a log cares about them and
    they make the line noisier.
    """
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def _abs(paths: list[str] | None) -> list[str]:
    if not paths:
        return []
    return [str(Path(p).resolve()) for p in paths]


def build_record(
    *,
    prompt: str,
    model: str,
    channel: str,
    protocol: str,
    aspect_ratio: str,
    image_size: str | None,
    ref_images: list[str] | None,
    outputs: list[str],
    status: str,
    error: str | None,
    latency_ms: int,
    workdir: str | None = None,
) -> dict[str, Any]:
    """Build one audit record.

    ``error`` is included only when ``status == "error"`` so the field set is
    minimal for the success case (which is the vast majority).

    Field insertion order is intentional: ``ts`` and ``prompt`` come first
    because that's what humans scan for. ``workdir`` comes near the end
    because it's a filter key, not a scan key.
    """
    record: dict[str, Any] = {
        "ts": _format_ts(),
        "prompt": prompt,
        "model": model,
        "channel": channel,
        "protocol": protocol,
        "aspect_ratio": aspect_ratio,
        "image_size": image_size,
        "ref_images": _abs(ref_images),
        "outputs": _abs(outputs),
        "status": status,
    }
    if error is not None:
        record["error"] = error
    record["latency_ms"] = latency_ms
    record["workdir"] = workdir if workdir is not None else resolve_workdir()
    return record


def append(record: dict[str, Any]) -> None:
    """Append one record to today's log file.

    Best-effort: any failure (mkdir, open, write, flock) is caught and
    surfaced as a single ``[q-imgen] warning: ...`` line on stderr. We do not
    re-raise, because the caller's actual work (generating an image) is
    already done and the log is secondary.
    """
    try:
        log_path = today_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        # Two-tier locking: in-process threading.Lock first (so same-process
        # threads can't race on the OS lock and starve), then the OS-level
        # lock for the cross-process case. Binary append keeps line endings
        # LF on every OS so the JSONL file is byte-identical across
        # platforms.
        with _PROCESS_LOCK:
            with open(log_path, "ab") as f:
                _lock_exclusive(f.fileno())
                try:
                    f.write(payload)
                    f.flush()
                finally:
                    _unlock(f.fileno())
    except Exception as exc:  # noqa: BLE001 — best-effort, see docstring
        print(
            f"[q-imgen] warning: history append failed: {exc}",
            file=sys.stderr,
        )

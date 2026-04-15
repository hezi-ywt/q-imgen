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
- **fcntl.flock for concurrency**. Multiple ``q-imgen`` processes can append
  to the same file at the same time (``xargs -P``, asyncio fanout, etc.)
  without interleaving. Each writer holds an exclusive lock for the
  microseconds it takes to ``write()`` + ``flush()``.
- **Workdir = git root if available, else cwd**. Same git repo from
  different subdirectories logs to the same workdir value, which makes
  ``jq 'select(.workdir == ...)'`` filtering more useful.
- **No rotation, no TTL**. JSONL records are tiny (~300 bytes); 10k calls is
  ~3 MB. If anyone hits a real wall the answer is ``find ~/.q-imgen/history
  -mtime +90 -delete``, not in-process logic.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import json
import sys
from pathlib import Path
from typing import Any

HISTORY_DIR = Path.home() / ".q-imgen" / "history"


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
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(log_path, "a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(line)
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as exc:  # noqa: BLE001 — best-effort, see docstring
        print(
            f"[q-imgen] warning: history append failed: {exc}",
            file=sys.stderr,
        )

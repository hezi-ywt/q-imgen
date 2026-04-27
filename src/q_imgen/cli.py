#!/usr/bin/env python3
"""q-imgen — atomic nanobanana CLI.

One command (``generate``) does text-to-image, single-image edit, multi-image
edit, and batch. A second command group (``channel``) manages endpoint
routing: each channel is a ``(protocol, base_url, api_key, model)`` tuple,
and ``--channel NAME`` picks one at call time (or the default if omitted).

Output contract:
- ``generate`` / ``batch`` stdout: one JSON object per call (or per batch),
  parseable by agents
- stderr: ``[q-imgen] ...`` diagnostic lines
- exit code: 0 on success, 1 on any failure
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from . import __version__
from .channels import CHANNELS_FILE, Channel, ChannelError, ChannelStore, mask_secret
from . import gemini_client
from . import history
from . import limiter
from . import openai_client
from . import openai_images_client


# ---- small helpers ----


def _fail(message: str) -> int:
    print(f"[q-imgen] {message}", file=sys.stderr)
    return 1


def _emit(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def _classify_error(message: str) -> tuple[str, bool]:
    lowered = message.lower()

    if (
        "http 401" in lowered
        or "http 403" in lowered
        or "unauthorized" in lowered
        or "invalid api key" in lowered
    ):
        return "auth_error", False
    if (
        "http 429" in lowered
        or "rate limit" in lowered
        or "too many requests" in lowered
    ):
        return "rate_limit", True
    if "local limiter" in lowered:
        return "local_limiter_error", True
    if "api returned no images" in lowered or "response contained no images" in lowered:
        return "no_image_returned", False
    if "model" in lowered and (
        "not found" in lowered or "unsupported" in lowered or "does not exist" in lowered
    ):
        return "invalid_model", False
    if (
        "request timed out" in lowered
        or "api request timed out" in lowered
        or "url error" in lowered
        or "connection reset" in lowered
        or "dns" in lowered
        or "ssl" in lowered
    ):
        return "network_error", True
    if (
        "http 500" in lowered
        or "http 502" in lowered
        or "http 503" in lowered
        or "http 504" in lowered
        or "service unavailable" in lowered
        or "bad gateway" in lowered
    ):
        return "provider_busy", True
    if (
        "reference image not found" in lowered
        or "unsupported image type" in lowered
        or "missing required field" in lowered
        or "unknown parameter" in lowered
        or "must be a string" in lowered
        or "task is not an object" in lowered
        or "unknown protocol" in lowered
    ):
        return "invalid_request", False
    return "unknown_error", False


def _error_result(base_result: dict[str, Any], message: str, **extra: Any) -> dict[str, Any]:
    error_code, retryable = _classify_error(message)
    return {
        **base_result,
        "status": "error",
        "error": message,
        "error_code": error_code,
        "retryable": retryable,
        **extra,
    }


def _batch_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    failed_task_indexes = [
        int(result["task_index"])
        for result in results
        if result.get("status") != "ok" and "task_index" in result
    ]
    error_counts = Counter(
        str(result.get("error_code", "unknown_error"))
        for result in results
        if result.get("status") != "ok"
    )
    return {
        "failed": len(failed_task_indexes),
        "retryable_failures": sum(
            1 for result in results if result.get("status") != "ok" and result.get("retryable")
        ),
        "failed_task_indexes": failed_task_indexes,
        "error_counts": dict(error_counts),
    }


# ---- generate (shared by one-shot and batch) ----


def _run_single(
    channel: Channel,
    *,
    prompt: str,
    reference_images: list[str] | None,
    aspect_ratio: str,
    image_size: str | None,
    quality: str | None,
    background: str | None,
    output_format: str | None,
    num_images: int | None,
    output_dir: str,
    prefix: str,
) -> dict[str, Any]:
    """Dispatch one generate call to the right protocol and return a result dict.

    Shape on success::
        {"status": "ok", "channel": ..., "model": ..., "prompt": ...,
         "images": [paths], "ref_images": [paths]}

    Shape on failure::
        {"status": "error", "channel": ..., "error": "..."}

    Never raises — the caller decides how to aggregate results. Always writes
    one history record after the call (success or failure) via
    ``history.append`` (best-effort, see history.py).
    """
    # Default prefix to the channel name so two runs on different channels
    # naturally land on disjoint filenames (e.g. ``gemini-a_000.png`` vs
    # ``openai-a_000.png``). _save_*_path also auto-suffixes on collision,
    # so even a same-channel rerun is non-destructive.
    if not prefix:
        prefix = channel.name

    base_result: dict[str, Any] = {
        "channel": channel.name,
        "model": channel.model,
        "prompt": prompt,
        "ref_images": reference_images or [],
    }
    # Initialized to a sentinel so the history append at the end always has a
    # valid result dict to read from, even if the try block exits via a code
    # path we didn't anticipate (it shouldn't, but defense in depth).
    result: dict[str, Any] = _error_result(
        base_result,
        "internal: protocol dispatch did not produce a result",
    )
    started_ns = time.monotonic_ns()
    try:
        with limiter.acquire(
            api_key=channel.api_key,
            channel=channel.name,
            prompt=prompt,
        ):
            if channel.protocol == "gemini":
                response = gemini_client.generate(
                    prompt=prompt,
                    base_url=channel.base_url,
                    api_key=channel.api_key,
                    model=channel.model,
                    reference_images=reference_images,
                    aspect_ratio=aspect_ratio,
                    image_size=image_size,
                    timeout=300,
                )
                images, texts = gemini_client.extract_images(response)
                if not images:
                    result = _error_result(
                        base_result,
                        "API returned no images",
                        texts=texts,
                    )
                else:
                    saved = gemini_client.save_images(images, output_dir, prefix=prefix)
                    result = {
                        **base_result,
                        "status": "ok",
                        "images": saved,
                        "texts": texts,
                    }

            elif channel.protocol == "openai":
                saved = openai_client.generate(
                    prompt=prompt,
                    base_url=channel.base_url,
                    api_key=channel.api_key,
                    model=channel.model,
                    reference_images=reference_images,
                    aspect_ratio=aspect_ratio,
                    image_size=image_size,
                    output_dir=output_dir,
                    prefix=prefix,
                    timeout=300,
                )
                result = {**base_result, "status": "ok", "images": saved}

            elif channel.protocol == "openai_images":
                saved = openai_images_client.generate(
                    prompt=prompt,
                    base_url=channel.base_url,
                    api_key=channel.api_key,
                    model=channel.model,
                    reference_images=reference_images,
                    aspect_ratio=aspect_ratio,
                    image_size=image_size,
                    quality=quality,
                    background=background,
                    output_format=output_format,
                    num_images=num_images,
                    output_dir=output_dir,
                    prefix=prefix,
                    timeout=300,
                )
                result = {**base_result, "status": "ok", "images": saved}

            else:
                result = _error_result(
                    base_result,
                    f"unknown protocol: {channel.protocol}",
                )

    except (
        gemini_client.GeminiError,
        openai_client.OpenAIError,
        openai_images_client.OpenAIImagesError,
        limiter.LimiterError,
    ) as exc:
        result = _error_result(base_result, str(exc))

    latency_ms = (time.monotonic_ns() - started_ns) // 1_000_000
    history.append(
        history.build_record(
            prompt=prompt,
            model=channel.model,
            channel=channel.name,
            protocol=channel.protocol,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            ref_images=reference_images,
            outputs=result.get("images", []),
            status=result["status"],
            error=result.get("error"),
            latency_ms=latency_ms,
        )
    )
    return result


def _bench_prompt_from_markdown(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    excerpt = " ".join(raw.split())
    if len(excerpt) > 180:
        excerpt = excerpt[:177] + "..."
    if excerpt:
        return (
            f"国风漫画单幅海报，基于短篇《{path.stem}》创作。"
            f"核心设定：{excerpt}。"
            "竖版构图，单主角突出，场景完整，电影感光影，高细节，"
            "适合中文网文改编视觉开发。"
        )
    return (
        f"国风漫画单幅海报，基于短篇《{path.stem}》创作。"
        "竖版构图，单主角突出，场景完整，电影感光影，高细节，"
        "适合中文网文改编视觉开发。"
    )


def _run_bench_stage(
    channel: Channel,
    *,
    files: list[Path],
    concurrency: int,
    aspect_ratio: str,
    image_size: str | None,
    output_dir: str,
    status_interval: float,
) -> dict[str, Any]:
    stage_dir = Path(output_dir) / f"stage-{concurrency}"
    stage_dir.mkdir(parents=True, exist_ok=True)
    resource_key = limiter.resource_key_for_api_key(channel.api_key)
    started = time.monotonic()

    def _worker(index: int, path: Path) -> dict[str, Any]:
        result = _run_single(
            channel,
            prompt=_bench_prompt_from_markdown(path),
            reference_images=None,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            quality=None,
            background=None,
            output_format=None,
            num_images=None,
            output_dir=str(stage_dir),
            prefix=f"s{index:02d}",
        )
        return {
            "index": index,
            "source_file": str(path),
            "title": path.stem,
            **result,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(_worker, idx, path)
            for idx, path in enumerate(files[:concurrency], start=1)
        ]

        snapshots: list[dict[str, Any]] = []
        while True:
            done = sum(1 for future in futures if future.done())
            try:
                rows = limiter.status_rows()
                row = next(
                    (item for item in rows if item["resource_key"] == resource_key),
                    None,
                )
                snapshots.append(
                    {
                        "elapsed_s": round(time.monotonic() - started, 1),
                        "done": done,
                        "running": int(row["running"]) if row else 0,
                        "waiting": int(row["waiting"]) if row else 0,
                    }
                )
            except limiter.LimiterError as exc:
                snapshots.append(
                    {
                        "elapsed_s": round(time.monotonic() - started, 1),
                        "done": done,
                        "running": None,
                        "waiting": None,
                        "error": str(exc),
                    }
                )

            if done == len(futures):
                break
            time.sleep(status_interval)

        results = [future.result() for future in futures]

    ok = sum(1 for result in results if result["status"] == "ok")
    return {
        "concurrency": concurrency,
        "elapsed_s": round(time.monotonic() - started, 1),
        "ok": ok,
        "failed": len(results) - ok,
        "peak_running": max((snap["running"] or 0) for snap in snapshots),
        "peak_waiting": max((snap["waiting"] or 0) for snap in snapshots),
        "snapshots": snapshots,
        "results": results,
    }


# ---- subcommand handlers ----


def cmd_generate(args: argparse.Namespace) -> int:
    try:
        store = ChannelStore.load()
        channel = store.resolve(args.channel)
    except ChannelError as exc:
        return _fail(str(exc))

    # --model overrides the channel's default model for this one call.
    if args.model:
        channel = Channel(
            name=channel.name,
            protocol=channel.protocol,
            base_url=channel.base_url,
            api_key=channel.api_key,
            model=args.model,
        )

    result = _run_single(
        channel,
        prompt=args.prompt,
        reference_images=args.images,
        aspect_ratio=args.aspect_ratio,
        image_size=args.image_size,
        quality=args.quality,
        background=args.background,
        output_format=args.output_format,
        num_images=args.num_images,
        output_dir=args.output_dir,
        prefix=args.prefix,
    )
    _emit(result)
    return 0 if result["status"] == "ok" else 1


def cmd_batch(args: argparse.Namespace) -> int:
    try:
        store = ChannelStore.load()
        channel = store.resolve(args.channel)
    except ChannelError as exc:
        return _fail(str(exc))

    task_file = Path(args.task_file)
    if not task_file.exists():
        return _fail(f"task file not found: {args.task_file}")

    try:
        tasks = json.loads(task_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _fail(f"task file is not valid JSON: {exc}")

    if not isinstance(tasks, list):
        return _fail("task file must contain a JSON array")

    # Per-call --model override still applies to every task.
    active_channel = channel
    if args.model:
        active_channel = Channel(
            name=channel.name,
            protocol=channel.protocol,
            base_url=channel.base_url,
            api_key=channel.api_key,
            model=args.model,
        )

    # Resolve the batch-wide prefix base now (channel name when not given)
    # so every per-task prefix below is well-formed.
    batch_prefix = args.prefix if args.prefix else active_channel.name

    results: list[dict[str, Any]] = []
    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            results.append(
                _error_result(
                    {"task_index": i},
                    "task is not an object",
                )
            )
            continue

        if "prompt" not in task:
            results.append(
                _error_result(
                    {"task_index": i},
                    "task is missing required field: prompt",
                )
            )
            continue

        if not isinstance(task["prompt"], str):
            results.append(
                _error_result(
                    {"task_index": i},
                    "task field 'prompt' must be a string",
                )
            )
            continue

        result = _run_single(
            active_channel,
            prompt=task["prompt"],
            reference_images=task.get("images"),
            aspect_ratio=task.get("aspect_ratio", args.aspect_ratio),
            image_size=task.get("image_size", args.image_size),
            quality=task.get("quality", args.quality),
            background=task.get("background", args.background),
            output_format=task.get("output_format", args.output_format),
            num_images=task.get("num_images", args.num_images),
            output_dir=args.output_dir,
            prefix=f"{batch_prefix}_{i:03d}",
        )
        result["task_index"] = i
        results.append(result)

        if i < len(tasks) - 1:
            time.sleep(args.delay)

    total_ok = sum(1 for r in results if r["status"] == "ok")
    summary = _batch_summary(results)
    _emit(
        {
            "status": "ok" if total_ok == len(results) else "partial",
            "channel": active_channel.name,
            "model": active_channel.model,
            "total": len(results),
            "ok": total_ok,
            **summary,
            "results": results,
        }
    )
    # Exit 1 only if every task failed; partial success is still exit 0 so the
    # caller can inspect the results array.
    return 1 if total_ok == 0 and results else 0


def cmd_channel_add(args: argparse.Namespace) -> int:
    try:
        store = ChannelStore.load()
        store.add(
            args.name,
            protocol=args.protocol,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            overwrite=args.force,
        )
        store.save()
    except ChannelError as exc:
        return _fail(str(exc))
    print(f"[q-imgen] channel '{args.name}' saved to {CHANNELS_FILE}")
    if store.default == args.name:
        print(f"[q-imgen] '{args.name}' is now the default channel")
    return 0


def cmd_channel_list(args: argparse.Namespace) -> int:
    store = ChannelStore.load()
    if not store.channels:
        print("[q-imgen] no channels configured. Run `q-imgen channel add ...`")
        return 0
    for name in store.names():
        marker = "*" if name == store.default else " "
        ch = store.channels[name]
        print(f" {marker} {name}  [{ch.protocol}]  {ch.model}  {ch.base_url}")
    return 0


def cmd_channel_show(args: argparse.Namespace) -> int:
    try:
        store = ChannelStore.load()
        channel = store.resolve(args.name)
    except ChannelError as exc:
        return _fail(str(exc))

    _emit(
        {
            "name": channel.name,
            "protocol": channel.protocol,
            "base_url": channel.base_url,
            "api_key": mask_secret(channel.api_key),
            "model": channel.model,
            "is_default": channel.name == store.default,
        }
    )
    return 0


def cmd_channel_use(args: argparse.Namespace) -> int:
    try:
        store = ChannelStore.load()
        store.set_default(args.name)
        store.save()
    except ChannelError as exc:
        return _fail(str(exc))
    print(f"[q-imgen] default channel is now '{args.name}'")
    return 0


def cmd_channel_rm(args: argparse.Namespace) -> int:
    try:
        store = ChannelStore.load()
        store.remove(args.name)
        store.save()
    except ChannelError as exc:
        return _fail(str(exc))
    print(f"[q-imgen] channel '{args.name}' removed")
    if store.default:
        print(f"[q-imgen] default channel is now '{store.default}'")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    """Print today's history log file path. Nothing else.

    All actual querying is left to ``cat`` / ``grep`` / ``jq``. See
    ``skills/q-imgen/references/history-queries.md`` for ready-to-paste
    command templates.
    """
    print(history.today_log_path())
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    try:
        rows = limiter.status_rows()
    except limiter.LimiterError as exc:
        return _fail(str(exc))

    if args.json:
        _emit(rows)
        return 0

    if not rows:
        print("[q-imgen] no local limiter activity")
        return 0

    for row in rows:
        print(
            f"{row['resource_key']} cap={row['max_concurrent']} "
            f"running={row['running']} waiting={row['waiting']}"
        )
        for lease in row["leases"]:
            print(
                f"  RUN pid={lease['pid']} channel={lease['channel']} "
                f"since={lease['started_at']} prompt=\"{lease['prompt_preview']}\""
            )
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    try:
        store = ChannelStore.load()
        channel = store.resolve(args.channel)
    except ChannelError as exc:
        return _fail(str(exc))

    if args.model:
        channel = Channel(
            name=channel.name,
            protocol=channel.protocol,
            base_url=channel.base_url,
            api_key=channel.api_key,
            model=args.model,
        )

    source_dir = Path(args.source_dir)
    if not source_dir.exists():
        return _fail(f"source dir not found: {args.source_dir}")
    if not source_dir.is_dir():
        return _fail(f"source dir is not a directory: {args.source_dir}")

    files = sorted(path for path in source_dir.iterdir() if path.suffix.lower() == ".md")
    if not files:
        return _fail(f"no .md files found in: {args.source_dir}")

    levels = args.concurrency or [1, 5, 10, 20]
    if any(level <= 0 for level in levels):
        return _fail("all --concurrency values must be positive integers")
    if max(levels) > len(files):
        return _fail(
            f"not enough markdown files for requested concurrency: "
            f"need {max(levels)}, found {len(files)}"
        )

    stages = [
        _run_bench_stage(
            channel,
            files=files,
            concurrency=level,
            aspect_ratio=args.aspect_ratio,
            image_size=args.image_size,
            output_dir=args.output_dir,
            status_interval=args.status_interval,
        )
        for level in levels
    ]

    total_failed = sum(stage["failed"] for stage in stages)
    _emit(
        {
            "status": "ok" if total_failed == 0 else "partial",
            "channel": channel.name,
            "model": channel.model,
            "source_dir": str(source_dir),
            "stages": stages,
        }
    )
    return 0 if total_failed == 0 else 1


# ---- parser wiring ----


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="q-imgen",
        description="Atomic nanobanana CLI: one image generation primitive, many channels.",
    )
    parser.add_argument(
        "--version", action="version", version=f"q-imgen {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # generate
    gen = subparsers.add_parser(
        "generate",
        help="Generate image(s) from a prompt (text-to-image, edit, or multi-edit)",
    )
    gen.add_argument("prompt", help="Text prompt")
    gen.add_argument(
        "--image",
        dest="images",
        action="append",
        help="Reference image path (repeat for multi-image edit)",
    )
    gen.add_argument("--channel", help="Channel name (default: configured default)")
    gen.add_argument("--model", help="Override the channel's model for this call")
    gen.add_argument("--aspect-ratio", default="3:4", help="Aspect ratio (default: 3:4)")
    gen.add_argument(
        "--image-size",
        default=None,
        help="Image size hint. openai_images accepts sizes like 1024x1536.",
    )
    gen.add_argument("--quality", default=None, help="openai_images quality")
    gen.add_argument("--background", default=None, help="openai_images background")
    gen.add_argument("--output-format", default=None, help="openai_images output format")
    gen.add_argument(
        "--num-images",
        type=int,
        default=None,
        help="Number of images for openai_images",
    )
    gen.add_argument(
        "-o", "--output-dir", default="./output", help="Output directory"
    )
    gen.add_argument(
        "--prefix",
        default=None,
        help="Filename prefix (default: the channel name, so switching "
        "channels won't collide on disk)",
    )
    gen.set_defaults(func=cmd_generate)

    # batch
    bat = subparsers.add_parser(
        "batch", help="Run a JSON array of generation tasks"
    )
    bat.add_argument("task_file", help="Path to JSON file with an array of task objects")
    bat.add_argument("--channel", help="Channel name (default: configured default)")
    bat.add_argument("--model", help="Override the channel's model for all tasks")
    bat.add_argument(
        "--aspect-ratio",
        default="3:4",
        help="Default aspect ratio when a task doesn't specify one",
    )
    bat.add_argument(
        "--image-size",
        default=None,
        help="Default image size when a task doesn't specify one",
    )
    bat.add_argument("--quality", default=None, help="Default openai_images quality")
    bat.add_argument("--background", default=None, help="Default openai_images background")
    bat.add_argument("--output-format", default=None, help="Default openai_images output format")
    bat.add_argument(
        "--num-images",
        type=int,
        default=None,
        help="Default number of images for openai_images tasks",
    )
    bat.add_argument("-o", "--output-dir", default="./output", help="Output directory")
    bat.add_argument(
        "--prefix",
        default=None,
        help="Filename prefix (default: the channel name)",
    )
    bat.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to sleep between tasks (default: 1.0)",
    )
    bat.set_defaults(func=cmd_batch)

    # channel ...
    ch = subparsers.add_parser("channel", help="Manage endpoint channels")
    ch_sub = ch.add_subparsers(dest="channel_command", required=True)

    ch_add = ch_sub.add_parser("add", help="Add or overwrite a channel")
    ch_add.add_argument("name")
    ch_add.add_argument(
        "--protocol",
        required=True,
        choices=["gemini", "openai", "openai_images"],
        help="API protocol this channel speaks",
    )
    ch_add.add_argument("--base-url", required=True)
    ch_add.add_argument("--api-key", required=True)
    ch_add.add_argument("--model", required=True)
    ch_add.add_argument("--force", action="store_true", help="Overwrite if name exists")
    ch_add.set_defaults(func=cmd_channel_add)

    ch_list = ch_sub.add_parser("list", help="List channels")
    ch_list.set_defaults(func=cmd_channel_list)

    ch_show = ch_sub.add_parser("show", help="Show channel details (api key masked)")
    ch_show.add_argument("name", nargs="?", help="Channel name (default: active default)")
    ch_show.set_defaults(func=cmd_channel_show)

    ch_use = ch_sub.add_parser("use", help="Set the default channel")
    ch_use.add_argument("name")
    ch_use.set_defaults(func=cmd_channel_use)

    ch_rm = ch_sub.add_parser("rm", help="Remove a channel")
    ch_rm.add_argument("name")
    ch_rm.set_defaults(func=cmd_channel_rm)

    # history (one-liner: print today's log path; query is shell + jq's job)
    hist = subparsers.add_parser(
        "history",
        help="Print today's history log file path (queries via shell + jq)",
    )
    hist.set_defaults(func=cmd_history)

    status = subparsers.add_parser(
        "status",
        help="Show local per-key limiter occupancy on this machine",
    )
    status.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON for agent flows",
    )
    status.set_defaults(func=cmd_status)

    bench = subparsers.add_parser(
        "bench",
        help="Run a live staged concurrency benchmark from a directory of .md prompts",
    )
    bench.add_argument("source_dir", help="Directory containing prompt .md files")
    bench.add_argument("--channel", help="Channel name (default: configured default)")
    bench.add_argument("--model", help="Override the channel's model for this benchmark")
    bench.add_argument(
        "--concurrency",
        type=int,
        action="append",
        default=None,
        help="Stage concurrency level (repeat for multiple stages; default: 1,5,10,20)",
    )
    bench.add_argument(
        "--aspect-ratio",
        default="2:3",
        help="Aspect ratio for every benchmark call (default: 2:3)",
    )
    bench.add_argument(
        "--image-size",
        default="512",
        help="Image size hint for every benchmark call (default: 512)",
    )
    bench.add_argument(
        "--status-interval",
        type=float,
        default=1.0,
        help="Seconds between limiter occupancy snapshots (default: 1.0)",
    )
    bench.add_argument(
        "-o",
        "--output-dir",
        default="./bench-output",
        help="Output directory for benchmark images",
    )
    bench.set_defaults(func=cmd_bench)

    return parser


def _force_utf8_streams() -> None:
    """Force stdout/stderr to UTF-8 so Chinese prompts and JSON survive
    pipes, redirects, and Windows consoles whose codepage is cp936/cp1252.

    Without this, ``print(json.dumps(..., ensure_ascii=False))`` on Windows
    can raise ``UnicodeEncodeError`` when the prompt contains CJK and the
    process stdout is bound to a non-UTF-8 codepage.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (LookupError, ValueError):
                # Fall back silently — non-text stream or already configured.
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

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
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .channels import CHANNELS_FILE, Channel, ChannelError, ChannelStore, mask_secret
from . import gemini_client
from . import history
from . import openai_client


# ---- small helpers ----


def _fail(message: str) -> int:
    print(f"[q-imgen] {message}", file=sys.stderr)
    return 1


def _emit(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False))


# ---- generate (shared by one-shot and batch) ----


def _run_single(
    channel: Channel,
    *,
    prompt: str,
    reference_images: list[str] | None,
    aspect_ratio: str,
    image_size: str | None,
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
    result: dict[str, Any] = {
        **base_result,
        "status": "error",
        "error": "internal: protocol dispatch did not produce a result",
    }
    started_ns = time.monotonic_ns()
    try:
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
                result = {
                    **base_result,
                    "status": "error",
                    "error": "API returned no images",
                    "texts": texts,
                }
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

        else:
            result = {
                **base_result,
                "status": "error",
                "error": f"unknown protocol: {channel.protocol}",
            }

    except (gemini_client.GeminiError, openai_client.OpenAIError) as exc:
        result = {**base_result, "status": "error", "error": str(exc)}

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
                {"task_index": i, "status": "error", "error": "task is not an object"}
            )
            continue

        if "prompt" not in task:
            results.append(
                {
                    "task_index": i,
                    "status": "error",
                    "error": "task is missing required field: prompt",
                }
            )
            continue

        if not isinstance(task["prompt"], str):
            results.append(
                {
                    "task_index": i,
                    "status": "error",
                    "error": "task field 'prompt' must be a string",
                }
            )
            continue

        result = _run_single(
            active_channel,
            prompt=task["prompt"],
            reference_images=task.get("images"),
            aspect_ratio=task.get("aspect_ratio", args.aspect_ratio),
            image_size=task.get("image_size"),
            output_dir=args.output_dir,
            prefix=f"{batch_prefix}_{i:03d}",
        )
        result["task_index"] = i
        results.append(result)

        if i < len(tasks) - 1:
            time.sleep(args.delay)

    total_ok = sum(1 for r in results if r["status"] == "ok")
    _emit(
        {
            "status": "ok" if total_ok == len(results) else "partial",
            "channel": active_channel.name,
            "model": active_channel.model,
            "total": len(results),
            "ok": total_ok,
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
        "--image-size", default=None, help="Image size hint: 512, 1K, 2K, 4K"
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
        choices=["gemini", "openai"],
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

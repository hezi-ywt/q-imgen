#!/usr/bin/env python3
"""q-imgen - unified wrapper over atomic image engine CLIs."""

from __future__ import annotations

import argparse
import subprocess
import sys

from . import __version__
from .banana_provider import resolve_banana_provider, run_openai_compat_banana
from .config import (
    CONFIG_FILE,
    init_config,
    merged_env,
    show_config,
    update_config,
)
from .routing import canonicalize_engine

ENGINE_MODULES = {
    "midjourney": "midjourney",
    "nanobanana": "nanobanana",
}

ENGINE_ALIASES = {
    "mj": "midjourney",
    "banana": "nanobanana",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="q-imgen",
        description="Unified entrypoint for atomic image generation engines.",
    )
    parser.add_argument("--version", action="version", version=f"q-imgen {__version__}")

    parser.add_argument(
        "target",
        nargs="?",
        help="Subcommand or engine to invoke",
    )
    parser.add_argument(
        "target_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed through to the selected target",
    )
    return parser


def _add_config_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mj-api-key", default=None, help="Set Midjourney API key")
    parser.add_argument("--mj-base-url", default=None, help="Set Midjourney base URL")
    parser.add_argument(
        "--banana-api-key", default=None, help="Set Nano Banana API key"
    )
    parser.add_argument(
        "--banana-base-url", default=None, help="Set Nano Banana base URL"
    )
    parser.add_argument(
        "--banana-model", default=None, help="Set Nano Banana default model"
    )
    parser.add_argument(
        "--banana-provider",
        default=None,
        help="Set Nano Banana provider (gemini or openai)",
    )
    parser.add_argument(
        "--banana-profile",
        default=None,
        help="Set active Nano Banana profile",
    )
    parser.add_argument(
        "--banana-openai-base-url",
        default=None,
        help="Set OpenAI-compatible Banana base URL",
    )
    parser.add_argument(
        "--banana-openai-api-key",
        default=None,
        help="Set OpenAI-compatible Banana API key",
    )
    parser.add_argument(
        "--banana-openai-model",
        default=None,
        help="Set OpenAI-compatible Banana model",
    )


def build_init_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="q-imgen init",
        description="Initialize persistent configuration",
    )
    _add_config_flags(parser)
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing config"
    )
    return parser


def build_config_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="q-imgen config",
        description="Show or update persistent configuration",
    )
    _add_config_flags(parser)
    return parser


def resolve_engine(name: str) -> str:
    return canonicalize_engine(name)


def dispatch(engine: str, engine_args: list[str]) -> int:
    canonical_engine = resolve_engine(engine)
    env = merged_env()

    if canonical_engine == "nanobanana":
        provider = resolve_banana_provider(env)
        if provider == "openai":
            return run_openai_compat_banana(engine_args, env)

    module = ENGINE_MODULES[canonical_engine]
    command = [sys.executable, "-m", module, *engine_args]
    result = subprocess.run(command, check=False, env=env)
    return result.returncode


def handle_config_command(args: argparse.Namespace) -> int:
    updates = {
        "mj_api_key": args.mj_api_key,
        "mj_base_url": args.mj_base_url,
        "banana_api_key": args.banana_api_key,
        "banana_base_url": args.banana_base_url,
        "banana_model": args.banana_model,
        "banana_provider": args.banana_provider,
        "banana_profile": args.banana_profile,
        "banana_openai_base_url": args.banana_openai_base_url,
        "banana_openai_api_key": args.banana_openai_api_key,
        "banana_openai_model": args.banana_openai_model,
    }
    if all(value is None for value in updates.values()):
        current = show_config()
        print(f"  mj_api_key:      {current['mj_api_key']}")
        print(f"  mj_base_url:     {current['mj_base_url']}")
        print(f"  banana_api_key:  {current['banana_api_key']}")
        print(f"  banana_base_url: {current['banana_base_url']}")
        print(f"  banana_model:    {current['banana_model']}")
        print(f"  banana_provider: {current['banana_provider']}")
        print(f"  banana_profile:  {current['banana_profile']}")
        print(f"  banana_openai_base_url: {current['banana_openai_base_url']}")
        print(f"  banana_openai_api_key:  {current['banana_openai_api_key']}")
        print(f"  banana_openai_model:    {current['banana_openai_model']}")
        print(f"  file:            {CONFIG_FILE}")
        return 0

    update_config(**updates)
    print("[q-imgen] Updated configuration.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.target == "init":
        init_args = build_init_parser().parse_args(args.target_args)
        init_config(
            mj_api_key=init_args.mj_api_key,
            mj_base_url=init_args.mj_base_url,
            banana_api_key=init_args.banana_api_key,
            banana_base_url=init_args.banana_base_url,
            banana_model=init_args.banana_model,
            banana_provider=init_args.banana_provider,
            banana_profile=init_args.banana_profile,
            banana_openai_base_url=init_args.banana_openai_base_url,
            banana_openai_api_key=init_args.banana_openai_api_key,
            banana_openai_model=init_args.banana_openai_model,
            force=init_args.force,
        )
        return 0

    if args.target == "config":
        config_args = build_config_parser().parse_args(args.target_args)
        return handle_config_command(config_args)

    if not args.target:
        parser.print_help()
        return 1

    if args.target not in ENGINE_MODULES and args.target not in ENGINE_ALIASES:
        parser.error(f"Unknown target: {args.target}")

    return dispatch(args.target, args.target_args)

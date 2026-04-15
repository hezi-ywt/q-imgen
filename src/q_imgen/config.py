"""Persistent config and env injection for q-imgen."""

from __future__ import annotations

import os
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".q-imgen"
CONFIG_FILE = CONFIG_DIR / ".env"

DEFAULTS = {
    "MJ_BASE_URL": "https://yunwu.ai",
    "NANOBANANA_PROVIDER": "gemini",
    "NANOBANANA_MODEL": "gemini-3.1-flash-image-preview",
}

CONFIG_KEYS = (
    "MJ_API_KEY",
    "MJ_BASE_URL",
    "NANOBANANA_API_KEY",
    "NANOBANANA_BASE_URL",
    "NANOBANANA_MODEL",
    "NANOBANANA_PROVIDER",
    "NANOBANANA_PROFILE",
    "NANOBANANA_OPENAI_BASE_URL",
    "NANOBANANA_OPENAI_API_KEY",
    "NANOBANANA_OPENAI_MODEL",
)


def _load_config_file() -> dict[str, str]:
    if CONFIG_FILE.exists():
        values: dict[str, str] = {}
        for raw_line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
        return values
    return {}


def _sanitize(value: str) -> str:
    return value.replace("\n", "").replace("\r", "")


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return value[:6] + "..." + value[-4:]


def _write_config(values: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)

    lines: list[str] = []
    for key in CONFIG_KEYS:
        value = values.get(key) or DEFAULTS.get(key)
        if value:
            lines.append(f"{key}={_sanitize(value)}")

    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    CONFIG_FILE.chmod(0o600)


def show_config() -> dict[str, str]:
    stored = _load_config_file()
    banana_provider = stored.get("NANOBANANA_PROVIDER", DEFAULTS["NANOBANANA_PROVIDER"])
    if banana_provider == "openai_compat":
        banana_provider = "openai"
    return {
        "mj_api_key": _mask(stored.get("MJ_API_KEY", "")),
        "mj_base_url": stored.get("MJ_BASE_URL", DEFAULTS["MJ_BASE_URL"]),
        "banana_api_key": _mask(stored.get("NANOBANANA_API_KEY", "")),
        "banana_base_url": stored.get("NANOBANANA_BASE_URL", ""),
        "banana_model": stored.get("NANOBANANA_MODEL", DEFAULTS["NANOBANANA_MODEL"]),
        "banana_provider": banana_provider,
        "banana_profile": stored.get("NANOBANANA_PROFILE", ""),
        "banana_openai_base_url": stored.get("NANOBANANA_OPENAI_BASE_URL", ""),
        "banana_openai_api_key": _mask(stored.get("NANOBANANA_OPENAI_API_KEY", "")),
        "banana_openai_model": stored.get("NANOBANANA_OPENAI_MODEL", ""),
    }


def update_config(
    *,
    mj_api_key: str | None = None,
    mj_base_url: str | None = None,
    banana_api_key: str | None = None,
    banana_base_url: str | None = None,
    banana_model: str | None = None,
    banana_provider: str | None = None,
    banana_profile: str | None = None,
    banana_openai_base_url: str | None = None,
    banana_openai_api_key: str | None = None,
    banana_openai_model: str | None = None,
) -> None:
    stored = _load_config_file()

    updates = {
        "MJ_API_KEY": mj_api_key,
        "MJ_BASE_URL": mj_base_url,
        "NANOBANANA_API_KEY": banana_api_key,
        "NANOBANANA_BASE_URL": banana_base_url,
        "NANOBANANA_MODEL": banana_model,
        "NANOBANANA_PROVIDER": banana_provider,
        "NANOBANANA_PROFILE": banana_profile,
        "NANOBANANA_OPENAI_BASE_URL": banana_openai_base_url,
        "NANOBANANA_OPENAI_API_KEY": banana_openai_api_key,
        "NANOBANANA_OPENAI_MODEL": banana_openai_model,
    }

    for key, value in updates.items():
        if value is not None:
            stored[key] = value

    _write_config(stored)


def _ask(label: str, default: str | None = None, required: bool = False) -> str:
    try:
        if default is not None:
            raw = input(f"{label} [{default}]: ").strip()
            return raw or default

        while True:
            raw = input(f"{label}: ").strip()
            if raw or not required:
                return raw
            print("[q-imgen] This field is required.", file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        print("\n[q-imgen] Setup cancelled.", file=sys.stderr)
        sys.exit(1)


def run_first_time_setup() -> None:
    print("[q-imgen] First-run setup — values will be saved to ~/.q-imgen/.env")
    mj_api_key = _ask("Midjourney API key", required=False)
    mj_base_url = _ask("Midjourney base URL", DEFAULTS["MJ_BASE_URL"])
    banana_api_key = _ask("Nano Banana API key", required=False)
    banana_base_url = _ask("Nano Banana base URL", "")
    banana_model = _ask("Nano Banana model", DEFAULTS["NANOBANANA_MODEL"])
    banana_provider = _ask("Nano Banana provider", DEFAULTS["NANOBANANA_PROVIDER"])
    banana_profile = _ask("Nano Banana profile", "")
    banana_openai_base_url = _ask("Nano Banana OpenAI-compatible base URL", "")
    banana_openai_api_key = _ask(
        "Nano Banana OpenAI-compatible API key", required=False
    )
    banana_openai_model = _ask("Nano Banana OpenAI-compatible model", "")

    update_config(
        mj_api_key=mj_api_key,
        mj_base_url=mj_base_url,
        banana_api_key=banana_api_key,
        banana_base_url=banana_base_url,
        banana_model=banana_model,
        banana_provider=banana_provider,
        banana_profile=banana_profile,
        banana_openai_base_url=banana_openai_base_url,
        banana_openai_api_key=banana_openai_api_key,
        banana_openai_model=banana_openai_model,
    )
    print(f"[q-imgen] Configuration saved to {CONFIG_FILE}")


def init_config(
    *,
    mj_api_key: str | None = None,
    mj_base_url: str | None = None,
    banana_api_key: str | None = None,
    banana_base_url: str | None = None,
    banana_model: str | None = None,
    banana_provider: str | None = None,
    banana_profile: str | None = None,
    banana_openai_base_url: str | None = None,
    banana_openai_api_key: str | None = None,
    banana_openai_model: str | None = None,
    force: bool = False,
) -> None:
    if CONFIG_FILE.exists() and not force:
        current = show_config()
        print(f"[q-imgen] Config already exists ({CONFIG_FILE}):")
        print(f"  mj_api_key:      {current['mj_api_key']}")
        print(f"  mj_base_url:     {current['mj_base_url']}")
        print(f"  banana_api_key:  {current['banana_api_key']}")
        print(f"  banana_base_url: {current['banana_base_url']}")
        print(f"  banana_model:    {current['banana_model']}")
        print(f"  banana_provider: {current['banana_provider']}")
        print(f"  banana_profile:  {current['banana_profile']}")
        print(
            "Run with --force to overwrite, or use 'q-imgen config' to update fields."
        )
        return

    provided = any(
        value is not None
        for value in (
            mj_api_key,
            mj_base_url,
            banana_api_key,
            banana_base_url,
            banana_model,
            banana_provider,
            banana_profile,
            banana_openai_base_url,
            banana_openai_api_key,
            banana_openai_model,
        )
    )

    if provided:
        update_config(
            mj_api_key=mj_api_key,
            mj_base_url=mj_base_url or DEFAULTS["MJ_BASE_URL"],
            banana_api_key=banana_api_key,
            banana_base_url=banana_base_url or "",
            banana_model=banana_model or DEFAULTS["NANOBANANA_MODEL"],
            banana_provider=banana_provider or DEFAULTS["NANOBANANA_PROVIDER"],
            banana_profile=banana_profile or "",
            banana_openai_base_url=banana_openai_base_url or "",
            banana_openai_api_key=banana_openai_api_key,
            banana_openai_model=banana_openai_model or "",
        )
        print(f"[q-imgen] Config saved to {CONFIG_FILE}")
        return

    if sys.stdin.isatty():
        run_first_time_setup()
        return

    print(
        "[q-imgen] Non-interactive environment detected. Provide values explicitly, for example:\n"
        "  q-imgen init --mj-api-key <KEY> --banana-api-key <KEY>",
        file=sys.stderr,
    )
    sys.exit(1)


def merged_env() -> dict[str, str]:
    child_env = dict(os.environ)
    stored = _load_config_file()
    for key, value in stored.items():
        child_env.setdefault(key, value)
    for key, value in DEFAULTS.items():
        child_env.setdefault(key, value)
    return child_env

"""Channel storage for q-imgen.

A "channel" is one complete route to an image generation endpoint:
    protocol + base_url + api_key + model

Storage lives at ``~/.q-imgen/channels.json`` with this shape::

    {
        "default": "proxy-a",
        "channels": {
            "proxy-a": {
                "protocol": "openai",
                "base_url": "https://...",
                "api_key": "sk-...",
                "model": "gemini-3.1-flash-image-preview"
            }
        }
    }

There is intentionally no "merge with env vars" layer. One file is the single
source of truth; callers who want to override do so via CLI flags at call
time. This keeps the mental model flat.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

CONFIG_DIR = Path.home() / ".q-imgen"
CHANNELS_FILE = CONFIG_DIR / "channels.json"

VALID_PROTOCOLS: frozenset[str] = frozenset({"gemini", "openai", "openai_images"})


class ChannelError(Exception):
    """Raised for any channel-level user error (unknown name, invalid field, etc.).

    cli.py catches this and translates it to a ``[q-imgen] ...`` stderr line.
    """


@dataclass(frozen=True)
class Channel:
    name: str
    protocol: str    # "gemini", "openai", or "openai_images"
    base_url: str
    api_key: str
    model: str

    def to_dict(self) -> dict[str, str]:
        # Order matters for JSON readability / diffs.
        return {
            "protocol": self.protocol,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
        }


@dataclass
class ChannelStore:
    """In-memory view of channels.json. Use ``load()`` / ``save()`` to persist."""

    channels: dict[str, Channel]
    default: str | None

    # ---- persistence ----

    @classmethod
    def load(cls) -> "ChannelStore":
        if not CHANNELS_FILE.exists():
            return cls(channels={}, default=None)
        try:
            data = json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ChannelError(
                f"{CHANNELS_FILE} is not valid JSON: {exc}. "
                "Fix it by hand or delete the file to start over."
            ) from exc

        raw_channels = data.get("channels") or {}
        channels: dict[str, Channel] = {}
        for name, body in raw_channels.items():
            if not isinstance(body, dict):
                continue
            try:
                channels[name] = Channel(
                    name=name,
                    protocol=body["protocol"],
                    base_url=body["base_url"],
                    api_key=body["api_key"],
                    model=body["model"],
                )
            except KeyError as exc:
                raise ChannelError(
                    f"channel '{name}' in {CHANNELS_FILE} is missing field {exc}"
                ) from exc

        default = data.get("default")
        if default is not None and default not in channels:
            # Stale default — drop it instead of exploding.
            default = None

        return cls(channels=channels, default=default)

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_DIR.chmod(0o700)
        payload = {
            "default": self.default,
            "channels": {
                name: self.channels[name].to_dict()
                for name in sorted(self.channels)
            },
        }
        CHANNELS_FILE.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        CHANNELS_FILE.chmod(0o600)

    # ---- CRUD ----

    def add(
        self,
        name: str,
        *,
        protocol: str,
        base_url: str,
        api_key: str,
        model: str,
        overwrite: bool = False,
    ) -> Channel:
        _validate_name(name)
        if protocol not in VALID_PROTOCOLS:
            raise ChannelError(
                f"unknown protocol '{protocol}' "
                f"(valid: {', '.join(sorted(VALID_PROTOCOLS))})"
            )
        if not base_url:
            raise ChannelError("base_url is required")
        if not api_key:
            raise ChannelError("api_key is required")
        if not model:
            raise ChannelError("model is required")

        if name in self.channels and not overwrite:
            raise ChannelError(
                f"channel '{name}' already exists. Pass --force to overwrite."
            )

        channel = Channel(
            name=name,
            protocol=protocol,
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
        self.channels[name] = channel
        # First channel added becomes default automatically.
        if self.default is None:
            self.default = name
        return channel

    def remove(self, name: str) -> None:
        if name not in self.channels:
            raise ChannelError(f"no such channel: '{name}'")
        del self.channels[name]
        if self.default == name:
            # Pick any remaining channel as the new default, or None if empty.
            self.default = next(iter(self.channels), None)

    def set_default(self, name: str) -> None:
        if name not in self.channels:
            raise ChannelError(f"no such channel: '{name}'")
        self.default = name

    def resolve(self, name: str | None = None) -> Channel:
        """Return the named channel, or the default if ``name`` is None.

        Raises ChannelError with an actionable message if no channel matches —
        this is the path every ``generate`` call takes, so the message has to
        tell the user exactly what to do next.
        """
        if name is not None:
            if name not in self.channels:
                raise ChannelError(
                    f"no such channel: '{name}'. "
                    f"Known: {_format_channel_list(self.channels) or '(none)'}"
                )
            return self.channels[name]

        if self.default is not None and self.default in self.channels:
            return self.channels[self.default]

        if not self.channels:
            raise ChannelError(
                "no channels configured. Run `q-imgen channel add <name> "
                "--protocol {gemini|openai|openai_images} "
                "--base-url URL --api-key KEY --model M` "
                "to create one."
            )

        raise ChannelError(
            "no default channel set. Run `q-imgen channel use <name>` "
            f"to pick one. Known: {_format_channel_list(self.channels)}"
        )

    def names(self) -> list[str]:
        return sorted(self.channels)


# ---- helpers ----


def _validate_name(name: str) -> None:
    if not name:
        raise ChannelError("channel name cannot be empty")
    if "/" in name or "\\" in name:
        raise ChannelError(f"channel name must not contain slashes: {name!r}")


def _format_channel_list(channels: dict[str, Channel]) -> str:
    return ", ".join(sorted(channels))


def mask_secret(value: str) -> str:
    """Same mask style used elsewhere in the codebase."""
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return value[:6] + "..." + value[-4:]

"""Banana provider/profile resolution and OpenAI-compatible execution."""

from __future__ import annotations

import argparse
import base64
import io
import json
import mimetypes
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass(frozen=True)
class BananaSettings:
    provider: str = "gemini"
    profile: str = ""
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = ""


def _profile_key(profile: str, suffix: str) -> str:
    return f"NANOBANANA_PROFILE_{profile.upper()}_{suffix}"


def _resolve_with_profile(
    env: dict[str, str], profile: str, generic_key: str, profile_suffix: str
) -> str:
    if profile:
        profiled = env.get(_profile_key(profile, profile_suffix), "")
        if profiled:
            return profiled
    return env.get(generic_key, "")


def resolve_banana_provider(env: dict[str, str]) -> str:
    profile = env.get("NANOBANANA_PROFILE", "")
    provider = _resolve_with_profile(env, profile, "NANOBANANA_PROVIDER", "PROVIDER")
    normalized = (provider or "gemini").strip().lower()
    if normalized == "openai_compat":
        return "openai"
    return normalized or "gemini"


def resolve_banana_settings(env: dict[str, str]) -> BananaSettings:
    profile = env.get("NANOBANANA_PROFILE", "")
    return BananaSettings(
        provider=resolve_banana_provider(env),
        profile=profile,
        openai_base_url=_resolve_with_profile(
            env,
            profile,
            "NANOBANANA_OPENAI_BASE_URL",
            "OPENAI_BASE_URL",
        ),
        openai_api_key=_resolve_with_profile(
            env,
            profile,
            "NANOBANANA_OPENAI_API_KEY",
            "OPENAI_API_KEY",
        ),
        openai_model=_resolve_with_profile(
            env,
            profile,
            "NANOBANANA_OPENAI_MODEL",
            "OPENAI_MODEL",
        ),
    )


def _encode_image_data_url(path: str) -> str:
    image_path = Path(path)
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    image = Image.open(image_path)
    buffer = io.BytesIO()
    image_format = "PNG" if mime_type == "image/png" else "JPEG"
    save_kwargs = {"format": image_format}
    if image_format == "JPEG":
        save_kwargs["quality"] = 85
    image.save(buffer, **save_kwargs)
    data = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:{mime_type};base64,{data}"


def _save_openai_images(images: list[dict], output_dir: str, prefix: str) -> list[str]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for index, image_data in enumerate(images):
        url = image_data.get("image_url", {}).get("url", "")
        if not url.startswith("data:"):
            continue
        header, b64_data = url.split(",", 1)
        ext = ".png"
        if "jpeg" in header:
            ext = ".jpg"
        elif "webp" in header:
            ext = ".webp"
        file_path = out_dir / f"{prefix}_{index:03d}{ext}"
        file_path.write_bytes(base64.b64decode(b64_data))
        saved.append(str(file_path))
    return saved


def _build_openai_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="q-imgen banana")
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen = subparsers.add_parser("generate")
    gen.add_argument("prompt")
    gen.add_argument("--images", nargs="+")
    gen.add_argument("--aspect-ratio", default="3:4")
    gen.add_argument("--image-size", default=None)
    gen.add_argument("--output-dir", default="./output")
    gen.add_argument("--prefix", default="img")

    return parser


def run_openai_compat_banana(args: list[str], env: dict[str, str]) -> int:
    parsed = _build_openai_parser().parse_args(args)
    settings = resolve_banana_settings(env)

    if (
        not settings.openai_base_url
        or not settings.openai_api_key
        or not settings.openai_model
    ):
        raise RuntimeError(
            "OpenAI-compatible Banana provider requires base URL, API key, and model"
        )

    content: list[dict] = []
    if parsed.images:
        for image_path in parsed.images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _encode_image_data_url(image_path)},
                }
            )
    content.append({"type": "text", "text": parsed.prompt})

    payload = {
        "model": settings.openai_model,
        "messages": [{"role": "user", "content": content}],
        "image_config": {
            "image_size": parsed.image_size,
            "aspect_ratio": parsed.aspect_ratio,
        },
    }

    req = urllib.request.Request(
        f"{settings.openai_base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=300) as response:
        body = json.loads(response.read().decode("utf-8"))

    images = body.get("choices", [{}])[0].get("message", {}).get("images", [])
    if not images:
        print(json.dumps({"status": "error", "error": "No images in response"}))
        return 1

    saved = _save_openai_images(images, parsed.output_dir, parsed.prefix)
    print(
        json.dumps(
            {
                "status": "ok",
                "model": settings.openai_model,
                "prompt": parsed.prompt,
                "images": saved,
                "ref_images": parsed.images or [],
            },
            ensure_ascii=False,
        )
    )
    return 0

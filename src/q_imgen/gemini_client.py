"""Gemini-native image generation.

Protocol: ``POST {base_url}/models/{model}:generateContent`` with a Gemini
``contents/parts/generationConfig`` payload.

Handles both authentication styles the same endpoint accepts:
- ``generativelanguage.googleapis.com`` → ``?key=API_KEY`` query param
- Proxy gateways → ``Authorization: Bearer <key>`` header

Absorbed from the original nanobanana client so q-imgen is self-contained
and does not depend on a loose local package.
"""

from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image as PILImage

_TIMEOUT_SECONDS = 300
_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 5

# Gemini inline_data rejects anything outside this set, so we reject early
# instead of getting a cryptic 400 back.
_SUPPORTED_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp"}
_EXT_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class GeminiError(Exception):
    """User-visible error from the Gemini path. cli.py translates to stderr."""


def _mime_for(path: Path) -> str:
    mime = _EXT_TO_MIME.get(path.suffix.lower())
    if mime is None:
        raise GeminiError(
            f"unsupported image type for Gemini: {path.name} "
            f"(supported: {', '.join(sorted(_SUPPORTED_IMAGE_MIME))})"
        )
    return mime


def _pil_to_inline(image: PILImage.Image, mime: str = "image/png") -> dict[str, Any]:
    """Encode a PIL Image as a Gemini ``inline_data`` part."""
    fmt = "PNG" if mime == "image/png" else "JPEG" if mime in ("image/jpeg",) else "WEBP"
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return {
        "inline_data": {
            "mime_type": mime,
            "data": base64.b64encode(buf.getvalue()).decode("utf-8"),
        }
    }


def _load_image_inline(path: str | Path | PILImage.Image) -> dict[str, Any]:
    if isinstance(path, PILImage.Image):
        fmt = (path.format or "PNG").upper()
        mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
            fmt, "image/png"
        )
        return _pil_to_inline(path, mime)
    p = Path(path)
    if not p.exists():
        raise GeminiError(f"reference image not found: {path}")
    mime = _mime_for(p)
    return {
        "inline_data": {
            "mime_type": mime,
            "data": base64.b64encode(p.read_bytes()).decode("utf-8"),
        }
    }


def _sanitize_error(message: str, api_key: str) -> str:
    """Strip live API key from error messages before surfacing them."""
    if api_key and api_key in message:
        return message.replace(api_key, "<redacted>")
    return message


def _post_json(
    url: str,
    payload: dict,
    api_key: str,
    base_url: str,
    timeout: float,
    max_retries: int = _MAX_RETRIES,
) -> dict:
    payload_bytes = json.dumps(payload).encode("utf-8")

    # Google's first-party endpoint wants the key as a query param; every
    # third-party proxy I've seen uses Bearer. Branch on hostname so the user
    # doesn't have to configure the auth style separately.
    if "googleapis.com" in base_url:
        full_url = f"{url}?key={api_key}"
        headers = {"Content-Type": "application/json"}
    else:
        full_url = url
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    req = urllib.request.Request(
        full_url, data=payload_bytes, headers=headers, method="POST"
    )

    last_error: str | None = None
    for attempt in range(max_retries + 1):
        if attempt > 0:
            time.sleep(_RETRY_DELAY_SECONDS)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        except urllib.error.HTTPError as e:
            status = e.code
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            detail = _sanitize_error(body.strip(), api_key)
            message = f"HTTP {status}" + (f": {detail}" if detail else "")

            # 4xx (except 429 rate limit) are permanent — don't retry.
            if 400 <= status < 500 and status != 429:
                raise GeminiError(message)
            last_error = message

        except urllib.error.URLError as e:
            last_error = f"URL error: {_sanitize_error(str(e.reason), api_key)}"

        except TimeoutError:
            last_error = f"request timed out after {timeout}s"

    raise GeminiError(f"all retries exhausted. Last error: {last_error}")


def generate(
    *,
    prompt: str,
    base_url: str,
    api_key: str,
    model: str,
    reference_images: list[str | Path | PILImage.Image] | None = None,
    aspect_ratio: str = "3:4",
    image_size: str | None = None,
    timeout: float = _TIMEOUT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> dict:
    """One-shot generation. Returns the raw Gemini API response dict.

    Keeping the raw response lets callers pull ``candidates[].content.parts``
    themselves if they want text output too. ``extract_images`` handles the
    common case.
    """
    parts: list[dict[str, Any]] = []
    if reference_images:
        for img in reference_images:
            parts.append(_load_image_inline(img))
    parts.append({"text": prompt.strip()})

    image_config: dict[str, Any] = {"aspectRatio": aspect_ratio}
    if image_size:
        image_config["imageSize"] = image_size

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": image_config,
        },
    }

    url = f"{base_url.rstrip('/')}/models/{model}:generateContent"
    return _post_json(url, payload, api_key, base_url, timeout, max_retries)


def generate_images(
    *,
    prompt: str,
    base_url: str,
    api_key: str,
    model: str,
    reference_images: list[str | Path | PILImage.Image] | None = None,
    aspect_ratio: str = "3:4",
    image_size: str | None = None,
    timeout: float = _TIMEOUT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> list[PILImage.Image]:
    """Generate images and return as PIL Image objects (no disk I/O).

    Raises ``GeminiError`` on failure.
    """
    response = generate(
        prompt=prompt,
        base_url=base_url,
        api_key=api_key,
        model=model,
        reference_images=reference_images,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        timeout=timeout,
        max_retries=max_retries,
    )
    raw_images, _ = extract_images(response)
    if not raw_images:
        raise GeminiError("API returned no images")
    return [
        PILImage.open(io.BytesIO(base64.b64decode(img["data"])))
        for img in raw_images
    ]


def extract_images(response: dict) -> tuple[list[dict], list[str]]:
    """Pull ``(images, texts)`` out of a Gemini response.

    Each image dict is ``{"mime_type": str, "data": str (b64)}``.
    """
    images: list[dict] = []
    texts: list[str] = []

    for candidate in response.get("candidates", []):
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if part.get("text"):
                texts.append(part["text"])
            # API responses use camelCase (inlineData); our request payload
            # uses snake_case (inline_data). Both can appear on the wire.
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                mime = (
                    inline.get("mimeType")
                    or inline.get("mime_type")
                    or "image/png"
                )
                images.append({"mime_type": mime, "data": inline["data"]})

    return images, texts


def _unique_output_path(out: Path, base: str, ext: str) -> Path:
    """Return ``out/base+ext`` if free, else ``out/base_1+ext``, ``_2``, ...

    Prevents silent overwrites when two runs (often from different channels
    or models) would land on the same filename. The cost is one extra
    ``stat`` per image, which is negligible next to the network call that
    just completed. ``base`` is e.g. ``"img_000"``; suffix is appended
    *before* the extension so file managers still detect the type.
    """
    candidate = out / f"{base}{ext}"
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        candidate = out / f"{base}_{counter}{ext}"
        if not candidate.exists():
            return candidate
        counter += 1


def save_images(
    images: list[dict], output_dir: str | Path, prefix: str
) -> list[str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    ext_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }
    saved: list[str] = []
    for i, img in enumerate(images):
        ext = ext_map.get(img["mime_type"], ".png")
        file_path = _unique_output_path(out, f"{prefix}_{i:03d}", ext)
        file_path.write_bytes(base64.b64decode(img["data"]))
        saved.append(str(file_path))
    return saved

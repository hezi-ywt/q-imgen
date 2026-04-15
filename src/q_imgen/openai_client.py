"""OpenAI-compatible image generation.

Protocol: ``POST {base_url}/chat/completions`` with an OpenAI-style
``messages`` payload, expecting generated images back somewhere in
``choices[0].message``. Different gateways put them in different places —
see ``_extract_images_from_response`` for the full list of recognized
shapes (``message.images[]``, markdown in ``message.content`` strings,
and OpenAI vision-style content parts arrays). All shapes are scanned in
parallel and deduped by URL.

This path hits gateways that re-expose image-generating models behind the
OpenAI chat schema. The payload format is **not** interchangeable with the
Gemini-native path in ``gemini_client`` — do not merge them.
"""

from __future__ import annotations

import base64
import io
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

# Re-encode cap for inline reference images. Keeps payloads under typical API
# limits while staying large enough that reference quality isn't wrecked.
_MAX_IMAGE_EDGE = 2048
_JPEG_QUALITY = 90
_TIMEOUT_SECONDS = 300
_DOWNLOAD_TIMEOUT_SECONDS = 120


class OpenAIError(Exception):
    """User-visible error from the OpenAI-compat path."""


# ---- request encoding ----


def _encode_image_data_url(path: str) -> str:
    """Read an image, resize if oversized, and return a ``data:`` URL.

    The mime type and the actual encoded bytes stay consistent: PNG in → PNG
    out, everything else → JPEG (with RGBA flattened to white). Strict
    OpenAI-compat backends reject mismatches.
    """
    image_path = Path(path)
    if not image_path.exists():
        raise OpenAIError(f"reference image not found: {path}")

    with Image.open(image_path) as image:
        image.load()
        source_format = (image.format or "").upper()

        if source_format == "PNG":
            mime_type = "image/png"
            save_kwargs: dict[str, object] = {"format": "PNG", "optimize": True}
            encoded = image
        else:
            mime_type = "image/jpeg"
            save_kwargs = {
                "format": "JPEG",
                "quality": _JPEG_QUALITY,
                "optimize": True,
            }
            if image.mode not in ("RGB", "L"):
                background = Image.new("RGB", image.size, (255, 255, 255))
                if image.mode in ("RGBA", "LA"):
                    background.paste(image, mask=image.split()[-1])
                else:
                    background.paste(image.convert("RGB"))
                encoded = background
            else:
                encoded = image

        if max(encoded.size) > _MAX_IMAGE_EDGE:
            encoded = encoded.copy()
            encoded.thumbnail((_MAX_IMAGE_EDGE, _MAX_IMAGE_EDGE), Image.LANCZOS)

        buffer = io.BytesIO()
        encoded.save(buffer, **save_kwargs)

    data = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:{mime_type};base64,{data}"


# ---- error sanitization ----


def _sanitize_error(message: str, api_key: str) -> str:
    """Strip the live API key and obvious token patterns from error text."""
    cleaned = message.replace(api_key, "<redacted>") if api_key else message
    cleaned = re.sub(
        r"Bearer\s+[A-Za-z0-9._\-]+",
        "Bearer <redacted>",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"sk-[A-Za-z0-9]{6,}", "sk-<redacted>", cleaned)
    return cleaned


# ---- response decoding ----


# Matches markdown image syntax: ![alt](url).
#
# Why ``[^)\s]+`` for the URL group:
# - Base64 data URLs use the alphabet [A-Za-z0-9+/=], no parens — safe.
# - HTTP URLs with query strings use [?&=] — safe.
# - Excluding whitespace in addition to `)` defends against the rare gateway
#   that doesn't escape the URL and relies on whitespace as a delimiter.
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


def _extract_images_from_response(body: dict) -> list[dict]:
    """Find image URLs anywhere in a chat/completions response.

    OpenAI-compatible image-generation gateways are not standardized on where
    they put generated images — the ``/chat/completions`` response shape
    differs across one-api / new-api / litellm / yunwu and other forks.
    Rather than guessing which one a gateway uses, we **scan all known
    locations and merge the results**, deduping by URL.

    Recognized locations (all checked, results merged in encounter order):

    1. ``choices[0].message.images[]`` — explicit array, each entry of the
       form ``{"image_url": {"url": "..."}}``. Used by sd.rnglg2.top and a
       few other proxies as a non-standard extension.

    2. **Markdown** ``![alt](url)`` **inside ``choices[0].message.content``**
       (string). This is the **most common shape** across one-api / new-api /
       litellm / yunwu and most public OpenAI-compat image gateways. The URL
       may be a ``data:image/...;base64,...`` payload or an
       ``http(s)://...`` link. Multiple images per response are supported.

    3. ``choices[0].message.content`` as a list of OpenAI vision-style parts
       (``[{"type": "image_url", "image_url": {"url": "..."}}, ...]``). Some
       proxies symmetric-encode their output the same way they accept input.
       Text parts inside this list are also scanned for markdown image syntax.

    Returns a list of ``{"image_url": {"url": ...}}`` records ready for
    ``_save_response_images``. Returns ``[]`` if nothing matched anywhere —
    callers translate that into an OpenAIError with the "no images" message.
    """
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return []

    seen: set[str] = set()
    records: list[dict] = []

    def _add(url: str | None) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        records.append({"image_url": {"url": url}})

    # Shape 1: explicit images[] array.
    for entry in message.get("images") or []:
        if isinstance(entry, dict):
            _add(entry.get("image_url", {}).get("url"))

    # Shape 2 / 3: scan message.content (string or list-of-parts).
    content = message.get("content")
    if isinstance(content, str):
        for url in _MARKDOWN_IMAGE_RE.findall(content):
            _add(url)
    elif isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url":
                _add(part.get("image_url", {}).get("url"))
            elif isinstance(part.get("text"), str):
                for url in _MARKDOWN_IMAGE_RE.findall(part["text"]):
                    _add(url)

    return records


def _ext_from_content_type(content_type: str) -> str:
    ct = content_type.lower()
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    return ".png"


def _save_response_images(
    images: list[dict], output_dir: str | Path, prefix: str
) -> list[str]:
    """Persist images from a chat/completions response.

    Supports both inline ``data:`` URLs and remote ``http(s)://`` URLs — some
    gateways return one, some the other. A single failed download doesn't
    abort the whole save.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for index, image_data in enumerate(images):
        url = image_data.get("image_url", {}).get("url", "")
        if not url:
            continue

        if url.startswith("data:"):
            header, _, b64_data = url.partition(",")
            if not b64_data:
                continue
            ext = _ext_from_content_type(header)
            payload = base64.b64decode(b64_data)
        elif url.startswith(("http://", "https://")):
            try:
                with urllib.request.urlopen(
                    url, timeout=_DOWNLOAD_TIMEOUT_SECONDS
                ) as response:
                    payload = response.read()
                    content_type = response.headers.get("Content-Type", "")
            except (urllib.error.URLError, TimeoutError):
                # Don't explode the whole call on one bad image — skip and
                # let the caller see "returned 2 of 3" in saved length.
                continue
            ext = _ext_from_content_type(content_type)
        else:
            continue

        file_path = out / f"{prefix}_{index:03d}{ext}"
        file_path.write_bytes(payload)
        saved.append(str(file_path))
    return saved


# ---- main entry ----


def generate(
    *,
    prompt: str,
    base_url: str,
    api_key: str,
    model: str,
    reference_images: list[str] | None = None,
    aspect_ratio: str = "3:4",
    image_size: str | None = None,
    output_dir: str | Path = "./output",
    prefix: str = "img",
    timeout: float = _TIMEOUT_SECONDS,
) -> list[str]:
    """Send one request and return saved image paths.

    Raises ``OpenAIError`` on any network / protocol / save failure so cli.py
    can translate it to a single ``[q-imgen] ...`` stderr line.
    """
    content: list[dict] = []
    if reference_images:
        for img in reference_images:
            content.append(
                {"type": "image_url", "image_url": {"url": _encode_image_data_url(img)}}
            )
    content.append({"type": "text", "text": prompt})

    image_config: dict[str, object] = {"aspect_ratio": aspect_ratio}
    if image_size:
        image_config["image_size"] = image_size

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "image_config": image_config,
    }

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body_text = ""
        detail = _sanitize_error(body_text.strip(), api_key)
        suffix = f": {detail}" if detail else ""
        raise OpenAIError(f"API request failed with HTTP {exc.code}{suffix}") from exc
    except urllib.error.URLError as exc:
        raise OpenAIError(
            f"failed to reach {base_url}: "
            f"{_sanitize_error(str(exc.reason), api_key)}"
        ) from exc
    except TimeoutError as exc:
        raise OpenAIError(
            f"API request timed out after {timeout}s ({base_url})"
        ) from exc

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OpenAIError(f"API returned non-JSON response: {exc}") from exc

    images = _extract_images_from_response(body)
    if not images:
        raise OpenAIError("API response contained no images")

    saved = _save_response_images(images, output_dir, prefix)
    if not saved:
        raise OpenAIError(
            "API returned images but none could be saved (unknown URL format)"
        )
    return saved

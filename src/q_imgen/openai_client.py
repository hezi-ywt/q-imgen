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
_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 5
_DOWNLOAD_TIMEOUT_SECONDS = 120


class OpenAIError(Exception):
    """User-visible error from the OpenAI-compat path."""


# ---- request encoding ----


def _encode_image_data_url(path: str | Path | Image.Image) -> str:
    """Read an image, resize if oversized, and return a ``data:`` URL.

    Accepts a file path (str/Path) or an in-memory PIL Image.

    The mime type and the actual encoded bytes stay consistent: PNG in → PNG
    out, everything else → JPEG (with RGBA flattened to white). Strict
    OpenAI-compat backends reject mismatches.
    """
    if isinstance(path, Image.Image):
        image = path
        source_format = (image.format or "").upper()
    else:
        image_path = Path(path)
        if not image_path.exists():
            raise OpenAIError(f"reference image not found: {path}")
        image = Image.open(image_path)
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
    differs across one-api / new-api / litellm / various proxies and other forks.
    Rather than guessing which one a gateway uses, we **scan all known
    locations and merge the results**, deduping by URL.

    Recognized locations (all checked, results merged in encounter order):

    1. ``choices[0].message.images[]`` — explicit array, each entry of the
       form ``{"image_url": {"url": "..."}}``. Used by proxy.example.com and a
       few other proxies as a non-standard extension.

    2. **Markdown** ``![alt](url)`` **inside ``choices[0].message.content``**
       (string). This is the **most common shape** across one-api / new-api /
       litellm / various proxies and most public OpenAI-compat image gateways. The URL
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


def _unique_output_path(out: Path, base: str, ext: str) -> Path:
    """Return ``out/base+ext`` if free, else ``out/base_1+ext``, ``_2``, ...

    Prevents silent overwrites when two runs (often from different channels
    or models) would land on the same filename. Mirrors the helper in
    ``gemini_client``; intentionally duplicated to keep the two protocol
    clients independent (see CLAUDE.md "do not unify them").
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

        file_path = _unique_output_path(out, f"{prefix}_{index:03d}", ext)
        file_path.write_bytes(payload)
        saved.append(str(file_path))
    return saved


# ---- HTTP call ----


def _call_api(
    *,
    prompt: str,
    base_url: str,
    api_key: str,
    model: str,
    reference_images: list[str | Path | Image.Image] | None = None,
    aspect_ratio: str = "3:4",
    image_size: str | None = None,
    timeout: float = _TIMEOUT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> list[dict]:
    """Send one request and return extracted image records.

    Returns a list of ``{"image_url": {"url": ...}}`` dicts. Raises
    ``OpenAIError`` on failure.
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

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data_bytes = json.dumps(payload).encode("utf-8")

    last_error: str | None = None
    for attempt in range(max_retries + 1):
        if attempt > 0:
            import time
            time.sleep(_RETRY_DELAY_SECONDS)

        req = urllib.request.Request(
            url, data=data_bytes, headers=headers, method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")

            try:
                body = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise OpenAIError(f"API returned non-JSON response: {exc}") from exc

            images = _extract_images_from_response(body)
            if not images:
                raise OpenAIError("API response contained no images")
            return images

        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body_text = ""
            detail = _sanitize_error(body_text.strip(), api_key)
            message = f"API request failed with HTTP {status}"
            if detail:
                message += f": {detail}"

            if 400 <= status < 500 and status != 429:
                raise OpenAIError(message) from exc
            last_error = message

        except urllib.error.URLError as exc:
            last_error = (
                f"failed to reach {base_url}: "
                f"{_sanitize_error(str(exc.reason), api_key)}"
            )

        except TimeoutError:
            last_error = f"API request timed out after {timeout}s ({base_url})"

    raise OpenAIError(f"all retries exhausted. Last error: {last_error}")


def _image_records_to_pil(records: list[dict]) -> list[Image.Image]:
    """Convert extracted image records to PIL Image objects."""
    result: list[Image.Image] = []
    for rec in records:
        url = rec.get("image_url", {}).get("url", "")
        if not url:
            continue
        if url.startswith("data:"):
            _, _, b64_data = url.partition(",")
            if not b64_data:
                continue
            result.append(Image.open(io.BytesIO(base64.b64decode(b64_data))))
        elif url.startswith(("http://", "https://")):
            try:
                with urllib.request.urlopen(
                    url, timeout=_DOWNLOAD_TIMEOUT_SECONDS
                ) as response:
                    result.append(Image.open(io.BytesIO(response.read())))
            except (urllib.error.URLError, TimeoutError):
                continue
    return result


# ---- main entries ----


def generate_images(
    *,
    prompt: str,
    base_url: str,
    api_key: str,
    model: str,
    reference_images: list[str | Path | Image.Image] | None = None,
    aspect_ratio: str = "3:4",
    image_size: str | None = None,
    timeout: float = _TIMEOUT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> list[Image.Image]:
    """Generate images and return as PIL Image objects (no disk I/O).

    Raises ``OpenAIError`` on failure.
    """
    records = _call_api(
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
    pil_images = _image_records_to_pil(records)
    if not pil_images:
        raise OpenAIError(
            "API returned images but none could be decoded (unknown URL format)"
        )
    return pil_images


def generate(
    *,
    prompt: str,
    base_url: str,
    api_key: str,
    model: str,
    reference_images: list[str | Path | Image.Image] | None = None,
    aspect_ratio: str = "3:4",
    image_size: str | None = None,
    output_dir: str | Path = "./output",
    prefix: str = "img",
    timeout: float = _TIMEOUT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> list[str]:
    """Send one request and return saved image paths.

    Raises ``OpenAIError`` on any network / protocol / save failure so cli.py
    can translate it to a single ``[q-imgen] ...`` stderr line.
    """
    records = _call_api(
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
    saved = _save_response_images(records, output_dir, prefix)
    if not saved:
        raise OpenAIError(
            "API returned images but none could be saved (unknown URL format)"
        )
    return saved

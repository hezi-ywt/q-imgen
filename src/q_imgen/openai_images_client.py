"""OpenAI Images endpoint generation.

Protocol: ``POST {base_url}/images/generations`` with an OpenAI Images-style
payload. This is separate from ``openai_client`` because chat/completions image
gateways use a different request and response contract.
"""

from __future__ import annotations

import base64
import http.client
import io
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

_MAX_IMAGE_EDGE = 2048
_JPEG_QUALITY = 90
_TIMEOUT_SECONDS = 300
_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 5
_DOWNLOAD_TIMEOUT_SECONDS = 120

_ASPECT_RATIO_TO_SIZE = {
    "1:1": "1024x1024",
    "2:3": "1024x1536",
    "3:2": "1536x1024",
    "3:4": "1152x1536",
    "4:3": "1536x1152",
    "9:16": "1024x1792",
    "16:9": "1792x1024",
}


class OpenAIImagesError(Exception):
    """User-visible error from the OpenAI Images path."""


def _size_for(*, aspect_ratio: str, image_size: str | None) -> str:
    if image_size:
        return image_size
    return _ASPECT_RATIO_TO_SIZE.get(aspect_ratio.strip(), "1024x1536")


def _encode_image_data_url(path: str | Path | Image.Image) -> str:
    if isinstance(path, Image.Image):
        image = path
        source_format = (image.format or "").upper()
    else:
        image_path = Path(path)
        if not image_path.exists():
            raise OpenAIImagesError(f"reference image not found: {path}")
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


def _sanitize_error(message: str, api_key: str) -> str:
    cleaned = message.replace(api_key, "<redacted>") if api_key else message
    cleaned = re.sub(
        r"Bearer\s+[A-Za-z0-9._\-]+",
        "Bearer <redacted>",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"sk-[A-Za-z0-9]{6,}", "sk-<redacted>", cleaned)
    return cleaned


def _ext_from_content_type(content_type: str) -> str:
    ct = content_type.lower()
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    return ".png"


def _ext_from_output_format(output_format: str | None) -> str:
    fmt = (output_format or "").strip().lower()
    if fmt in {"jpeg", "jpg"}:
        return ".jpg"
    if fmt == "webp":
        return ".webp"
    if fmt == "png":
        return ".png"
    return ".png"


def _unique_output_path(out: Path, base: str, ext: str) -> Path:
    candidate = out / f"{base}{ext}"
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        candidate = out / f"{base}_{counter}{ext}"
        if not candidate.exists():
            return candidate
        counter += 1


def _decode_b64_payload(
    value: str,
    *,
    fallback_ext: str = ".png",
) -> tuple[bytes, str]:
    if value.startswith("data:"):
        header, _, payload = value.partition(",")
        return base64.b64decode(payload), _ext_from_content_type(header)
    return base64.b64decode(value), fallback_ext


def _call_api(
    *,
    prompt: str,
    base_url: str,
    api_key: str,
    model: str,
    reference_images: list[str | Path | Image.Image] | None = None,
    aspect_ratio: str = "3:4",
    image_size: str | None = None,
    quality: str | None = None,
    background: str | None = None,
    output_format: str | None = None,
    num_images: int | None = None,
    timeout: float = _TIMEOUT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> list[dict]:
    payload: dict[str, object] = {
        "model": model,
        "prompt": prompt,
        "size": _size_for(aspect_ratio=aspect_ratio, image_size=image_size),
    }
    if reference_images:
        payload["input_images"] = [
            _encode_image_data_url(image) for image in reference_images
        ]
    if quality:
        payload["quality"] = quality
    if background:
        payload["background"] = background
    if output_format:
        payload["output_format"] = output_format
    if num_images is not None:
        payload["n"] = num_images

    url = f"{base_url.rstrip('/')}/images/generations"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data_bytes = json.dumps(payload).encode("utf-8")

    last_error: str | None = None
    for attempt in range(max_retries + 1):
        if attempt > 0:
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
                raise OpenAIImagesError(
                    f"API returned non-JSON response: {exc}"
                ) from exc

            records = body.get("data") or []
            if not isinstance(records, list) or not records:
                raise OpenAIImagesError("API response contained no images")
            return [record for record in records if isinstance(record, dict)]

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

            if "unknown parameter" in detail.lower():
                raise OpenAIImagesError(message) from exc
            if 400 <= status < 500 and status != 429:
                raise OpenAIImagesError(message) from exc
            last_error = message

        except urllib.error.URLError as exc:
            last_error = (
                f"failed to reach {base_url}: "
                f"{_sanitize_error(str(exc.reason), api_key)}"
            )

        except TimeoutError:
            last_error = f"API request timed out after {timeout}s ({base_url})"

        except http.client.RemoteDisconnected as exc:
            last_error = (
                f"remote closed connection while calling {base_url}: "
                f"{_sanitize_error(str(exc), api_key)}"
            )

    raise OpenAIImagesError(f"all retries exhausted. Last error: {last_error}")


def _records_to_pil(records: list[dict]) -> list[Image.Image]:
    result: list[Image.Image] = []
    for record in records:
        if record.get("b64_json"):
            payload, _ = _decode_b64_payload(str(record["b64_json"]))
            result.append(Image.open(io.BytesIO(payload)))
        elif record.get("url"):
            try:
                with urllib.request.urlopen(
                    str(record["url"]), timeout=_DOWNLOAD_TIMEOUT_SECONDS
                ) as response:
                    result.append(Image.open(io.BytesIO(response.read())))
            except (urllib.error.URLError, TimeoutError):
                continue
    return result


def _save_records(
    records: list[dict],
    output_dir: str | Path,
    prefix: str,
    *,
    output_format: str | None = None,
) -> list[str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    fallback_ext = _ext_from_output_format(output_format)
    for index, record in enumerate(records):
        if record.get("b64_json"):
            payload, ext = _decode_b64_payload(
                str(record["b64_json"]),
                fallback_ext=fallback_ext,
            )
        elif record.get("url"):
            try:
                with urllib.request.urlopen(
                    str(record["url"]), timeout=_DOWNLOAD_TIMEOUT_SECONDS
                ) as response:
                    payload = response.read()
                    ext = _ext_from_content_type(response.headers.get("Content-Type", ""))
            except (urllib.error.URLError, TimeoutError):
                continue
        else:
            continue

        file_path = _unique_output_path(out, f"{prefix}_{index:03d}", ext)
        file_path.write_bytes(payload)
        saved.append(str(file_path))
    return saved


def generate_images(
    *,
    prompt: str,
    base_url: str,
    api_key: str,
    model: str,
    reference_images: list[str | Path | Image.Image] | None = None,
    aspect_ratio: str = "3:4",
    image_size: str | None = None,
    quality: str | None = None,
    background: str | None = None,
    output_format: str | None = None,
    num_images: int | None = None,
    timeout: float = _TIMEOUT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> list[Image.Image]:
    records = _call_api(
        prompt=prompt,
        base_url=base_url,
        api_key=api_key,
        model=model,
        reference_images=reference_images,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        quality=quality,
        background=background,
        output_format=output_format,
        num_images=num_images,
        timeout=timeout,
        max_retries=max_retries,
    )
    images = _records_to_pil(records)
    if not images:
        raise OpenAIImagesError(
            "API returned images but none could be decoded (unknown response format)"
        )
    return images


def generate(
    *,
    prompt: str,
    base_url: str,
    api_key: str,
    model: str,
    reference_images: list[str | Path | Image.Image] | None = None,
    aspect_ratio: str = "3:4",
    image_size: str | None = None,
    quality: str | None = None,
    background: str | None = None,
    output_format: str | None = None,
    num_images: int | None = None,
    output_dir: str | Path = "./output",
    prefix: str = "img",
    timeout: float = _TIMEOUT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> list[str]:
    records = _call_api(
        prompt=prompt,
        base_url=base_url,
        api_key=api_key,
        model=model,
        reference_images=reference_images,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        quality=quality,
        background=background,
        output_format=output_format,
        num_images=num_images,
        timeout=timeout,
        max_retries=max_retries,
    )
    saved = _save_records(records, output_dir, prefix, output_format=output_format)
    if not saved:
        raise OpenAIImagesError(
            "API returned images but none could be saved (unknown response format)"
        )
    return saved

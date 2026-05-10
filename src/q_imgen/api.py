"""Public Python API for q-imgen.

Usage::

    from q_imgen import generate

    images = generate("a cute cat", channel="my-proxy")
    for img in images:
        img.save("cat.png")

Returns ``list[PIL.Image.Image]``. Raises on failure. Does not save files,
write history, or print anything.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .channels import ChannelStore

_TIMEOUT_SECONDS = 300
_MAX_RETRIES = 3
_MAX_IMAGE_EDGE = 2048


def _prepare_image(img: str | Path | Image.Image) -> str | Path | Image.Image:
    """Resize oversized PIL Images before sending to the API.

    File paths are left as-is — the protocol clients handle their own
    file-based encoding. Only in-memory PIL Images get resized here so
    that both protocol paths benefit from a consistent size cap.
    """
    if not isinstance(img, Image.Image):
        return img
    if max(img.size) <= _MAX_IMAGE_EDGE:
        return img
    resized = img.copy()
    resized.thumbnail((_MAX_IMAGE_EDGE, _MAX_IMAGE_EDGE), Image.LANCZOS)
    return resized


def generate(
    prompt: str,
    *,
    images: list[str | Path | Image.Image] | None = None,
    channel: str | None = None,
    aspect_ratio: str = "3:4",
    image_size: str | None = None,
    quality: str | None = None,
    background: str | None = None,
    output_format: str | None = None,
    num_images: int | None = None,
    timeout: float = _TIMEOUT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> list[Image.Image]:
    """Generate images from a prompt using a configured channel.

    Args:
        prompt: Text prompt.
        images: Reference images — file paths or PIL Image objects.
        channel: Channel name. ``None`` uses the configured default.
        aspect_ratio: Aspect ratio string (e.g. ``"1:1"``, ``"3:4"``).
        image_size: Size hint (``"512"``, ``"1K"``, ``"2K"``, ``"4K"``).
        quality: OpenAI Images quality option.
        background: OpenAI Images background option.
        output_format: OpenAI Images output format option.
        num_images: OpenAI Images ``n`` option.
        timeout: Seconds to wait for the API response (default 300).
        max_retries: Retry count on 429/5xx (default 3).

    Returns:
        List of PIL Image objects.

    Raises:
        ChannelError: If the channel is not found or not configured.
        GeminiError: If the Gemini API call fails.
        OpenAIError: If the OpenAI-compat API call fails.
    """
    store = ChannelStore.load()
    ch = store.resolve(channel)

    prepared = [_prepare_image(img) for img in images] if images else None

    if ch.protocol == "gemini":
        from . import gemini_client

        return gemini_client.generate_images(
            prompt=prompt,
            base_url=ch.base_url,
            api_key=ch.api_key,
            model=ch.model,
            reference_images=prepared,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            timeout=timeout,
            max_retries=max_retries,
        )

    elif ch.protocol == "openai":
        from . import openai_client

        return openai_client.generate_images(
            prompt=prompt,
            base_url=ch.base_url,
            api_key=ch.api_key,
            model=ch.model,
            reference_images=prepared,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            timeout=timeout,
            max_retries=max_retries,
        )

    elif ch.protocol == "openai_images":
        from . import openai_images_client

        return openai_images_client.generate_images(
            prompt=prompt,
            base_url=ch.base_url,
            api_key=ch.api_key,
            model=ch.model,
            reference_images=prepared,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            quality=quality,
            background=background,
            output_format=output_format,
            num_images=num_images,
            timeout=timeout,
            max_retries=max_retries,
        )

    else:
        raise ValueError(f"unknown protocol: {ch.protocol}")

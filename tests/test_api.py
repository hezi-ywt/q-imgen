"""Tests for the public Python API (q_imgen.api.generate).

All tests mock HTTP — zero API cost. Verifies that:
- generate() returns list[PIL.Image.Image]
- Channel resolution works (by name and default)
- Both protocol paths dispatch correctly
- PIL.Image inputs are accepted as reference images
- Errors raise exceptions (not dicts)
"""

import base64
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image

from q_imgen.api import generate
from q_imgen.channels import Channel, ChannelError, ChannelStore
from q_imgen.gemini_client import GeminiError
from q_imgen.openai_client import OpenAIError
from q_imgen.openai_images_client import OpenAIImagesError

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4"
    "////fwAJ+wP9KobjigAAAABJRU5ErkJggg=="
)
_PNG_B64 = base64.b64encode(_PNG_1X1).decode()


def _make_store(protocol: str = "openai", name: str = "test-ch") -> ChannelStore:
    ch = Channel(
        name=name,
        protocol=protocol,
        base_url="https://example.com/v1",
        api_key="sk-test",
        model="test-model",
    )
    store = ChannelStore(channels={name: ch}, default=name)
    return store


def _openai_response_body() -> bytes:
    return json.dumps({
        "choices": [{
            "message": {
                "images": [{
                    "image_url": {
                        "url": f"data:image/png;base64,{_PNG_B64}"
                    }
                }]
            }
        }]
    }).encode()


def _openai_images_response_body() -> bytes:
    return json.dumps({"data": [{"b64_json": _PNG_B64}]}).encode()


def _gemini_response_body() -> bytes:
    return json.dumps({
        "candidates": [{
            "content": {
                "parts": [{
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": _PNG_B64,
                    }
                }]
            }
        }]
    }).encode()


class FakeHTTPResponse:
    def __init__(self, data: bytes):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._data


class GenerateOpenAITests(unittest.TestCase):
    """Test generate() dispatching to OpenAI protocol."""

    @patch("q_imgen.openai_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_returns_pil_images(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("openai")
        mock_urlopen.return_value = FakeHTTPResponse(_openai_response_body())

        result = generate("a cat", channel="test-ch")

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Image.Image)

    @patch("q_imgen.openai_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_uses_default_channel(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("openai")
        mock_urlopen.return_value = FakeHTTPResponse(_openai_response_body())

        result = generate("a cat")  # no channel= arg

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Image.Image)

    @patch("q_imgen.openai_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_accepts_pil_image_as_reference(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("openai")
        mock_urlopen.return_value = FakeHTTPResponse(_openai_response_body())

        ref_img = Image.new("RGB", (64, 64), color="red")
        result = generate("edit this", images=[ref_img], channel="test-ch")

        self.assertEqual(len(result), 1)
        # Verify the urlopen was called (image was encoded and sent)
        mock_urlopen.assert_called_once()

    @patch("q_imgen.openai_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_accepts_mixed_refs(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("openai")
        mock_urlopen.return_value = FakeHTTPResponse(_openai_response_body())

        ref_img = Image.new("RGB", (64, 64), color="red")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            ref_img.save(f, format="PNG")
            path = f.name

        result = generate("edit", images=[path, ref_img], channel="test-ch")
        self.assertEqual(len(result), 1)

    @patch("q_imgen.api.ChannelStore.load")
    def test_bad_channel_raises(self, mock_load):
        mock_load.return_value = _make_store("openai")

        with self.assertRaises(ChannelError):
            generate("a cat", channel="nonexistent")


class GenerateGeminiTests(unittest.TestCase):
    """Test generate() dispatching to Gemini protocol."""

    @patch("q_imgen.gemini_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_returns_pil_images(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("gemini")
        mock_urlopen.return_value = FakeHTTPResponse(_gemini_response_body())

        result = generate("a cat", channel="test-ch")

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Image.Image)

    @patch("q_imgen.gemini_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_accepts_pil_image_as_reference(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("gemini")
        mock_urlopen.return_value = FakeHTTPResponse(_gemini_response_body())

        ref_img = Image.new("RGB", (64, 64), color="blue")
        result = generate("edit this", images=[ref_img], channel="test-ch")

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Image.Image)

    @patch("q_imgen.gemini_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_no_images_raises(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("gemini")
        empty_response = json.dumps({"candidates": [{"content": {"parts": []}}]}).encode()
        mock_urlopen.return_value = FakeHTTPResponse(empty_response)

        with self.assertRaises(GeminiError):
            generate("a cat", channel="test-ch")


class GenerateOpenAIImagesTests(unittest.TestCase):
    """Test generate() dispatching to OpenAI Images protocol."""

    @patch("q_imgen.openai_images_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_returns_pil_images(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("openai_images")
        mock_urlopen.return_value = FakeHTTPResponse(_openai_images_response_body())

        result = generate("a cat", channel="test-ch", aspect_ratio="2:3")

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Image.Image)
        req = mock_urlopen.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(req.full_url, "https://example.com/v1/images/generations")
        self.assertEqual(payload["size"], "1024x1536")

    @patch("q_imgen.openai_images_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_accepts_pil_image_as_reference(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("openai_images")
        mock_urlopen.return_value = FakeHTTPResponse(_openai_images_response_body())

        ref_img = Image.new("RGB", (64, 64), color="red")
        result = generate("edit this", images=[ref_img], channel="test-ch")

        self.assertEqual(len(result), 1)
        req = mock_urlopen.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(len(payload["input_images"]), 1)

    @patch("q_imgen.openai_images_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_forwards_openai_images_options(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("openai_images")
        mock_urlopen.return_value = FakeHTTPResponse(_openai_images_response_body())

        generate(
            "a poster",
            channel="test-ch",
            image_size="1536x1024",
            quality="high",
            background="transparent",
            output_format="webp",
            num_images=2,
        )

        req = mock_urlopen.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(payload["size"], "1536x1024")
        self.assertEqual(payload["quality"], "high")
        self.assertEqual(payload["background"], "transparent")
        self.assertEqual(payload["output_format"], "webp")
        self.assertEqual(payload["n"], 2)


class GenerateParamsTests(unittest.TestCase):
    """Test that parameters are forwarded correctly."""

    @patch("q_imgen.openai_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_aspect_ratio_forwarded(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("openai")
        mock_urlopen.return_value = FakeHTTPResponse(_openai_response_body())

        generate("a cat", aspect_ratio="16:9", channel="test-ch")

        # Extract the payload sent to urlopen
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        payload = json.loads(req.data)
        self.assertEqual(payload["image_config"]["aspect_ratio"], "16:9")

    @patch("q_imgen.openai_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_image_size_forwarded(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("openai")
        mock_urlopen.return_value = FakeHTTPResponse(_openai_response_body())

        generate("a cat", image_size="2K", channel="test-ch")

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        payload = json.loads(req.data)
        self.assertEqual(payload["image_config"]["image_size"], "2K")

    @patch("q_imgen.openai_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_image_size_none_omitted(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("openai")
        mock_urlopen.return_value = FakeHTTPResponse(_openai_response_body())

        generate("a cat", channel="test-ch")

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        payload = json.loads(req.data)
        self.assertNotIn("image_size", payload["image_config"])


class PrepareImageTests(unittest.TestCase):
    """Test that oversized PIL Images are resized before API call."""

    @patch("q_imgen.openai_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_large_pil_image_is_resized(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("openai")
        mock_urlopen.return_value = FakeHTTPResponse(_openai_response_body())

        big_img = Image.new("RGB", (4000, 3000), color="red")
        generate("edit", images=[big_img], channel="test-ch")

        # The image sent to the API should have been resized
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        payload = json.loads(req.data)
        # Decode the base64 image from the payload to check its size
        data_url = payload["messages"][0]["content"][0]["image_url"]["url"]
        _, b64_data = data_url.split(",", 1)
        sent_img = Image.open(io.BytesIO(base64.b64decode(b64_data)))
        self.assertLessEqual(max(sent_img.size), 2048)

    @patch("q_imgen.openai_client.urllib.request.urlopen")
    @patch("q_imgen.api.ChannelStore.load")
    def test_small_pil_image_unchanged(self, mock_load, mock_urlopen):
        mock_load.return_value = _make_store("openai")
        mock_urlopen.return_value = FakeHTTPResponse(_openai_response_body())

        small_img = Image.new("RGB", (256, 256), color="green")
        generate("edit", images=[small_img], channel="test-ch")

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        payload = json.loads(req.data)
        data_url = payload["messages"][0]["content"][0]["image_url"]["url"]
        _, b64_data = data_url.split(",", 1)
        sent_img = Image.open(io.BytesIO(base64.b64decode(b64_data)))
        self.assertEqual(sent_img.size, (256, 256))


if __name__ == "__main__":
    unittest.main()

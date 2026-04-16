"""Tests for gemini_client and openai_client modules.

Focus on behavior that breaks the agent output contract when it regresses:
- API error sanitization (no live keys leak)
- image_size=None pops from OpenAI payload instead of serializing as null
- PNG → PNG / other → JPEG consistency in _encode_image_data_url
- Gemini auth style branches on base_url
"""

import base64
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from q_imgen import gemini_client, openai_client
from q_imgen.gemini_client import GeminiError
from q_imgen.openai_client import OpenAIError, _encode_image_data_url


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4"
    "////fwAJ+wP9KobjigAAAABJRU5ErkJggg=="
)


# ============================================================
# openai_client
# ============================================================


class OpenAIImageEncodingTests(unittest.TestCase):
    def test_png_in_stays_png_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "input.png"
            p.write_bytes(_PNG_1X1)
            url = _encode_image_data_url(str(p))
        self.assertTrue(url.startswith("data:image/png;base64,"))

    def test_missing_file_raises_openai_error(self):
        with self.assertRaises(OpenAIError):
            _encode_image_data_url("/definitely/not/here.png")


class OpenAIGenerateTests(unittest.TestCase):
    def test_image_size_none_is_omitted_from_payload(self):
        fake_body = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "images": [
                                {
                                    "image_url": {
                                        "url": "data:image/png;base64,"
                                        + base64.b64encode(_PNG_1X1).decode()
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        ).encode()

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return fake_body

        with (
            patch(
                "q_imgen.openai_client.urllib.request.urlopen",
                return_value=FakeResponse(),
            ) as urlopen_mock,
            tempfile.TemporaryDirectory() as tmp,
        ):
            saved = openai_client.generate(
                prompt="cat",
                base_url="https://compat.example/v1",
                api_key="sk-test",
                model="m",
                output_dir=tmp,
                prefix="x",
            )
            self.assertEqual(len(saved), 1)
            req = urlopen_mock.call_args.args[0]
            payload = json.loads(req.data.decode())
            self.assertIn("image_config", payload)
            self.assertNotIn("image_size", payload["image_config"])
            self.assertEqual(payload["image_config"]["aspect_ratio"], "3:4")

    def test_image_size_string_is_included(self):
        fake_body = json.dumps(
            {"choices": [{"message": {"images": []}}]}
        ).encode()

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return fake_body

        with (
            patch(
                "q_imgen.openai_client.urllib.request.urlopen",
                return_value=FakeResponse(),
            ) as urlopen_mock,
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaises(OpenAIError),  # no images triggers error path
        ):
            openai_client.generate(
                prompt="cat",
                base_url="https://compat.example/v1",
                api_key="sk-test",
                model="m",
                image_size="2K",
                output_dir=tmp,
                prefix="x",
            )

        req = urlopen_mock.call_args.args[0]
        payload = json.loads(req.data.decode())
        self.assertEqual(payload["image_config"]["image_size"], "2K")

    def test_extracts_images_from_markdown_content(self):
        """gateway-style response: ![img](data:image/jpeg;base64,...) inside message.content string."""
        from q_imgen.openai_client import _extract_images_from_response

        body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            "Here you go: ![img](data:image/jpeg;base64,/9j/AAA) "
                            "and a bonus ![alt2](https://cdn.example/img.png)"
                        ),
                    }
                }
            ]
        }
        images = _extract_images_from_response(body)
        self.assertEqual(len(images), 2)
        self.assertTrue(images[0]["image_url"]["url"].startswith("data:image/jpeg"))
        self.assertEqual(images[1]["image_url"]["url"], "https://cdn.example/img.png")

    def test_merges_explicit_array_and_markdown_content(self):
        """Both shapes contribute; results merged in encounter order, no dedup needed when distinct."""
        from q_imgen.openai_client import _extract_images_from_response

        body = {
            "choices": [
                {
                    "message": {
                        "images": [
                            {"image_url": {"url": "data:image/png;base64,AAA"}}
                        ],
                        "content": "extra ![x](data:image/png;base64,BBB)",
                    }
                }
            ]
        }
        images = _extract_images_from_response(body)
        self.assertEqual(len(images), 2)
        self.assertIn("AAA", images[0]["image_url"]["url"])
        self.assertIn("BBB", images[1]["image_url"]["url"])

    def test_dedupes_url_appearing_in_both_shapes(self):
        """Same URL in images[] and in markdown content collapses to one record."""
        from q_imgen.openai_client import _extract_images_from_response

        url = "https://cdn.example/abc.png"
        body = {
            "choices": [
                {
                    "message": {
                        "images": [{"image_url": {"url": url}}],
                        "content": f"and again ![dup]({url})",
                    }
                }
            ]
        }
        images = _extract_images_from_response(body)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["image_url"]["url"], url)

    def test_extracts_multiple_markdown_images_in_one_content(self):
        """Multi-image responses (single chat call returning N pictures)."""
        from q_imgen.openai_client import _extract_images_from_response

        body = {
            "choices": [
                {
                    "message": {
                        "content": (
                            "Here are three:\n"
                            "![a](data:image/png;base64,AAA)\n"
                            "![b](data:image/jpeg;base64,BBB)\n"
                            "and ![c](https://cdn.example/c.webp?token=xyz)"
                        )
                    }
                }
            ]
        }
        images = _extract_images_from_response(body)
        self.assertEqual(len(images), 3)
        self.assertTrue(images[2]["image_url"]["url"].endswith("token=xyz"))

    def test_extracts_url_with_query_params(self):
        """Common case: signed CDN URLs with query strings."""
        from q_imgen.openai_client import _extract_images_from_response

        body = {
            "choices": [
                {
                    "message": {
                        "content": "![signed](https://cdn.example/x.jpg?sig=abc&exp=1234)"
                    }
                }
            ]
        }
        images = _extract_images_from_response(body)
        self.assertEqual(len(images), 1)
        self.assertEqual(
            images[0]["image_url"]["url"],
            "https://cdn.example/x.jpg?sig=abc&exp=1234",
        )

    def test_extracts_from_vision_style_content_parts(self):
        """Some proxies symmetrically use OpenAI's vision input format for output."""
        from q_imgen.openai_client import _extract_images_from_response

        body = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "Here's your image"},
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,AAA"},
                            },
                            {"type": "text", "text": "and a bonus ![extra](https://cdn.example/y.png)"},
                        ]
                    }
                }
            ]
        }
        images = _extract_images_from_response(body)
        self.assertEqual(len(images), 2)
        self.assertIn("AAA", images[0]["image_url"]["url"])
        self.assertEqual(images[1]["image_url"]["url"], "https://cdn.example/y.png")

    def test_empty_or_missing_content_returns_empty_list(self):
        """Defensive: malformed responses must not crash, just return []."""
        from q_imgen.openai_client import _extract_images_from_response

        self.assertEqual(_extract_images_from_response({}), [])
        self.assertEqual(
            _extract_images_from_response({"choices": []}), []
        )
        self.assertEqual(
            _extract_images_from_response(
                {"choices": [{"message": {"content": ""}}]}
            ),
            [],
        )
        self.assertEqual(
            _extract_images_from_response(
                {"choices": [{"message": {"content": None}}]}
            ),
            [],
        )

    def test_full_gateway_style_generate_roundtrip(self):
        """End-to-end: gateway-shape response through generate() writes a file."""
        import base64
        real_b64 = base64.b64encode(_PNG_1X1).decode()
        fake_body = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f"![image](data:image/png;base64,{real_b64})",
                        }
                    }
                ]
            }
        ).encode()

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return fake_body

        with (
            patch(
                "q_imgen.openai_client.urllib.request.urlopen",
                return_value=FakeResponse(),
            ),
            tempfile.TemporaryDirectory() as tmp,
        ):
            saved = openai_client.generate(
                prompt="test",
                base_url="https://gateway.example.com/v1",
                api_key="sk-test",
                model="gemini-3-pro-image-preview",
                output_dir=tmp,
                prefix="gateway",
            )
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0].endswith(".png"))

    def test_http_error_sanitizes_api_key(self):
        http_error = urllib.error.HTTPError(
            url="https://compat.example/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(
                b'{"error":"invalid key sk-supersecretkey12345"}'
            ),
        )
        with (
            patch(
                "q_imgen.openai_client.urllib.request.urlopen",
                side_effect=http_error,
            ),
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaises(OpenAIError) as ctx,
        ):
            openai_client.generate(
                prompt="cat",
                base_url="https://compat.example/v1",
                api_key="sk-supersecretkey12345",
                model="m",
                output_dir=tmp,
                prefix="x",
            )
        message = str(ctx.exception)
        self.assertIn("401", message)
        self.assertNotIn("sk-supersecretkey12345", message)


# ============================================================
# gemini_client
# ============================================================


class GeminiAuthTests(unittest.TestCase):
    def test_googleapis_base_url_uses_query_param_auth(self):
        """First-party Google endpoint must auth via ?key= not Bearer."""
        fake_body = json.dumps({"candidates": []}).encode()

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return fake_body

        with patch(
            "q_imgen.gemini_client.urllib.request.urlopen",
            return_value=FakeResponse(),
        ) as urlopen_mock:
            gemini_client.generate(
                prompt="cat",
                base_url="https://generativelanguage.googleapis.com/v1beta",
                api_key="AIza-secret-12345",
                model="gemini-3.1-flash-image-preview",
            )

        req = urlopen_mock.call_args.args[0]
        self.assertIn("?key=AIza-secret-12345", req.full_url)
        self.assertNotIn("Authorization", req.headers)

    def test_proxy_base_url_uses_bearer_auth(self):
        """Non-Google endpoints get Bearer token instead."""
        fake_body = json.dumps({"candidates": []}).encode()

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return fake_body

        with patch(
            "q_imgen.gemini_client.urllib.request.urlopen",
            return_value=FakeResponse(),
        ) as urlopen_mock:
            gemini_client.generate(
                prompt="cat",
                base_url="https://proxy.example/v1beta",
                api_key="sk-proxy-key",
                model="gemini-3.1-flash-image-preview",
            )

        req = urlopen_mock.call_args.args[0]
        self.assertNotIn("?key=", req.full_url)
        self.assertEqual(
            req.headers["Authorization"], "Bearer sk-proxy-key"
        )


class GeminiErrorTests(unittest.TestCase):
    def test_permanent_4xx_does_not_retry_and_sanitizes_key(self):
        http_error = urllib.error.HTTPError(
            url="https://proxy.example/v1beta/models/m:generateContent",
            code=400,
            msg="Bad Request",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(
                b'{"error":"bad key sk-leakedkey12345"}'
            ),
        )
        with (
            patch(
                "q_imgen.gemini_client.urllib.request.urlopen",
                side_effect=http_error,
            ) as urlopen_mock,
            self.assertRaises(GeminiError) as ctx,
        ):
            gemini_client.generate(
                prompt="cat",
                base_url="https://proxy.example/v1beta",
                api_key="sk-leakedkey12345",
                model="m",
            )
        self.assertEqual(urlopen_mock.call_count, 1)  # no retries on 400
        self.assertIn("400", str(ctx.exception))
        self.assertNotIn("sk-leakedkey12345", str(ctx.exception))


class GeminiExtractTests(unittest.TestCase):
    def test_extract_images_handles_camel_and_snake_case(self):
        resp = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "a cat"},
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": "AAAA",
                                }
                            },
                            {
                                "inline_data": {
                                    "mime_type": "image/jpeg",
                                    "data": "BBBB",
                                }
                            },
                        ]
                    }
                }
            ]
        }
        images, texts = gemini_client.extract_images(resp)
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0]["mime_type"], "image/png")
        self.assertEqual(images[1]["mime_type"], "image/jpeg")
        self.assertEqual(texts, ["a cat"])


if __name__ == "__main__":
    unittest.main()

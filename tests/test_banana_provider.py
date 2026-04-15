import io
import json
import os
import sys
import tempfile
import unittest
import base64
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from q_imgen.banana_provider import (
    BananaSettings,
    _encode_image_data_url,
    resolve_banana_provider,
    resolve_banana_settings,
    run_openai_compat_banana,
)


class BananaProviderTests(unittest.TestCase):
    def test_default_provider_is_gemini(self):
        self.assertEqual(resolve_banana_provider({}), "gemini")

    def test_provider_can_be_openai(self):
        self.assertEqual(
            resolve_banana_provider({"NANOBANANA_PROVIDER": "openai"}),
            "openai",
        )

    def test_openai_compat_is_supported_as_backward_compatible_alias(self):
        self.assertEqual(
            resolve_banana_provider({"NANOBANANA_PROVIDER": "openai_compat"}),
            "openai",
        )

    def test_profile_specific_settings_override_generic_settings(self):
        settings = resolve_banana_settings(
            {
                "NANOBANANA_PROVIDER": "gemini",
                "NANOBANANA_PROFILE": "prod",
                "NANOBANANA_PROFILE_PROD_PROVIDER": "openai",
                "NANOBANANA_PROFILE_PROD_OPENAI_BASE_URL": "https://compat.example/v1",
                "NANOBANANA_PROFILE_PROD_OPENAI_API_KEY": "sk-profile",
                "NANOBANANA_PROFILE_PROD_OPENAI_MODEL": "gemini-compat",
            }
        )

        self.assertEqual(settings.provider, "openai")
        self.assertEqual(settings.profile, "prod")
        self.assertEqual(settings.openai_base_url, "https://compat.example/v1")
        self.assertEqual(settings.openai_api_key, "sk-profile")
        self.assertEqual(settings.openai_model, "gemini-compat")

    @patch("q_imgen.banana_provider.urllib.request.urlopen")
    def test_openai_generate_builds_chat_completions_request(self, urlopen_mock):
        response_body = {
            "choices": [
                {
                    "message": {
                        "images": [
                            {
                                "image_url": {
                                    "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WnWZtQAAAAASUVORK5CYII="
                                }
                            }
                        ]
                    }
                }
            ]
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(response_body).encode("utf-8")

        urlopen_mock.return_value = FakeResponse()

        env = {
            "NANOBANANA_PROVIDER": "openai",
            "NANOBANANA_OPENAI_BASE_URL": "https://compat.example/v1",
            "NANOBANANA_OPENAI_API_KEY": "sk-test",
            "NANOBANANA_OPENAI_MODEL": "gemini-openai",
        }

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("sys.stdout", new=io.StringIO()) as stdout,
        ):
            exit_code = run_openai_compat_banana(
                [
                    "generate",
                    "anime portrait",
                    "--image-size",
                    "512",
                    "--output-dir",
                    temp_dir,
                    "--prefix",
                    "case",
                ],
                env,
            )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["images"])

        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.full_url, "https://compat.example/v1/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer sk-test")
        request_payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request_payload["model"], "gemini-openai")
        self.assertIn("messages", request_payload)
        self.assertEqual(request_payload["image_config"]["image_size"], "512")

    def test_resolve_banana_settings_returns_dataclass(self):
        settings = resolve_banana_settings({})
        self.assertIsInstance(settings, BananaSettings)

    def test_encode_image_data_url_reencodes_png_via_pillow(self):
        sample_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4////fwAJ+wP9KobjigAAAABJRU5ErkJggg=="
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "input.png"
            image_path.write_bytes(sample_png)

            data_url = _encode_image_data_url(str(image_path))

        self.assertTrue(data_url.startswith("data:image/png;base64,"))
        self.assertNotEqual(
            data_url.split(",", 1)[1], base64.b64encode(sample_png).decode("utf-8")
        )

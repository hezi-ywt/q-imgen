"""Manual Nano Banana live smoke tests.

Not included in default unittest discovery to avoid accidental API cost.

Run manually:
  python -m unittest tests.live_banana_smoke -v
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from q_imgen.config import merged_env
from q_imgen.banana_provider import resolve_banana_provider

SAMPLE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4////fwAJ+wP9KobjigAAAABJRU5ErkJggg=="
)


class BananaLiveSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("nanobanana") is None:
            raise unittest.SkipTest(
                "nanobanana is not installed in the current interpreter"
            )

        effective_env = merged_env()
        if not any(
            effective_env.get(key)
            for key in (
                "NANOBANANA_API_KEY",
                "MJ_API_KEY",
                "BANANA_API_KEY",
                "GEMINI_API_KEY",
            )
        ):
            raise unittest.SkipTest("No Nano Banana API key configured")

        cls.base_env = dict(os.environ)
        cls.provider = resolve_banana_provider(effective_env)
        existing_pythonpath = cls.base_env.get("PYTHONPATH")
        cls.base_env["PYTHONPATH"] = (
            str(SRC_DIR)
            if not existing_pythonpath
            else str(SRC_DIR) + os.pathsep + existing_pythonpath
        )

    def run_wrapper(self, args: list[str]) -> dict:
        command = [sys.executable, "-m", "q_imgen", "banana", *args]
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=self.base_env,
            capture_output=True,
            text=True,
            timeout=360,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"command failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        return json.loads(result.stdout)

    def test_text_to_image_smoke(self):
        if self.provider != "openai_compat":
            self.skipTest(
                "Current live environment is not using openai_compat provider"
            )
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = self.run_wrapper(
                [
                    "generate",
                    "simple anime portrait, flat colors, plain background",
                    "--image-size",
                    "512",
                    "--output-dir",
                    temp_dir,
                    "--prefix",
                    "smoke",
                ]
            )

            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["images"])
            for image_path in payload["images"]:
                self.assertTrue(
                    Path(image_path).exists(), msg=f"Missing output file: {image_path}"
                )

    def test_image_edit_smoke(self):
        if self.provider != "openai_compat":
            self.skipTest(
                "Current live environment is not using openai_compat provider"
            )
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.png"
            input_path.write_bytes(SAMPLE_PNG)
            output_dir = Path(temp_dir) / "output"

            payload = self.run_wrapper(
                [
                    "generate",
                    "turn this into a simple anime sticker portrait",
                    "--images",
                    str(input_path),
                    "--image-size",
                    "512",
                    "--output-dir",
                    str(output_dir),
                    "--prefix",
                    "edit",
                ]
            )

            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["images"])
            for image_path in payload["images"]:
                self.assertTrue(
                    Path(image_path).exists(), msg=f"Missing output file: {image_path}"
                )

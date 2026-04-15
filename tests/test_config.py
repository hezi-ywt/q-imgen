import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from q_imgen import config


class QImgenConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_dir = Path(self.temp_dir.name)
        self.config_file = self.config_dir / ".env"

        self.dir_patch = patch.object(config, "CONFIG_DIR", self.config_dir)
        self.file_patch = patch.object(config, "CONFIG_FILE", self.config_file)
        self.dir_patch.start()
        self.file_patch.start()
        self.addCleanup(self.dir_patch.stop)
        self.addCleanup(self.file_patch.stop)

    def test_update_and_show_config_masks_keys(self):
        config.update_config(
            mj_api_key="sk-1234567890",
            banana_model="gemini-test",
            banana_provider="openai_compat",
            banana_profile="prod",
            banana_openai_base_url="https://compat.example/v1",
            banana_openai_api_key="sk-abcdef123456",
            banana_openai_model="gemini-openai-test",
        )

        current = config.show_config()

        self.assertEqual(current["banana_model"], "gemini-test")
        self.assertEqual(current["mj_api_key"], "sk-123...7890")
        self.assertEqual(current["banana_provider"], "openai")
        self.assertEqual(current["banana_profile"], "prod")
        self.assertEqual(current["banana_openai_base_url"], "https://compat.example/v1")
        self.assertEqual(current["banana_openai_api_key"], "sk-abc...3456")
        self.assertEqual(current["banana_openai_model"], "gemini-openai-test")

    def test_merged_env_prefers_process_env_over_file(self):
        config.update_config(
            mj_base_url="https://stored.example", banana_model="stored-model"
        )

        with patch.dict(
            os.environ,
            {
                "MJ_BASE_URL": "https://env.example",
                "NANOBANANA_PROVIDER": "gemini",
            },
            clear=False,
        ):
            merged = config.merged_env()

        self.assertEqual(merged["MJ_BASE_URL"], "https://env.example")
        self.assertEqual(merged["NANOBANANA_MODEL"], "stored-model")
        self.assertEqual(merged["NANOBANANA_PROVIDER"], "gemini")

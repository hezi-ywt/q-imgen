import unittest
from unittest.mock import ANY, patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from q_imgen.cli import dispatch, handle_config_command, main, resolve_engine


class QImgenCliTests(unittest.TestCase):
    def test_alias_resolution(self):
        self.assertEqual(resolve_engine("mj"), "midjourney")
        self.assertEqual(resolve_engine("banana"), "nanobanana")

    @patch("q_imgen.cli.merged_env", return_value={"TEST": "1"})
    @patch("q_imgen.cli.subprocess.run")
    def test_dispatch_uses_alias_and_injected_env(self, run_mock, merged_env_mock):
        run_mock.return_value.returncode = 0

        exit_code = dispatch("mj", ["imagine", "prompt"])

        self.assertEqual(exit_code, 0)
        merged_env_mock.assert_called_once_with()
        run_mock.assert_called_once_with(
            [ANY, "-m", "midjourney", "imagine", "prompt"],
            check=False,
            env={"TEST": "1"},
        )

    def test_handle_config_show_returns_zero(self):
        with patch("q_imgen.cli.show_config") as show_mock, patch("builtins.print"):
            show_mock.return_value = {
                "mj_api_key": "***",
                "mj_base_url": "https://yunwu.ai",
                "banana_api_key": "***",
                "banana_base_url": "",
                "banana_model": "gemini",
                "banana_provider": "gemini",
                "banana_profile": "",
                "banana_openai_base_url": "",
                "banana_openai_api_key": "",
                "banana_openai_model": "",
            }
            args = type(
                "Args",
                (),
                {
                    "mj_api_key": None,
                    "mj_base_url": None,
                    "banana_api_key": None,
                    "banana_base_url": None,
                    "banana_model": None,
                    "banana_provider": None,
                    "banana_profile": None,
                    "banana_openai_base_url": None,
                    "banana_openai_api_key": None,
                    "banana_openai_model": None,
                },
            )()

            self.assertEqual(handle_config_command(args), 0)

    @patch("q_imgen.cli.update_config")
    def test_handle_config_update_passes_banana_provider_fields(self, update_mock):
        args = type(
            "Args",
            (),
            {
                "mj_api_key": None,
                "mj_base_url": None,
                "banana_api_key": None,
                "banana_base_url": None,
                "banana_model": None,
                "banana_provider": "openai_compat",
                "banana_profile": "prod",
                "banana_openai_base_url": "https://compat.example/v1",
                "banana_openai_api_key": "sk-xxx",
                "banana_openai_model": "gemini-openai",
            },
        )()

        with patch("builtins.print"):
            exit_code = handle_config_command(args)

        self.assertEqual(exit_code, 0)
        update_mock.assert_called_once_with(
            mj_api_key=None,
            mj_base_url=None,
            banana_api_key=None,
            banana_base_url=None,
            banana_model=None,
            banana_provider="openai_compat",
            banana_profile="prod",
            banana_openai_base_url="https://compat.example/v1",
            banana_openai_api_key="sk-xxx",
            banana_openai_model="gemini-openai",
        )

    @patch("q_imgen.cli.init_config")
    def test_main_init_routes_to_init_config(self, init_mock):
        exit_code = main(["init", "--mj-api-key", "sk-xxx"])

        self.assertEqual(exit_code, 0)
        init_mock.assert_called_once()

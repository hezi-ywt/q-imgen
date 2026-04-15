"""Tests for cli.py entry — dispatch to clients and output contract."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from q_imgen import channels, cli, history
from q_imgen.channels import Channel, ChannelStore


class CliGenerateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config_dir = Path(self.tmp.name) / "config"
        self.out_dir = Path(self.tmp.name) / "out"
        self.history_dir = Path(self.tmp.name) / "history"
        self.dir_patch = patch.object(channels, "CONFIG_DIR", config_dir)
        self.file_patch = patch.object(
            channels, "CHANNELS_FILE", config_dir / "channels.json"
        )
        # Always isolate history writes to a tmp dir — tests must never touch
        # the real ~/.q-imgen/history/.
        self.history_patch = patch.object(history, "HISTORY_DIR", self.history_dir)
        self.dir_patch.start()
        self.file_patch.start()
        self.history_patch.start()
        self.addCleanup(self.dir_patch.stop)
        self.addCleanup(self.file_patch.stop)
        self.addCleanup(self.history_patch.stop)

        # Seed one of each protocol so every dispatch path is covered.
        store = ChannelStore.load()
        store.add(
            "openai-a",
            protocol="openai",
            base_url="https://openai.example/v1",
            api_key="sk-openai-123456",
            model="gemini-3.1-flash-image-preview",
        )
        store.add(
            "gemini-a",
            protocol="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            api_key="AIza-secret-12345",
            model="gemini-3.1-flash-image-preview",
        )
        store.set_default("openai-a")
        store.save()

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        with (
            patch("sys.stdout", new=io.StringIO()) as out,
            patch("sys.stderr", new=io.StringIO()) as err,
        ):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    # ---- generate: openai path ----

    def test_generate_dispatches_to_openai_client_for_openai_channel(self):
        with patch(
            "q_imgen.cli.openai_client.generate",
            return_value=[str(self.out_dir / "img_000.png")],
        ) as gen_mock:
            code, out, err = self._run(
                ["generate", "cat", "-o", str(self.out_dir)]
            )

        self.assertEqual(code, 0)
        gen_mock.assert_called_once()
        kwargs = gen_mock.call_args.kwargs
        self.assertEqual(kwargs["base_url"], "https://openai.example/v1")
        self.assertEqual(kwargs["api_key"], "sk-openai-123456")
        self.assertEqual(kwargs["prompt"], "cat")

        payload = json.loads(out)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["channel"], "openai-a")

    def test_generate_dispatches_to_gemini_client_for_gemini_channel(self):
        fake_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": "aGVsbG8=",  # "hello" base64
                                }
                            }
                        ]
                    }
                }
            ]
        }
        with (
            patch(
                "q_imgen.cli.gemini_client.generate",
                return_value=fake_response,
            ) as gen_mock,
            patch(
                "q_imgen.cli.gemini_client.save_images",
                return_value=[str(self.out_dir / "img_000.png")],
            ),
        ):
            code, out, err = self._run(
                ["generate", "cat", "--channel", "gemini-a", "-o", str(self.out_dir)]
            )

        self.assertEqual(code, 0)
        kwargs = gen_mock.call_args.kwargs
        self.assertEqual(
            kwargs["base_url"], "https://generativelanguage.googleapis.com/v1beta"
        )
        payload = json.loads(out)
        self.assertEqual(payload["channel"], "gemini-a")

    # ---- generate: errors hit stderr with [q-imgen] prefix ----

    def test_generate_unknown_channel_goes_to_stderr(self):
        code, out, err = self._run(
            ["generate", "cat", "--channel", "ghost"]
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("[q-imgen]", err)
        self.assertIn("no such channel", err)

    def test_generate_openai_error_becomes_error_json_and_exit_1(self):
        from q_imgen.openai_client import OpenAIError

        with patch(
            "q_imgen.cli.openai_client.generate",
            side_effect=OpenAIError("HTTP 401: Unauthorized"),
        ):
            code, out, err = self._run(
                ["generate", "cat", "-o", str(self.out_dir)]
            )

        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "error")
        self.assertIn("401", payload["error"])

    def test_generate_model_override_passes_to_client(self):
        with patch(
            "q_imgen.cli.openai_client.generate",
            return_value=[str(self.out_dir / "img_000.png")],
        ) as gen_mock:
            self._run(
                [
                    "generate",
                    "cat",
                    "--model",
                    "override-model",
                    "-o",
                    str(self.out_dir),
                ]
            )
        self.assertEqual(gen_mock.call_args.kwargs["model"], "override-model")

    # ---- channel commands ----

    def test_channel_list_shows_default_marker(self):
        code, out, err = self._run(["channel", "list"])
        self.assertEqual(code, 0)
        self.assertIn("*", out)  # default marker
        self.assertIn("openai-a", out)
        self.assertIn("gemini-a", out)

    def test_channel_show_masks_api_key(self):
        code, out, err = self._run(["channel", "show", "openai-a"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertNotEqual(payload["api_key"], "sk-openai-123456")
        self.assertIn("...", payload["api_key"])

    def test_channel_use_changes_default(self):
        code, _, _ = self._run(["channel", "use", "gemini-a"])
        self.assertEqual(code, 0)
        store = ChannelStore.load()
        self.assertEqual(store.default, "gemini-a")

    def test_channel_rm_removes_channel(self):
        code, _, _ = self._run(["channel", "rm", "gemini-a"])
        self.assertEqual(code, 0)
        store = ChannelStore.load()
        self.assertNotIn("gemini-a", store.channels)

    def test_channel_add_then_available(self):
        code, _, err = self._run(
            [
                "channel",
                "add",
                "new-one",
                "--protocol",
                "openai",
                "--base-url",
                "https://new.example/v1",
                "--api-key",
                "sk-new-1234567890",
                "--model",
                "m1",
            ]
        )
        self.assertEqual(code, 0)
        store = ChannelStore.load()
        self.assertIn("new-one", store.channels)

    def test_channel_add_duplicate_fails_without_force(self):
        code, _, err = self._run(
            [
                "channel",
                "add",
                "openai-a",
                "--protocol",
                "openai",
                "--base-url",
                "https://dup.example/v1",
                "--api-key",
                "sk-dup-1234567890",
                "--model",
                "m",
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("[q-imgen]", err)
        self.assertIn("already exists", err)


class CliBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config_dir = Path(self.tmp.name) / "config"
        self.out_dir = Path(self.tmp.name) / "out"
        self.history_dir = Path(self.tmp.name) / "history"
        self.dir_patch = patch.object(channels, "CONFIG_DIR", config_dir)
        self.file_patch = patch.object(
            channels, "CHANNELS_FILE", config_dir / "channels.json"
        )
        self.history_patch = patch.object(history, "HISTORY_DIR", self.history_dir)
        self.dir_patch.start()
        self.file_patch.start()
        self.history_patch.start()
        self.addCleanup(self.dir_patch.stop)
        self.addCleanup(self.file_patch.stop)
        self.addCleanup(self.history_patch.stop)

        store = ChannelStore.load()
        store.add(
            "openai-a",
            protocol="openai",
            base_url="https://openai.example/v1",
            api_key="sk-openai-123456",
            model="m0",
        )
        store.save()

    def test_batch_runs_each_task_and_reports_ok(self):
        task_file = Path(self.tmp.name) / "tasks.json"
        task_file.write_text(
            json.dumps(
                [
                    {"prompt": "cat"},
                    {"prompt": "dog", "aspect_ratio": "1:1"},
                ]
            )
        )

        call_kwargs = []

        def fake_generate(**kwargs):
            call_kwargs.append(kwargs)
            return [str(self.out_dir / f"fake_{len(call_kwargs):03d}.png")]

        with (
            patch("q_imgen.cli.openai_client.generate", side_effect=fake_generate),
            patch("sys.stdout", new=io.StringIO()) as out,
            patch("sys.stderr", new=io.StringIO()),
            patch("q_imgen.cli.time.sleep"),  # no real delay in test
        ):
            code = cli.main(
                [
                    "batch",
                    str(task_file),
                    "-o",
                    str(self.out_dir),
                    "--delay",
                    "0",
                ]
            )

        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["ok"], 2)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(call_kwargs), 2)
        # Second task overrides aspect_ratio per-task.
        self.assertEqual(call_kwargs[1]["aspect_ratio"], "1:1")

    def test_batch_partial_failure_returns_exit_0_with_status_partial(self):
        task_file = Path(self.tmp.name) / "tasks.json"
        task_file.write_text(json.dumps([{"prompt": "ok"}, {"prompt": "fail"}]))

        from q_imgen.openai_client import OpenAIError

        calls = {"n": 0}

        def fake_generate(**kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OpenAIError("boom")
            return [str(self.out_dir / "good.png")]

        with (
            patch("q_imgen.cli.openai_client.generate", side_effect=fake_generate),
            patch("sys.stdout", new=io.StringIO()) as out,
            patch("sys.stderr", new=io.StringIO()),
            patch("q_imgen.cli.time.sleep"),
        ):
            code = cli.main(
                ["batch", str(task_file), "-o", str(self.out_dir), "--delay", "0"]
            )

        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["ok"], 1)
        self.assertEqual(payload["total"], 2)

    def test_batch_missing_task_file_error_contract(self):
        with (
            patch("sys.stdout", new=io.StringIO()),
            patch("sys.stderr", new=io.StringIO()) as err,
        ):
            code = cli.main(["batch", "/nonexistent.json", "-o", str(self.out_dir)])
        self.assertEqual(code, 1)
        self.assertIn("[q-imgen]", err.getvalue())
        self.assertIn("not found", err.getvalue())

    def test_batch_task_missing_prompt_is_task_error_without_api_call(self):
        task_file = Path(self.tmp.name) / "tasks.json"
        task_file.write_text(json.dumps([{"aspect_ratio": "1:1"}, {"prompt": "ok"}]))

        with (
            patch(
                "q_imgen.cli.openai_client.generate",
                return_value=[str(self.out_dir / "good.png")],
            ) as gen_mock,
            patch("sys.stdout", new=io.StringIO()) as out,
            patch("sys.stderr", new=io.StringIO()),
            patch("q_imgen.cli.time.sleep"),
        ):
            code = cli.main(
                ["batch", str(task_file), "-o", str(self.out_dir), "--delay", "0"]
            )

        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["ok"], 1)
        self.assertEqual(payload["results"][0]["status"], "error")
        self.assertIn("missing required field: prompt", payload["results"][0]["error"])
        self.assertEqual(gen_mock.call_count, 1)

    def test_batch_task_prompt_must_be_string(self):
        task_file = Path(self.tmp.name) / "tasks.json"
        task_file.write_text(json.dumps([{"prompt": 123}]))

        with (
            patch("q_imgen.cli.openai_client.generate") as gen_mock,
            patch("sys.stdout", new=io.StringIO()) as out,
            patch("sys.stderr", new=io.StringIO()),
        ):
            code = cli.main(["batch", str(task_file), "-o", str(self.out_dir)])

        self.assertEqual(code, 1)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["ok"], 0)
        self.assertEqual(payload["results"][0]["status"], "error")
        self.assertIn("must be a string", payload["results"][0]["error"])
        gen_mock.assert_not_called()


class CliHistoryIntegrationTests(unittest.TestCase):
    """Verify cli._run_single writes a history record after every call,
    success or failure, and the `q-imgen history` subcommand prints the
    today path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config_dir = Path(self.tmp.name) / "config"
        self.out_dir = Path(self.tmp.name) / "out"
        self.history_dir = Path(self.tmp.name) / "history"
        self.dir_patch = patch.object(channels, "CONFIG_DIR", config_dir)
        self.file_patch = patch.object(
            channels, "CHANNELS_FILE", config_dir / "channels.json"
        )
        self.history_patch = patch.object(history, "HISTORY_DIR", self.history_dir)
        self.dir_patch.start()
        self.file_patch.start()
        self.history_patch.start()
        self.addCleanup(self.dir_patch.stop)
        self.addCleanup(self.file_patch.stop)
        self.addCleanup(self.history_patch.stop)

        store = ChannelStore.load()
        store.add(
            "openai-a",
            protocol="openai",
            base_url="https://openai.example/v1",
            api_key="sk-openai-123456",
            model="gemini-3.1-flash-image-preview",
        )
        store.save()

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        with (
            patch("sys.stdout", new=io.StringIO()) as out,
            patch("sys.stderr", new=io.StringIO()) as err,
        ):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _read_history(self) -> list[dict]:
        log = history.today_log_path()
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    # ---- success path writes history ----

    def test_generate_success_appends_one_history_record(self):
        with patch(
            "q_imgen.cli.openai_client.generate",
            return_value=[str(self.out_dir / "img_000.png")],
        ):
            code, _, _ = self._run(["generate", "cat", "-o", str(self.out_dir)])
        self.assertEqual(code, 0)

        records = self._read_history()
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["status"], "ok")
        self.assertEqual(rec["prompt"], "cat")
        self.assertEqual(rec["channel"], "openai-a")
        self.assertEqual(rec["protocol"], "openai")
        self.assertEqual(rec["model"], "gemini-3.1-flash-image-preview")
        self.assertEqual(rec["aspect_ratio"], "3:4")
        self.assertNotIn("error", rec)
        self.assertIn("latency_ms", rec)
        self.assertIsInstance(rec["latency_ms"], int)
        self.assertEqual(len(rec["outputs"]), 1)
        self.assertIn("workdir", rec)

    def test_generate_failure_appends_history_with_error(self):
        from q_imgen.openai_client import OpenAIError

        with patch(
            "q_imgen.cli.openai_client.generate",
            side_effect=OpenAIError("HTTP 401: Unauthorized"),
        ):
            code, _, _ = self._run(["generate", "cat", "-o", str(self.out_dir)])
        self.assertEqual(code, 1)

        records = self._read_history()
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["status"], "error")
        self.assertEqual(rec["error"], "HTTP 401: Unauthorized")
        self.assertEqual(rec["outputs"], [])

    # NOTE: We intentionally do NOT have a "history.append throws → cli still
    # succeeds" test here. The contract is that ``history.append`` is internally
    # best-effort and never raises (verified by
    # ``test_append_failure_warns_to_stderr_does_not_raise`` in
    # test_history.py). cli.py trusts that contract — adding its own try/except
    # would be defense-in-depth for a contract already enforced one layer down.

    # ---- batch writes one record per task ----

    def test_batch_writes_one_history_record_per_task(self):
        task_file = Path(self.tmp.name) / "tasks.json"
        task_file.write_text(json.dumps([{"prompt": "a"}, {"prompt": "b"}, {"prompt": "c"}]))

        def fake_gen(**kwargs):
            return [str(self.out_dir / f"x_{kwargs['prompt']}.png")]

        with (
            patch("q_imgen.cli.openai_client.generate", side_effect=fake_gen),
            patch("q_imgen.cli.time.sleep"),
        ):
            code, _, _ = self._run(
                ["batch", str(task_file), "-o", str(self.out_dir), "--delay", "0"]
            )
        self.assertEqual(code, 0)

        records = self._read_history()
        self.assertEqual(len(records), 3)
        self.assertEqual([r["prompt"] for r in records], ["a", "b", "c"])
        self.assertTrue(all(r["status"] == "ok" for r in records))

    def test_invalid_batch_task_does_not_write_history_record(self):
        task_file = Path(self.tmp.name) / "tasks.json"
        task_file.write_text(json.dumps([{"prompt": "a"}, {"images": ["x.png"]}]))

        with (
            patch(
                "q_imgen.cli.openai_client.generate",
                return_value=[str(self.out_dir / "img_000.png")],
            ),
            patch("q_imgen.cli.time.sleep"),
        ):
            code, _, _ = self._run(
                ["batch", str(task_file), "-o", str(self.out_dir), "--delay", "0"]
            )
        self.assertEqual(code, 0)

        records = self._read_history()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["prompt"], "a")

    # ---- history subcommand ----

    def test_history_subcommand_prints_today_path(self):
        code, out, _ = self._run(["history"])
        self.assertEqual(code, 0)
        printed = out.strip()
        # Should be inside our patched history dir + today's date
        import datetime as _dt
        today = _dt.datetime.now().strftime("%Y-%m-%d")
        self.assertIn(today, printed)
        self.assertTrue(printed.endswith(".jsonl"))
        self.assertIn(str(self.history_dir), printed)


if __name__ == "__main__":
    unittest.main()

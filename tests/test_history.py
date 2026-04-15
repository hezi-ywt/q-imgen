"""Tests for history.py: workdir resolution, log path, append, concurrency."""

import datetime
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from q_imgen import history


class WorkdirResolutionTests(unittest.TestCase):
    def test_uses_cwd_when_no_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp).resolve()
            with patch("q_imgen.history.Path.cwd", return_value=cwd):
                self.assertEqual(history.resolve_workdir(), str(cwd))

    def test_finds_git_root_from_subdir(self):
        """A call inside scripts/deep should resolve to the git root."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".git").mkdir()
            sub = root / "scripts" / "deep"
            sub.mkdir(parents=True)
            with patch("q_imgen.history.Path.cwd", return_value=sub):
                self.assertEqual(history.resolve_workdir(), str(root))

    def test_uses_git_root_when_cwd_is_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".git").mkdir()
            with patch("q_imgen.history.Path.cwd", return_value=root):
                self.assertEqual(history.resolve_workdir(), str(root))

    def test_git_file_not_dir_also_counts(self):
        """Git worktrees use a `.git` *file* (not directory). Both should work
        because we check `.exists()`, not `.is_dir()`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".git").write_text("gitdir: ../some/worktree")
            sub = root / "src"
            sub.mkdir()
            with patch("q_imgen.history.Path.cwd", return_value=sub):
                self.assertEqual(history.resolve_workdir(), str(root))


class LogPathTests(unittest.TestCase):
    def test_today_path_uses_local_date_and_jsonl_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(history, "HISTORY_DIR", Path(tmp)):
                path = history.today_log_path()
                expected_name = (
                    datetime.datetime.now().strftime("%Y-%m-%d") + ".jsonl"
                )
                self.assertEqual(path.name, expected_name)
                self.assertEqual(path.parent, Path(tmp))


class RecordBuildTests(unittest.TestCase):
    def _base_args(self, **overrides):
        defaults = dict(
            prompt="cat",
            model="gemini-3.1-flash-image-preview",
            channel="yunwu-gemini",
            protocol="gemini",
            aspect_ratio="3:4",
            image_size=None,
            ref_images=None,
            outputs=["/abs/output/img_000.png"],
            status="ok",
            error=None,
            latency_ms=12345,
            workdir="/tmp/proj",
        )
        defaults.update(overrides)
        return defaults

    def test_success_record_has_no_error_field(self):
        r = history.build_record(**self._base_args())
        self.assertEqual(r["status"], "ok")
        self.assertNotIn("error", r)
        self.assertEqual(r["latency_ms"], 12345)

    def test_error_record_includes_error_field(self):
        r = history.build_record(
            **self._base_args(status="error", error="HTTP 401", outputs=[])
        )
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["error"], "HTTP 401")

    def test_field_order_human_friendly(self):
        """ts and prompt must come first (humans scan for them);
        workdir comes near the end (filter key, not scan key)."""
        r = history.build_record(**self._base_args())
        keys = list(r.keys())
        self.assertEqual(keys[0], "ts")
        self.assertEqual(keys[1], "prompt")
        self.assertEqual(keys[-1], "workdir")
        # latency_ms should be near the end too, after status (and error if present)
        self.assertLess(keys.index("status"), keys.index("latency_ms"))

    def test_ref_images_are_resolved_to_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "ref.png"
            f.write_bytes(b"x")
            r = history.build_record(
                **self._base_args(ref_images=[str(f)])
            )
            self.assertEqual(len(r["ref_images"]), 1)
            self.assertTrue(Path(r["ref_images"][0]).is_absolute())

    def test_ref_images_none_becomes_empty_list(self):
        r = history.build_record(**self._base_args(ref_images=None))
        self.assertEqual(r["ref_images"], [])

    def test_ts_is_iso8601_with_offset(self):
        r = history.build_record(**self._base_args())
        ts = r["ts"]
        # ISO8601 with timezone offset: "2026-04-15T19:09:23+08:00" or "...Z"
        self.assertRegex(
            ts, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2}|Z)$"
        )

    def test_workdir_defaults_to_resolve_when_not_provided(self):
        with patch("q_imgen.history.resolve_workdir", return_value="/auto/wd"):
            r = history.build_record(
                **{k: v for k, v in self._base_args().items() if k != "workdir"}
            )
        self.assertEqual(r["workdir"], "/auto/wd")


class AppendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir_patch = patch.object(
            history, "HISTORY_DIR", Path(self.tmp.name)
        )
        self.dir_patch.start()
        self.addCleanup(self.dir_patch.stop)

    def _record(self, n: int) -> dict:
        return {"n": n, "prompt": f"call {n}", "status": "ok"}

    def test_append_creates_parent_dir_and_file(self):
        history.append(self._record(1))
        log = history.today_log_path()
        self.assertTrue(log.parent.exists())
        self.assertTrue(log.exists())
        line = log.read_text(encoding="utf-8").strip()
        self.assertEqual(json.loads(line)["n"], 1)

    def test_append_multiple_records_in_order(self):
        for i in range(5):
            history.append(self._record(i))
        log = history.today_log_path()
        lines = [json.loads(l) for l in log.read_text().splitlines()]
        self.assertEqual([r["n"] for r in lines], [0, 1, 2, 3, 4])

    def test_each_line_is_complete_json_with_newline(self):
        history.append(self._record(1))
        history.append(self._record(2))
        log = history.today_log_path()
        raw = log.read_text(encoding="utf-8")
        # File ends in newline (so further appends start cleanly on a new line)
        self.assertTrue(raw.endswith("\n"))
        # Each line independently parseable
        for line in raw.splitlines():
            json.loads(line)  # raises if malformed

    def test_unicode_prompts_round_trip(self):
        history.append({"prompt": "银发精灵 弓箭手 🏹", "status": "ok"})
        log = history.today_log_path()
        record = json.loads(log.read_text().strip())
        self.assertEqual(record["prompt"], "银发精灵 弓箭手 🏹")

    def test_append_concurrent_writers_no_corruption(self):
        """8 threads × 25 writes = 200 records. Each line must be valid JSON
        and every (tid, i) tuple must appear exactly once."""
        N_THREADS = 8
        N_PER_THREAD = 25

        def worker(tid: int) -> None:
            for i in range(N_PER_THREAD):
                history.append({"tid": tid, "i": i, "prompt": f"t{tid}-{i}"})

        threads = [
            threading.Thread(target=worker, args=(t,)) for t in range(N_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        log = history.today_log_path()
        lines = log.read_text().splitlines()
        self.assertEqual(len(lines), N_THREADS * N_PER_THREAD)

        records = [json.loads(line) for line in lines]
        seen = {(r["tid"], r["i"]) for r in records}
        self.assertEqual(len(seen), N_THREADS * N_PER_THREAD)

    def test_append_failure_warns_to_stderr_does_not_raise(self):
        """If anything in the write path fails, append must surface a warning
        and return — never raise — because the caller's image is already
        generated and the log is best-effort."""
        with patch("builtins.open", side_effect=PermissionError("no write")):
            with patch("sys.stderr", new=io.StringIO()) as err:
                # Must not raise
                history.append({"a": 1})
                self.assertIn(
                    "[q-imgen] warning: history append failed", err.getvalue()
                )

    def test_append_failure_includes_exception_message(self):
        with patch("builtins.open", side_effect=OSError("disk full")):
            with patch("sys.stderr", new=io.StringIO()) as err:
                history.append({"a": 1})
                self.assertIn("disk full", err.getvalue())


if __name__ == "__main__":
    unittest.main()

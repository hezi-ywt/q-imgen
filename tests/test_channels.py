"""Tests for channels.py: CRUD, persistence, default resolution."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from q_imgen import channels
from q_imgen.channels import ChannelError, ChannelStore


class ChannelStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config_dir = Path(self.tmp.name)
        config_file = config_dir / "channels.json"
        self.dir_patch = patch.object(channels, "CONFIG_DIR", config_dir)
        self.file_patch = patch.object(channels, "CHANNELS_FILE", config_file)
        self.dir_patch.start()
        self.file_patch.start()
        self.addCleanup(self.dir_patch.stop)
        self.addCleanup(self.file_patch.stop)
        self.config_file = config_file

    def _add_sample(self, store: ChannelStore, name: str = "proxy-a") -> None:
        store.add(
            name,
            protocol="openai",
            base_url="https://compat.example/v1",
            api_key="sk-test-1234567890",
            model="gemini-3.1-flash-image-preview",
        )

    def test_load_empty_when_file_missing(self):
        store = ChannelStore.load()
        self.assertEqual(store.channels, {})
        self.assertIsNone(store.default)

    def test_first_channel_becomes_default(self):
        store = ChannelStore.load()
        self._add_sample(store)
        self.assertEqual(store.default, "proxy-a")

    def test_save_and_reload_roundtrip(self):
        store = ChannelStore.load()
        self._add_sample(store)
        store.save()

        reloaded = ChannelStore.load()
        self.assertIn("proxy-a", reloaded.channels)
        self.assertEqual(reloaded.default, "proxy-a")
        self.assertEqual(reloaded.channels["proxy-a"].model,
                         "gemini-3.1-flash-image-preview")

    @unittest.skipIf(
        os.name == "nt",
        "POSIX file modes don't apply to NTFS — Path.chmod on Windows only "
        "toggles the read-only bit. The 0o600 invariant is best-effort on "
        "Windows by design.",
    )
    def test_file_permissions_are_locked_down(self):
        store = ChannelStore.load()
        self._add_sample(store)
        store.save()
        mode = self.config_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, f"channels.json must be 600, got {oct(mode)}")

    def test_duplicate_add_without_force_raises(self):
        store = ChannelStore.load()
        self._add_sample(store)
        with self.assertRaises(ChannelError):
            self._add_sample(store)

    def test_duplicate_add_with_force_overwrites(self):
        store = ChannelStore.load()
        self._add_sample(store)
        store.add(
            "proxy-a",
            protocol="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            api_key="AIza-secret",
            model="gemini-3.1-flash-image-preview",
            overwrite=True,
        )
        self.assertEqual(store.channels["proxy-a"].protocol, "gemini")

    def test_add_rejects_unknown_protocol(self):
        store = ChannelStore.load()
        with self.assertRaises(ChannelError):
            store.add(
                "x",
                protocol="bedrock",
                base_url="u",
                api_key="k",
                model="m",
            )

    def test_add_rejects_empty_required_fields(self):
        store = ChannelStore.load()
        with self.assertRaises(ChannelError):
            store.add("x", protocol="openai", base_url="", api_key="k", model="m")

    def test_resolve_named_channel(self):
        store = ChannelStore.load()
        self._add_sample(store)
        store.add(
            "other",
            protocol="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            api_key="k",
            model="m",
        )
        self.assertEqual(store.resolve("other").name, "other")

    def test_resolve_default_when_no_name(self):
        store = ChannelStore.load()
        self._add_sample(store)
        self.assertEqual(store.resolve(None).name, "proxy-a")

    def test_resolve_unknown_name_has_actionable_message(self):
        store = ChannelStore.load()
        self._add_sample(store)
        with self.assertRaises(ChannelError) as ctx:
            store.resolve("nope")
        msg = str(ctx.exception)
        self.assertIn("no such channel", msg)
        self.assertIn("proxy-a", msg)  # lists known channels

    def test_resolve_empty_store_hints_at_add_command(self):
        store = ChannelStore.load()
        with self.assertRaises(ChannelError) as ctx:
            store.resolve(None)
        self.assertIn("channel add", str(ctx.exception))

    def test_set_default_unknown_raises(self):
        store = ChannelStore.load()
        self._add_sample(store)
        with self.assertRaises(ChannelError):
            store.set_default("ghost")

    def test_remove_reassigns_default(self):
        store = ChannelStore.load()
        self._add_sample(store, "a")
        store.add(
            "b",
            protocol="openai",
            base_url="https://b.example/v1",
            api_key="sk-b",
            model="m",
        )
        self.assertEqual(store.default, "a")
        store.remove("a")
        self.assertEqual(store.default, "b")

    def test_remove_last_channel_clears_default(self):
        store = ChannelStore.load()
        self._add_sample(store)
        store.remove("proxy-a")
        self.assertIsNone(store.default)
        self.assertEqual(store.channels, {})

    def test_load_stale_default_is_dropped(self):
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(
            json.dumps(
                {
                    "default": "ghost",
                    "channels": {
                        "real": {
                            "protocol": "openai",
                            "base_url": "u",
                            "api_key": "k",
                            "model": "m",
                        }
                    },
                }
            )
        )
        store = ChannelStore.load()
        self.assertIsNone(store.default)
        self.assertIn("real", store.channels)

    def test_load_corrupted_json_raises_channel_error(self):
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text("not json {{{")
        with self.assertRaises(ChannelError):
            ChannelStore.load()

    def test_mask_secret(self):
        self.assertEqual(channels.mask_secret(""), "")
        self.assertEqual(channels.mask_secret("short"), "***")
        self.assertEqual(
            channels.mask_secret("sk-1234567890abcdef"),
            "sk-123...cdef",
        )


if __name__ == "__main__":
    unittest.main()

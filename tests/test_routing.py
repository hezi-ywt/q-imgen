import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from q_imgen.routing import canonicalize_engine, choose_engine


class QImgenRoutingTests(unittest.TestCase):
    def test_canonicalize_engine_supports_aliases(self):
        self.assertEqual(canonicalize_engine("mj"), "midjourney")
        self.assertEqual(canonicalize_engine("banana"), "nanobanana")

    def test_status_routes_to_midjourney(self):
        self.assertEqual(choose_engine(operation="status"), "midjourney")

    def test_reference_images_route_to_nanobanana(self):
        self.assertEqual(
            choose_engine(operation="generate", reference_image_count=1),
            "nanobanana",
        )

    def test_batch_routes_to_nanobanana(self):
        self.assertEqual(choose_engine(operation="batch"), "nanobanana")

    def test_speed_priority_routes_to_nanobanana(self):
        self.assertEqual(choose_engine(priority="speed"), "nanobanana")

    def test_quality_anime_defaults_to_midjourney(self):
        self.assertEqual(
            choose_engine(priority="quality", style="anime portrait"),
            "midjourney",
        )

"""Runtime-free tests: no daemon, no SDK, no network, no credentials.

    python3 -m unittest discover -s freesound/tests

Only `rules.py` is tested, because it is the part that makes decisions. The
network code in `freesound_client.py` cannot be tested without credentials.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rules import (  # noqa: E402
    exceeds_max,
    extension_for,
    keep,
    max_bytes_from_mb,
    preview_url,
    safe_name,
    search_filter,
    sound_path,
)

#: A sound that passes every rule, so each test changes exactly one thing.
GOOD = {
    "id": 123,
    "name": "Drum loop 01",
    "type": "wav",
    "username": "someone",
    "license": "Attribution",
    "url": "https://freesound.org/people/someone/sounds/123/",
    "avg_rating": 4.5,
    "num_ratings": 10,
    "tags": ["drum", "loop"],
    "previews": {"preview-hq-mp3": "https://cdn.example/123.mp3"},
}


class Keep(unittest.TestCase):
    def test_keeps_a_new_well_rated_sound(self):
        self.assertTrue(keep(GOOD, set(), 4.0, 3))

    def test_skips_sounds_we_already_downloaded(self):
        self.assertFalse(keep(GOOD, {123}, 4.0, 3))

    def test_skips_low_ratings(self):
        self.assertFalse(keep(dict(GOOD, avg_rating=3.2), set(), 4.0, 3))

    def test_skips_sounds_with_too_few_ratings(self):
        self.assertFalse(keep(dict(GOOD, num_ratings=1), set(), 4.0, 3))

    def test_survives_a_missing_rating(self):
        sound = {key: value for key, value in GOOD.items() if key != "avg_rating"}
        self.assertFalse(keep(sound, set(), 4.0, 3))


class Names(unittest.TestCase):
    def test_safe_name_strips_punctuation(self):
        self.assertEqual(safe_name("Kick/snare #2!"), "Kick_snare_2")

    def test_safe_name_has_a_fallback(self):
        self.assertEqual(safe_name("///"), "sound")

    def test_path_leads_with_the_id(self):
        path = sound_path(Path("/tmp"), GOOD, "mp3")
        self.assertEqual(path.name, "123_Drum_loop_01.mp3")


class Format(unittest.TestCase):
    def test_original_keeps_the_uploaded_type(self):
        self.assertEqual(extension_for(GOOD, "original"), "wav")

    def test_preview_is_mp3(self):
        self.assertEqual(extension_for(GOOD, "preview"), "mp3")

    def test_preview_url_is_the_high_quality_one(self):
        self.assertEqual(preview_url(GOOD), "https://cdn.example/123.mp3")

    def test_preview_url_missing(self):
        self.assertIsNone(preview_url(dict(GOOD, previews={})))


class Filter(unittest.TestCase):
    def test_keeps_your_filter_and_adds_both_thresholds(self):
        text = search_filter("tag:drum", 4.0, 3)
        self.assertIn("tag:drum", text)
        self.assertIn("avg_rating:[4.0 TO *]", text)
        self.assertIn("num_ratings:[3 TO *]", text)

    def test_empty_filter_means_any_sound(self):
        self.assertEqual(
            search_filter("", 4.0, 3),
            "avg_rating:[4.0 TO *] num_ratings:[3 TO *]",
        )

    def test_your_filter_is_grouped_so_it_cannot_swallow_the_thresholds(self):
        self.assertTrue(search_filter("a:1 OR b:2", 4.0, 3).startswith("(a:1 OR b:2) "))

    def test_whitespace_only_filter_is_treated_as_empty(self):
        self.assertEqual(search_filter("   ", 4.0, 3), search_filter("", 4.0, 3))


class SizeLimit(unittest.TestCase):
    def test_megabytes_become_bytes(self):
        self.assertEqual(max_bytes_from_mb("5"), 5 * 1024 * 1024)

    def test_fractions_are_allowed(self):
        self.assertEqual(max_bytes_from_mb("0.5"), 512 * 1024)

    def test_zero_means_no_limit(self):
        self.assertIsNone(max_bytes_from_mb("0"))

    def test_empty_means_no_limit(self):
        self.assertIsNone(max_bytes_from_mb(""))

    def test_junk_means_no_limit(self):
        self.assertIsNone(max_bytes_from_mb("lots"))

    def test_known_size_over_the_limit(self):
        self.assertTrue(exceeds_max(6 * 1024 * 1024, 5 * 1024 * 1024))

    def test_known_size_under_the_limit(self):
        self.assertFalse(exceeds_max(4 * 1024 * 1024, 5 * 1024 * 1024))

    def test_unknown_size_is_never_over(self):
        self.assertFalse(exceeds_max(None, 5 * 1024 * 1024))

    def test_no_limit_accepts_anything(self):
        self.assertFalse(exceeds_max(10 ** 12, None))


if __name__ == "__main__":
    unittest.main()

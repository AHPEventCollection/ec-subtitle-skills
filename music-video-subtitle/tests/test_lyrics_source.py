from __future__ import annotations

# ruff: noqa: E402

import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lyrics_source import (
    choose_candidate,
    fetch_netease_bilingual,
    inspect_candidates,
    translation_coverage,
)


class LyricsSourceTests(unittest.TestCase):
    def test_coverage_ignores_metadata_and_ascii_chant(self) -> None:
        original = "\n".join(
            [
                "[00:01.00]作词：某人",
                "[00:02.00]原文一",
                "[00:04.00]Oh",
                "[00:06.00]原文二",
            ]
        )
        translated = "\n".join(["[00:02.10]译文一", "[00:06.00]译文二"])

        coverage, missing, original_count, translated_count = translation_coverage(
            original, translated
        )

        self.assertEqual(1.0, coverage)
        self.assertEqual((), missing)
        self.assertEqual(3, original_count)
        self.assertEqual(2, translated_count)

    def test_checks_five_candidates_and_chooses_best_translation_coverage(self) -> None:
        songs = [
            {
                "id": index,
                "name": "Song",
                "artists": [{"name": "Artist"}],
                "popularity": 100 - index,
            }
            for index in range(1, 7)
        ]
        lyric_responses = [
            {
                "lrc": {"lyric": "[00:01.00]原文一\n[00:02.00]原文二"},
                "tlyric": {"lyric": "[00:01.00]译文一"},
            },
            {
                "lrc": {"lyric": "[00:01.00]原文一\n[00:02.00]原文二"},
                "tlyric": {"lyric": "[00:01.00]译文一\n[00:02.00]译文二"},
            },
            {"lrc": {"lyric": "[00:01.00]原文"}},
            {"lrc": {"lyric": "无时间原文"}},
            {"lrc": {"lyric": "[00:01.00]另一版"}},
        ]
        with (
            patch("lyrics_source.netease_search", return_value=songs),
            patch("lyrics_source.netease_lyrics", side_effect=lyric_responses) as lyrics,
        ):
            candidates = inspect_candidates("Song", "Artist", "cookie")

        self.assertEqual(5, lyrics.call_count)
        self.assertEqual(5, len(candidates))
        self.assertEqual(2, choose_candidate(candidates).song["id"])

    def test_failed_netease_lookup_writes_fixed_two_site_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary)
            with (
                patch("lyrics_source.netease_search", return_value=[]),
                patch("lyrics_source.read_netease_cookie", return_value=None),
                self.assertRaisesRegex(LookupError, "固定双站方案"),
            ):
                fetch_netease_bilingual("Song", "Artist", out_dir)

            plan = (out_dir / "fallback-search.md").read_text(encoding="utf-8")
            self.assertIn("UtaTen", plan)
            self.assertIn("TuneCore Japan", plan)
            self.assertIn("site:utaten.com", urllib.parse.unquote(plan))
            self.assertIn("site:linkco.re", urllib.parse.unquote(plan))

    def test_fetch_writes_lrc_and_markdown_without_json_file(self) -> None:
        candidate_songs = [
            {
                "id": 123,
                "name": "Song",
                "artists": [{"name": "Artist"}],
            }
        ]
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary)
            with (
                patch("lyrics_source.netease_search", return_value=candidate_songs),
                patch(
                    "lyrics_source.netease_lyrics",
                    return_value={
                        "lrc": {"lyric": "[00:01.00]原文"},
                        "tlyric": {"lyric": "[00:01.00]译文"},
                    },
                ),
                patch("lyrics_source.read_netease_cookie", return_value="cookie"),
            ):
                chosen = fetch_netease_bilingual("Song", "Artist", out_dir)

            self.assertEqual(1.0, chosen.coverage)
            self.assertTrue((out_dir / "original.lrc").is_file())
            self.assertTrue((out_dir / "chinese.lrc").is_file())
            self.assertTrue((out_dir / "lookup.md").is_file())
            self.assertEqual([], list(out_dir.glob("*.json")))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from alignment_workflow import _lyric_text
from lyrics_source import (
    choose_candidate, fetch_netease_bilingual, has_complete_timing,
    inspect_candidates, lyric_text, parse_lrc, translation_coverage, lyric_response_status,
)


class PlainLyricsTests(unittest.TestCase):
    def test_response_states_distinguish_empty_metadata_plain_and_timed(self):
        self.assertEqual("本次接口未返回原文", lyric_response_status(" \n"))
        self.assertIn("接口有内容但未识别正文", lyric_response_status("作词 : 测试\n【1A】"))
        self.assertIn("待对齐", lyric_response_status("测试原文\n测试原文"))
        self.assertIn("完整时间标签", lyric_response_status("[00:01.00]测试原文"))

    def test_api_failure_is_not_treated_as_absent_lyrics(self):
        song = {"id": 1, "name": "Song", "artists": [{"name": "Artist"}]}
        with patch("lyrics_source.netease_search", return_value=[song]), patch(
            "lyrics_source.netease_lyrics", return_value={"code": 429}
        ):
            with self.assertRaisesRegex(RuntimeError, "接口失败.*不能判为无歌词"):
                inspect_candidates("Song", "Artist", None)

    def test_non_text_lyric_field_is_reported_as_invalid_response(self):
        song = {"id": 1, "name": "Song", "artists": [{"name": "Artist"}]}
        with patch("lyrics_source.netease_search", return_value=[song]), patch(
            "lyrics_source.netease_lyrics", return_value={"code": 200, "lrc": {"lyric": []}}
        ):
            with self.assertRaisesRegex(ValueError, "字段格式异常"):
                inspect_candidates("Song", "Artist", None)

    def candidates(self, original: str, *, exact_artist: str = "Artist"):
        songs = [
            {"id": 1, "name": "AIM STAR", "artists": [{"name": exact_artist}]},
            {"id": 2, "name": "Star", "artists": [{"name": "Other"}]},
        ]
        responses = {
            1: {"lrc": {"lyric": original}},
            2: {"lrc": {"lyric": "[00:01.00]别首原文"},
                "tlyric": {"lyric": "[00:01.00]别首译文"}},
        }
        with patch("lyrics_source.netease_search", return_value=songs), patch(
            "lyrics_source.netease_lyrics", side_effect=lambda song_id, _: responses[song_id]
        ):
            return inspect_candidates("AIM STAR", "Artist", None)

    def test_plain_text_keeps_repeats_and_removes_only_metadata(self):
        raw = "作词 : 测试\n作曲 : 测试\n[ar:Artist]\n【1A】\n测试甲\n\n测试乙\n【2サビ】\n测试甲\n【普通歌词】"
        expected = ["测试甲", "测试乙", "测试甲", "【普通歌词】"]
        self.assertEqual(expected, lyric_text(raw))
        self.assertEqual([], parse_lrc(raw))
        self.assertFalse(has_complete_timing(raw))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "original.lrc"
            path.write_text(raw, encoding="utf-8")
            self.assertEqual(expected, _lyric_text(path))

    def test_plain_original_is_selected_over_translated_unrelated_song(self):
        for artist in ("Artist", "別表記"):
            with self.subTest(artist=artist):
                chosen = choose_candidate(self.candidates("测试甲\n测试乙", exact_artist=artist))
                self.assertEqual(1, chosen.song["id"])
                self.assertEqual(2, chosen.lyric_lines)

    def test_empty_exact_song_cannot_fall_back_to_unrelated_song(self):
        self.assertIsNone(choose_candidate(self.candidates("")))

    def test_partial_title_without_artist_match_is_not_adopted(self):
        self.assertIsNone(choose_candidate(self.candidates("")[1:]))

    def test_plain_translation_is_not_assumed_aligned_by_line_count(self):
        self.assertEqual((0.0, (), 2, 2), translation_coverage("测试甲\n测试乙", "译文甲\n译文乙"))
        self.assertEqual((0.0, (), 2, 2), translation_coverage("[00:01.00]测试甲\n[00:02.00]测试乙", "译文甲\n译文乙"))

    def test_mixed_timing_does_not_lose_untimed_lines_or_claim_complete_coverage(self):
        original = "[00:01.00]测试甲\n测试乙"
        self.assertEqual(["测试甲", "测试乙"], lyric_text(original))
        self.assertFalse(has_complete_timing(original))
        self.assertEqual((0.0, (), 2, 1), translation_coverage(original, "[00:01.00]译文甲"))

    def test_fetch_preserves_raw_plain_text_and_reports_no_timing(self):
        raw = "作词 : 测试\n【1A】\n测试甲\n测试甲"
        candidates = self.candidates(raw)
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            with patch("lyrics_source.read_netease_cookie", return_value=None), patch(
                "lyrics_source.inspect_candidates", return_value=candidates
            ):
                fetch_netease_bilingual("AIM STAR", "Artist", out)
            self.assertEqual(raw + "\n", (out / "original.lrc").read_text(encoding="utf-8"))
            self.assertFalse((out / "chinese.lrc").exists())
            self.assertFalse((out / "fallback-search.md").exists())
            report = (out / "lookup.md").read_text(encoding="utf-8")
            self.assertIn("song?id=1", report)
            self.assertIn("含无时间标签正文，待对齐", report)
            self.assertIn("未核验", report)
            self.assertIn("接口未提供", report)
            self.assertIn(f"|{len(raw)}|0|正文含无时间标签行，待对齐|", report)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations
# ruff: noqa: E402
import csv
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from PIL import Image
import common
import mv_pipeline
from official_subtitle import apply_official_translations, prepare_official_subtitle, match_netease_reference, OfficialCue
from lyrics_source import Candidate, lyric_lines
from youtube_cover import prepare_youtube_cover, youtube_video_id

VTT = "WEBVTT\n\n00:01.000 --> 00:02.000\n最初の一行\n\n00:04.000 --> 00:05.000\n次の一行\n"


class ReviewEfficiencyTests(unittest.TestCase):
    def workspace(self, root):
        mv = root / "workspace/mvs/fixture"
        common.ensure_workspace(mv)
        return mv

    def test_sparse_edits_bind_japanese_preserve_other_rows_and_master(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(common, "project_root", return_value=Path(temp)):
            mv = self.workspace(Path(temp))
            (mv / "source/source.ja.vtt").write_text(VTT, encoding="utf-8")
            prepare_official_subtitle(mv)
            folder = mv / "review/official-subtitle"
            (folder / "chinese.tsv").write_text("line\tchinese\n1\t第一句\n2\t第二句\n", encoding="utf-8")
            apply_official_translations(mv, "GPT-6-Astra")
            master = mv / "subtitle/master.srt"
            master.write_bytes(b"human-reviewed")
            edits = folder / "edits.tsv"
            edits.write_text("line\tjapanese\tchinese\n2\t次の一行\t修正第二句\n", encoding="utf-8")
            apply_official_translations(mv, "GPT-6-Astra", edits=edits)
            with (folder / "translation.tsv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream, delimiter="\t"))
            self.assertEqual(["第一句", "修正第二句"], [r["chinese"] for r in rows])
            self.assertEqual(["1000", "4000"], [r["start_ms"] for r in rows])
            self.assertEqual(b"human-reviewed", master.read_bytes())
            self.assertIn("修正第二句", (folder / "review.md").read_text(encoding="utf-8"))
            before = (folder / "translation.tsv").read_bytes()
            for body in ["2\t違う日文\t中文", "3\t次の一行\t中文",
                         "2\t次の一行\t中文\n2\t次の一行\t中文", "2\t次の一行\t"]:
                edits.write_text("line\tjapanese\tchinese\n" + body + "\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    apply_official_translations(mv, "GPT-6-Astra", edits=edits)
                self.assertEqual(before, (folder / "translation.tsv").read_bytes())
            edits.write_text("line\tjapanese\tchinese\n2\t次の一行\t再次修改\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "模型名"):
                apply_official_translations(mv, edits=edits)

    def test_timing_projection_ignores_trailing_credit_and_unmatched_lyric(self):
        original = "[00:10.00]最初の一行\n[00:20.00]真ん中の行\n[00:30.00]次の一行\n[00:50.00]無関係の歌詞\n[01:00.00]混音工程师 : Engineer\n"
        candidate = Candidate({}, original, original, 0, 0, 0, 1.0, ())
        self.assertEqual(4, len(lyric_lines(original)))
        cues = [OfficialCue(1000, 2500, "最初の一行"), OfficialCue(11000, 12000, "真ん中の行"), OfficialCue(21000, 29000, "次の一行")]
        matches = match_netease_reference(cues, candidate)
        self.assertEqual([0, 0, 0], [row.timing_delta_ms for row in matches])
        self.assertEqual("relative-unavailable", match_netease_reference(cues[:1], candidate)[0].timing_hint)

    def youtube_workspace(self, root):
        mv = self.workspace(root)
        (mv / "review/source-acquisition.json").write_text(json.dumps({"url": "https://www.youtube.com/watch?v=vk6dcMsqTTI"}), encoding="utf-8")
        return mv

    def test_youtube_thumbnail_wins_over_bigger_album_and_reuses_output(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(common, "project_root", return_value=Path(temp)):
            mv = self.youtube_workspace(Path(temp))
            Image.new("RGB", (1500, 1500), "red").save(mv / "work/cover-source.jpg")
            Image.new("RGB", (1280, 720), "blue").save(mv / "source/source.webp")
            self.assertEqual([], mv_pipeline._available_cover_sources(mv))
            with patch("youtube_cover.urlopen") as request:
                cover = prepare_youtube_cover(mv)
                timestamp = cover.stat().st_mtime_ns
                self.assertEqual(cover, prepare_youtube_cover(mv))
                request.assert_not_called()
            self.assertEqual(timestamp, cover.stat().st_mtime_ns)
            with Image.open(cover) as opened:
                self.assertEqual((1280, 720), opened.size)
                self.assertGreater(opened.getpixel((10, 10))[2], 240)
            self.assertEqual([cover], mv_pipeline._available_cover_sources(mv))
            self.assertEqual([], list((mv / "work").glob("*partial*")))

    def test_missing_youtube_thumbnail_download_is_bounded_and_low_resolution_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(common, "project_root", return_value=Path(temp)):
            mv = self.youtube_workspace(Path(temp))
            Image.new("RGB", (1500, 1500), "red").save(mv / "work/cover-source.jpg")
            buffer = io.BytesIO()
            Image.new("RGB", (320, 180)).save(buffer, format="JPEG")
            buffer.seek(0)
            with patch("youtube_cover.urlopen", return_value=buffer) as request:
                with self.assertRaisesRegex(ValueError, "尺寸不足"):
                    prepare_youtube_cover(mv)
                request.assert_called_once_with("https://i.ytimg.com/vi/vk6dcMsqTTI/maxresdefault.jpg", timeout=15)
            self.assertEqual([], mv_pipeline._available_cover_sources(mv))
            (mv / "review/source-acquisition.json").write_text(json.dumps({"url": "https://youtube.com.evil/watch?v=vk6dcMsqTTI"}), encoding="utf-8")
            self.assertIsNone(youtube_video_id(mv))

    def test_finish_overlaps_cover_with_render_and_ignores_unfinished_copy(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(common, "project_root", return_value=Path(temp)):
            mv = self.workspace(Path(temp))
            (mv / "subtitle/master.srt").write_text("human", encoding="utf-8")
            (mv / "work/publish-copy.md").write_text("TODO", encoding="utf-8")
            (mv / "subtitle/chinese-source.txt").write_text("中文歌词来源：网易云音乐", encoding="utf-8")
            video = mv / "output/fixture.hardsub.v01.mp4"
            video.write_bytes(b"video")
            started, rendering = threading.Event(), threading.Event()
            def cover(*args):
                started.set()
                if not rendering.wait(3):
                    raise AssertionError("render did not overlap")
            def render(*args):
                self.assertTrue(started.wait(3))
                rendering.set()
                return video
            with patch("mv_pipeline.prepare_youtube_cover", side_effect=cover), patch("mv_pipeline.style_subtitle"), patch("mv_pipeline.preview"), patch("mv_pipeline.render", side_effect=render), patch("mv_pipeline.validate"), patch("mv_pipeline.delivery_status", return_value=mv / "output/status.md"), patch("mv_pipeline.stage_delivery") as stage:
                mv_pipeline.finish(mv)
                stage.assert_not_called()


if __name__ == "__main__":
    unittest.main()

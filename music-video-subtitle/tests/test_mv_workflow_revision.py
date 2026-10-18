from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import MEDIA_SUFFIXES, ensure_workspace, find_source_video, preview_source
from lyrics_source import translation_coverage
from official_subtitle import (
    apply_official_translations, build_official_candidate, discover_official_subtitle,
    prepare_official_subtitle, prepare_official_subtitle_if_present,
)
from mv_pipeline import build_subtitle, import_source, preview, render, style_subtitle, validate
from song_candidate import _prepare_candidate_preview, build_song_candidate
from subtitle_io import load_cues


ENGLISH = "WEBVTT\n\n00:00:01.250 --> 00:00:02.500\nStay with me\n\n00:00:03.000 --> 00:00:04.250\nLet it go\n"


class RevisedWorkflowTests(unittest.TestCase):
    def workspace(self, directory):
        return ensure_workspace(Path(directory) / "workspace/mvs/fixture")

    def candidate(self, mv):
        (mv / "source/source.en.vtt").write_text(ENGLISH, encoding="utf-8")
        prepare_official_subtitle(mv)
        (mv / "review/official-subtitle/chinese.tsv").write_text(
            "line\tchinese\n1\t留在我身边\n2\t随它去\n", encoding="utf-8")
        apply_official_translations(mv, "fixture-model")
        return build_official_candidate(mv)

    def test_english_official_timing_promotes_to_ass_without_master_srt(self):
        with tempfile.TemporaryDirectory() as folder:
            mv = self.workspace(folder)
            candidate = self.candidate(mv)
            master = build_subtitle(mv, candidate)
            self.assertEqual(".ass", master.suffix)
            self.assertFalse((mv / "subtitle/master.srt").exists())
            self.assertEqual(load_cues(candidate), load_cues(master))
            self.assertEqual((1.25, 2.5), (load_cues(master)[0].start, load_cues(master)[0].end))
            before = master.read_bytes()
            with self.assertRaises(FileExistsError):
                build_subtitle(mv, candidate)
            self.assertEqual(before, master.read_bytes())

    def test_any_manual_language_and_regional_variants_can_supply_timing(self):
        for language, original in (("en-US", "Stay with me"), ("ko", "함께 있어"), ("es", "Quédate aquí"), ("zh-Hans", "留在这里")):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as folder:
                mv = self.workspace(folder)
                track = mv / "source" / f"source.{language}.vtt"
                track.write_text(ENGLISH.replace("Stay with me", original), encoding="utf-8")
                self.assertEqual(track, discover_official_subtitle(mv))
                self.assertTrue(prepare_official_subtitle(mv).is_file())

    def test_multiple_tracks_need_sung_language_without_japanese_bias(self):
        with tempfile.TemporaryDirectory() as folder:
            mv = self.workspace(folder)
            for lang in ("ja", "en", "zh-Hans"):
                (mv / "source" / f"source.{lang}.vtt").write_text(ENGLISH, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source.en.vtt"):
                discover_official_subtitle(mv)
            self.assertIsNone(prepare_official_subtitle_if_present(mv))
            self.assertTrue((mv / "review/official-subtitle/selection-required.md").exists())
            with patch("song_candidate.doctor_state") as doctor, self.assertRaises(ValueError):
                build_song_candidate(mv)
            doctor.assert_not_called()
            (mv / "source/language.txt").write_text("en-US", encoding="utf-8")
            self.assertEqual("source.en.vtt", discover_official_subtitle(mv).name)

    def test_language_metadata_selects_english_over_japanese(self):
        with tempfile.TemporaryDirectory() as folder:
            mv = self.workspace(folder)
            for lang in ("ja", "en"):
                (mv / "source" / f"source.{lang}.vtt").write_text(ENGLISH, encoding="utf-8")
            (mv / "source/source.info.json").write_text(json.dumps({"language": "en"}), encoding="utf-8")
            self.assertEqual("source.en.vtt", discover_official_subtitle(mv).name)

    def test_auto_caption_file_is_not_promoted_as_official(self):
        with tempfile.TemporaryDirectory() as folder:
            mv = self.workspace(folder)
            (mv / "source/source.en.auto.vtt").write_text(ENGLISH, encoding="utf-8")
            self.assertIsNone(discover_official_subtitle(mv))

    def test_short_english_lyrics_still_require_translation(self):
        coverage, missing, _, _ = translation_coverage("[00:01.00]Let it go\n[00:02.00]Oh", None)
        self.assertEqual(0.0, coverage)
        self.assertEqual((1000,), missing)

    def test_candidate_rerun_reuses_one_video_and_preserves_ass(self):
        with tempfile.TemporaryDirectory() as folder:
            mv = self.workspace(folder)
            (mv / "source/source.mkv").write_bytes(b"original")
            candidate = self.candidate(mv)
            source = find_source_video(mv)
            self.assertEqual(mv / "review/preview/source.mkv", source)
            self.assertFalse((mv / "source/source.mkv").exists())
            paired_ass = source.with_suffix(".ass")
            paired_ass.write_bytes(b"human-styles")
            with patch("song_candidate.shutil.copy2", wraps=__import__("shutil").copy2) as copier:
                video, subtitle, _ = _prepare_candidate_preview(mv, source, candidate)
            self.assertEqual(source, video)
            self.assertEqual("source.candidate.srt", subtitle.name)
            self.assertNotIn(source, [call.args[0] for call in copier.call_args_list])
            self.assertEqual(b"human-styles", paired_ass.read_bytes())
            self.assertEqual([source], [p for p in mv.rglob("*") if p.suffix in MEDIA_SUFFIXES])

    def test_duplicate_legacy_media_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            mv = self.workspace(folder)
            (mv / "source/source.mkv").write_bytes(b"old")
            directory = mv / "review/preview"
            directory.mkdir()
            (directory / "source.mkv").write_bytes(b"different")
            with self.assertRaisesRegex(ValueError, "重复"):
                preview_source(mv)
            self.assertEqual(b"old", (mv / "source/source.mkv").read_bytes())

    def test_style_preserves_manual_ass_overrides(self):
        with tempfile.TemporaryDirectory() as folder:
            mv = self.workspace(folder)
            master = build_subtitle(mv, self.candidate(mv))
            text = master.read_text(encoding="utf-8").replace(r"\fs80", r"\fs72")
            master.write_text(text, encoding="utf-8")
            (mv / "source/source.mkv").write_bytes(b"source")
            with patch("mv_pipeline.probe_media", return_value=SimpleNamespace(play_res=(1440, 1080))):
                styled = style_subtitle(mv)
            self.assertIn(r"\fs72", styled.read_text(encoding="utf-8"))
            self.assertIn("PlayResX: 1440", styled.read_text(encoding="utf-8"))
            self.assertEqual(text, master.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.environ.get("MV_SUBTITLE_FFMPEG") and os.environ.get("MV_SUBTITLE_FFPROBE"), "requires bound FFmpeg/ffprobe")
    def test_real_english_ass_preview_render_and_decode(self):
        with tempfile.TemporaryDirectory() as folder:
            mv = self.workspace(folder)
            source = Path(folder) / "input.mp4"
            subprocess.run([os.environ["MV_SUBTITLE_FFMPEG"], "-nostdin", "-v", "error",
                            "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=24:d=6",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                            "-shortest", str(source)], check=True)
            imported = import_source(mv, source)
            source_bytes = imported.read_bytes()
            master = build_subtitle(mv, self.candidate(mv))
            style_subtitle(mv)
            preview(mv)
            final = render(mv, preset="ultrafast")
            report = validate(mv, final)
            self.assertTrue(report.is_file())
            from review_checks import review_check_issues
            checks = review_check_issues(mv, 1)
            self.assertEqual([], checks["machine"])
            self.assertTrue(checks["human"])
            self.assertEqual(source_bytes, imported.read_bytes())
            self.assertFalse((mv / "subtitle/master.srt").exists())
            self.assertEqual(load_cues(master), load_cues(mv / "output/fixture.subtitle.v01.ass"))
            media = [p for p in mv.rglob("*") if p.suffix in MEDIA_SUFFIXES]
            self.assertCountEqual([imported, final], media)
            self.assertFalse(imported.with_suffix(".srt").exists())


if __name__ == "__main__":
    unittest.main()

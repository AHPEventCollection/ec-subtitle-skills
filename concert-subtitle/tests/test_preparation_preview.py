from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_review_clips import build_source_preview
from common import render_srt, write_json
from merge_concert_subtitles import render_ass
from song_progress import render_song_progress_ass
from structure_timing import write_srt


class PreparationPreviewTests(unittest.TestCase):
    def test_explicit_notes_never_reach_ass_or_srt_but_real_lyrics_survive(self):
        event = {"start": 1, "end": 2, "source_text": "大阪", "translation": "大阪"}
        for marker in ("［後半要確認］", "【前半句待确认】", "[待核对]", "TODO"):
            for field in ("source_text", "translation"):
                candidate = {**event, field: marker}
                with self.subTest(marker=marker, field=field):
                    with self.assertRaises(ValueError):
                        render_ass([candidate], "sample")
                    with self.assertRaises(ValueError):
                        render_srt([candidate])
                    with tempfile.TemporaryDirectory() as temporary:
                        path = Path(temporary) / "candidate.srt"
                        with self.assertRaises(ValueError):
                            write_srt(path, [candidate])
                        self.assertFalse(path.exists())
        lyric = {**event, "source_text": "I KNOW 才能 確認中", "translation": "正在确认自己的才能"}
        self.assertIn("確認中", render_ass([lyric], "sample"))
        self.assertIn("確認中", render_srt([lyric]))
        blank = {**event, "source_text": "", "translation": ""}
        self.assertIn("00:00:01,000 --> 00:00:02,000", render_srt([blank]))

    def test_source_titles_and_progress_geometry_follow_material_analysis(self):
        manifest = {"sections": [
            {"id": "song_01", "type": "song", "start": 0, "end": 5, "title": "First", "artist": "Band"},
            {"id": "song_02", "type": "song", "start": 7, "end": 12, "title": "Second", "artist": "Band", "source_song_title": False},
        ], "presentation": {"source_song_titles": True,
                            "progress_bar": {"x": 0, "y": 0, "width": 1920, "height": 12}}}
        text = render_song_progress_ass(manifest)
        dialogue = "\n".join(line for line in text.splitlines() if line.startswith("Dialogue:"))
        self.assertNotIn("First", dialogue)
        self.assertIn("Second", dialogue)
        self.assertIn(r"\pos(0,0)", dialogue)
        self.assertIn("1920 12", dialogue)
        manifest["presentation"]["progress_bar"]["x"] = 1
        with self.assertRaises(ValueError):
            render_song_progress_ass(manifest)

    def test_full_preview_reuses_video_and_preserves_manual_caption_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            manifest = {"source_media": str(source), "duration_seconds": 10, "sections": [
                {"id": "mc_01", "type": "mc", "start": 0, "end": 10},
            ]}
            events_path = root / "run/sections/mc_01/events.json"
            event = {"start": 1, "end": 2, "source_text": "ありがとう", "translation": "谢谢"}
            write_json(events_path, {"events": [event]})
            def render(**kwargs):
                kwargs["output_path"].write_bytes(b"preview")
            with patch("build_review_clips.load_manifest", return_value=manifest), patch(
                "build_review_clips.resolve_binary", return_value=Path("ffmpeg")
            ), patch("build_review_clips.render_review_video", side_effect=render) as encode:
                subtitle = build_source_preview(root, lightweight_video=True)
                video = subtitle.with_suffix(".mp4")
                self.assertTrue(video.is_file())
                write_json(events_path, {"events": [{**event, "translation": "谢谢大家"}]})
                build_source_preview(root, lightweight_video=True)
                self.assertEqual(1, encode.call_count)
                self.assertIn("谢谢大家", subtitle.read_text(encoding="utf-8-sig"))
                subtitle.write_text("manual", encoding="utf-8")
                with self.assertRaises(FileExistsError):
                    build_source_preview(root, lightweight_video=True)
                self.assertEqual("manual", subtitle.read_text())
                self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()

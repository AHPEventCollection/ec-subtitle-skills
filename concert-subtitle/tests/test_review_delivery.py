import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build_review_clips import build_review_clips, build_source_preview
from common import write_json


class ReviewDeliveryTests(unittest.TestCase):
    def test_single_video_delivery_and_source_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"original")
            events_path = root / "run/sections/song_01/events.json"
            write_json(events_path, {"events": [
                {"start": 1, "end": 2, "source_text": "歌", "translation": "歌"}
            ]})
            manifest = {"source_media": str(source), "sections": [
                {"id": "song_01", "start": 10, "end": 20},
                {"id": "mc_01", "start": 20, "end": 30},
            ]}
            before = events_path.read_bytes()
            with patch("build_review_clips.load_manifest", return_value=manifest):
                candidate = build_source_preview(root)
                self.assertIn("0:00:11.00,0:00:12.00", candidate.read_text(encoding="utf-8-sig"))
                self.assertIn("mc_01", (candidate.parent / "打开方式.txt").read_text(encoding="utf-8-sig"))
                self.assertEqual(before, events_path.read_bytes())
                self.assertFalse((root / "output").exists())
                with self.assertRaises(FileExistsError):
                    build_source_preview(root)
                def render(**kwargs):
                    kwargs["output_path"].write_bytes(b"encoded")
                with patch("build_review_clips.resolve_binary", return_value=Path("ffmpeg")), patch(
                    "build_review_clips.render_review_video", side_effect=render
                ):
                    result = build_review_clips(root, {"song_01"})
                    self.assertEqual(root / "待检查/song_01.mp4", Path(result[0]["video"]))
                    self.assertFalse((root / "run/review").exists())
                    self.assertEqual(1, len(list((root / "待检查").rglob("*.mp4"))))
                    with patch("build_review_clips.render_review_video") as repeat_render:
                        build_review_clips(root, {"song_01"})
                        repeat_render.assert_not_called()
                    Path(result[0]["subtitle"]).write_text("manual", encoding="utf-8")
                    with self.assertRaises(FileExistsError):
                        build_review_clips(root, {"song_01"})
                    self.assertEqual("manual", Path(result[0]["subtitle"]).read_text())


if __name__ == "__main__":
    unittest.main()

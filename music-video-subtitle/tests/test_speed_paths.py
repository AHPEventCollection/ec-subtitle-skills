from __future__ import annotations

# ruff: noqa: E402
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ensure_workspace
from mv_pipeline import prepare_official_review
from official_subtitle import prepare_official_subtitle
from youtube_acquire import _compatible_audio


class SpeedPathsTests(unittest.TestCase):
    def test_official_review_builds_pair_but_preserves_master(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            mv = ensure_workspace(Path(temporary) / "workspace/mvs/fixture")
            (mv / "source/source.mkv").write_bytes(b"original-video")
            (mv / "source/source.ja.vtt").write_text(
                "WEBVTT\n\n00:01.000 --> 00:03.000\n最初の一行\n", encoding="utf-8")
            prepare_official_subtitle(mv)
            translations = mv / "review/official-subtitle/chinese.tsv"
            translations.write_text("line\tchinese\n1\t第一句\n", encoding="utf-8")
            master = mv / "subtitle/master.srt"
            master.write_bytes(b"human-edited")
            def frames(*_args):
                directory = mv / "review/source-visual-audit/frames"
                directory.mkdir(parents=True)
                Image.new("RGB", (960, 540), "white").save(directory / "frame-001.jpg")
            with patch("mv_pipeline._sample_source_frames", side_effect=frames), patch(
                "mv_pipeline.resolve_binary", return_value=Path("ffmpeg")):
                candidate = prepare_official_review(mv, translation_model="Test Model")
            self.assertEqual(b"human-edited", master.read_bytes())
            self.assertEqual(candidate.read_bytes(), (mv / "review/candidate-preview/source.srt").read_bytes())
            self.assertEqual(b"original-video", (mv / "review/candidate-preview/source.mkv").read_bytes())
            self.assertTrue((mv / "review/source-visual-audit/contact-sheet.jpg").is_file())
            self.assertFalse(list((mv / "output").glob("*.ass")))

    def _audio_case(self, wrong_id=False, fail=False):
        with tempfile.TemporaryDirectory() as temporary:
            mv = ensure_workspace(Path(temporary) / "workspace/mvs/fixture")
            staging = mv / "work/youtube-acquire"
            staging.mkdir()
            media = staging / "source.mkv"
            media.write_bytes(b"original-vp9-opus")
            command = ["global", "download", "--output", str(staging / "source.%(ext)s"),
                       "--container", "mkv", "--write-subs", "--sub-langs", "ja", "--write-thumbnail"]
            audit = {"attempts": []}
            def download(args, **_kwargs):
                self.assertEqual("mp4", args[args.index("--container") + 1])
                self.assertNotIn("--write-subs", args)
                audio = staging / "compatible-audio/source.mp4"
                audio.write_bytes(b"official-aac")
                result = "\n".join([
                    "__YTDLP_GLOBAL_ID__=" + ("different" if wrong_id else "fixture"),
                    f"__YTDLP_GLOBAL_FILE__={audio.resolve()}",
                    json.dumps({"operation": "download", "status": "verified"})])
                return subprocess.CompletedProcess(args, 1 if fail else 0, result, "")
            def probe(path, *_args):
                return SimpleNamespace(duration=10.0, width=3840, height=2160,
                    video_codec="vp9", audio_codec="opus" if path == media else "aac")
            def remux(args, **kwargs):
                self.assertEqual("copy", args[args.index("-c") + 1])
                self.assertEqual(["0:v:0", "1:a:0"], [args[i+1] for i,v in enumerate(args) if v == "-map"])
                Path(args[-1]).write_bytes(b"original-vp9-official-aac")
            with patch("youtube_acquire._run_download", side_effect=download), patch(
                "youtube_acquire.probe_media", side_effect=probe), patch(
                "youtube_acquire.subprocess.run", side_effect=remux) as run:
                inputs = (media, {"media": {"streams": [{"codec_type": "audio", "codec_name": "opus"}]}},
                    {"id": "fixture"}, command, {}, audit, mv / "review/source-acquisition.json", "ffmpeg", "ffprobe", mv)
                if wrong_id or fail:
                    with self.assertRaises(RuntimeError):
                        _compatible_audio(*inputs)
                    run.assert_not_called()
                    self.assertEqual(b"original-vp9-opus", media.read_bytes())
                else:
                    _compatible_audio(*inputs)
                    self.assertEqual(b"original-vp9-official-aac", media.read_bytes())
            self.assertEqual(b"original-vp9-opus", (mv / "work/youtube-original/source.mkv").read_bytes())

    def test_opus_keeps_original_picture_and_copies_official_audio(self):
        self._audio_case()

    def test_audio_from_other_video_is_rejected(self):
        self._audio_case(wrong_id=True)

    def test_failed_audio_download_preserves_original(self):
        self._audio_case(fail=True)

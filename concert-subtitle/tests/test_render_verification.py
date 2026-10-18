from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from render_policy import verify_encoded_media  # noqa: E402


class RenderVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / "source.mp4"
        self.output = Path(self.temporary.name) / "output.mp4"
        self.source.write_bytes(b"synthetic-source")
        self.output.write_bytes(b"synthetic-output")
        self.original = {
            "format": {"duration": "30"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 360, "duration": "30"},
                {"codec_type": "audio", "codec_name": "aac", "duration": "30"},
            ],
        }

    def verify(self, output, decode_error="", decode_code=0):
        results = [
            subprocess.CompletedProcess([], 0, json.dumps(self.original), ""),
            subprocess.CompletedProcess([], 0, json.dumps(output), ""),
            subprocess.CompletedProcess([], decode_code, "", decode_error),
        ]
        with patch("render_policy.subprocess.run", side_effect=results) as run:
            media = verify_encoded_media(Path("ffmpeg"), self.output, source=self.source)
        return media, run.call_args.args[0]

    def test_complete_media_decodes_both_streams_without_a_time_limit(self):
        media, command = self.verify(self.original)
        self.assertEqual("passed", media["verification"]["full_decode"])
        self.assertNotIn("-t", command)
        self.assertIn("0:v:0", command)
        self.assertIn("0:a:0", command)

    def test_missing_audio_is_rejected(self):
        media = copy.deepcopy(self.original)
        media["streams"].pop()
        with self.assertRaisesRegex(ValueError, "缺少音轨"):
            self.verify(media)

    def test_short_or_nonfinite_output_duration_is_rejected(self):
        for value in ("1", "0", "nan", "inf", "invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                media = copy.deepcopy(self.original)
                media["format"]["duration"] = value
                self.verify(media)

    def test_short_stream_cannot_hide_behind_full_container_duration(self):
        for index in (0, 1):
            with self.subTest(stream=index), self.assertRaisesRegex(ValueError, "流时长"):
                media = copy.deepcopy(self.original)
                media["streams"][index]["duration"] = "1"
                self.verify(media)

    def test_wrong_dimensions_are_rejected(self):
        media = copy.deepcopy(self.original)
        media["streams"][0]["width"] = 320
        with self.assertRaisesRegex(ValueError, "尺寸"):
            self.verify(media)

    def test_decode_error_is_rejected_even_with_zero_exit_code(self):
        for code in (0, 1):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, "完整音视频解码失败"):
                self.verify(self.original, decode_error="corrupt frame near the end", decode_code=code)

    def test_source_without_stream_duration_uses_container_duration(self):
        for stream in self.original["streams"]:
            stream.pop("duration")
        media, _ = self.verify(self.original)
        self.assertEqual("passed", media["verification"]["full_decode"])


@unittest.skipUnless(os.environ.get("CONCERT_SUBTITLE_MEDIA_SMOKE") == "1", "requires FFmpeg media smoke")
class RenderMediaSmokeTests(unittest.TestCase):
    def test_real_valid_silent_short_and_truncated_stream_files(self):
        ffmpeg = Path(os.environ["CONCERT_SUBTITLE_FFMPEG"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def make(name, video_seconds, audio_seconds, size="320x180"):
                target = root / name
                command = [str(ffmpeg), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                           f"testsrc2=s={size}:r=25:d={video_seconds}"]
                if audio_seconds:
                    command.extend(["-f", "lavfi", "-i", f"sine=frequency=440:duration={audio_seconds}"])
                command.extend(["-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
                                "-pix_fmt", "yuv420p", "-c:a", "aac", str(target)])
                subprocess.run(command, check=True, capture_output=True)
                return target

            source = make("source.mp4", 2, 2)
            complete = make("complete.mp4", 2, 2)
            self.assertEqual("passed", verify_encoded_media(ffmpeg, complete, source=source)["verification"]["full_decode"])
            cases = (
                ("silent.mp4", 1, None, "320x180", "缺少音轨"),
                ("short.mp4", 1, 1, "320x180", "成片时长"),
                ("short-video.mp4", 1, 2, "320x180", "video流时长"),
                ("short-audio.mp4", 2, 1, "320x180", "audio流时长"),
                ("wrong-size.mp4", 2, 2, "160x90", "尺寸"),
            )
            for name, video_seconds, audio_seconds, size, message in cases:
                with self.subTest(name=name):
                    output = make(name, video_seconds, audio_seconds, size)
                    with self.assertRaisesRegex(ValueError, message):
                        verify_encoded_media(ffmpeg, output, source=source)


if __name__ == "__main__":
    unittest.main()

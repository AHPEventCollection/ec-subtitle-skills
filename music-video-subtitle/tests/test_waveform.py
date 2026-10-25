from __future__ import annotations

import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ensure_workspace, resolve_binary  # noqa: E402
from waveform import generate_preview_waveform, validate_waveform  # noqa: E402


class WaveformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg = str(resolve_binary("ffmpeg"))
            resolve_binary("ffprobe")
        except FileNotFoundError as error:
            raise unittest.SkipTest(str(error))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.mv = ensure_workspace(self.root / "workspace/mvs/fixture")
        self.source = self.mv / "review/preview/source.mov"
        self.source.parent.mkdir(parents=True, exist_ok=True)

    def make_media(self, *, silent=False, no_audio=False):
        tone = self.root / "tone.wav"
        with wave.open(str(tone), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b"".join(
                struct.pack("<h", 0 if silent else round(12000 * math.sin(2 * math.pi * 440 * i / 8000)))
                for i in range(8000)
            ))
        command = [self.ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                   "color=c=black:s=64x64:r=10:d=3"]
        if not no_audio:
            command += ["-itsoffset", "1", "-i", str(tone), "-map", "0:v:0", "-map", "1:a:0",
                        "-c:a", "pcm_s16le"]
        command += ["-c:v", "mpeg4", "-t", "3", str(self.source)]
        subprocess.run(command, check=True, capture_output=True)

    def test_real_audio_keeps_delayed_onset_and_trailing_silence(self):
        self.make_media()
        source_bytes = self.source.read_bytes()
        output = generate_preview_waveform(self.mv)
        self.assertEqual(output, self.source.with_suffix(".waveform.json"))
        data = json.loads(output.read_text(encoding="utf-8"))
        self.assertAlmostEqual(data["duration"], 3, places=3)
        self.assertEqual(len(data["peaks"]), 240)
        audible = [i for i, peak in enumerate(data["peaks"]) if peak > 0.1]
        self.assertTrue(79 <= audible[0] <= 81, audible[:3])
        self.assertTrue(158 <= audible[-1] <= 161, audible[-3:])
        self.assertTrue(all(peak == 0 for peak in data["peaks"][:78]))
        self.assertTrue(all(peak == 0 for peak in data["peaks"][162:]))
        self.assertEqual(self.source.read_bytes(), source_bytes)
        validate_waveform(output, 3)

    def test_valid_existing_waveform_is_reused_without_decoding(self):
        self.make_media()
        output = generate_preview_waveform(self.mv)
        before = output.stat().st_mtime_ns
        with patch("waveform.extract", side_effect=AssertionError("must reuse")):
            self.assertEqual(generate_preview_waveform(self.mv), output)
        self.assertEqual(before, output.stat().st_mtime_ns)

    def test_refresh_failure_preserves_existing_waveform_and_stale_source_is_reported(self):
        self.make_media()
        output = generate_preview_waveform(self.mv)
        before = output.read_bytes()
        with patch("waveform.extract", side_effect=ValueError("decode failed")):
            with self.assertRaisesRegex(ValueError, "decode failed"):
                generate_preview_waveform(self.mv, refresh=True)
        self.assertEqual(output.read_bytes(), before)
        stamp = output.stat().st_mtime_ns + 2_000_000_000
        os.utime(self.source, ns=(stamp, stamp))
        with self.assertRaisesRegex(ValueError, "--refresh"):
            generate_preview_waveform(self.mv)
        self.assertEqual(output.read_bytes(), before)

    def test_silence_is_valid(self):
        self.make_media(silent=True)
        output = generate_preview_waveform(self.mv)
        data = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(data["peaks"], [0] * 240)

    def test_public_cli_generates_sidecar_in_bound_runtime(self):
        self.make_media()
        cli = Path(__file__).resolve().parents[1] / "scripts/mv_pipeline.py"
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(cli), "waveform", "--mv-dir", str(self.mv)],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        validate_waveform(self.source.with_suffix(".waveform.json"), 3)

    def test_video_without_audio_does_not_create_waveform(self):
        self.make_media(no_audio=True)
        with self.assertRaisesRegex(ValueError, "没有音轨"):
            generate_preview_waveform(self.mv)
        self.assertFalse(self.source.with_suffix(".waveform.json").exists())


if __name__ == "__main__":
    unittest.main()

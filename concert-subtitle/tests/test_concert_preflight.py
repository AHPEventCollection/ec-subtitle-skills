from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from concert_preflight import (  # noqa: E402
    _longest_common_run,
    assess_source_seek,
    media_duration,
    probe_media,
    run_preflight,
    transcode_preview_proxy,
)
from common import file_fingerprint, write_json  # noqa: E402
from index_concert import create_manifest  # noqa: E402


class SeekAssessmentTests(unittest.TestCase):
    def test_common_run_allows_one_frame_seek_rounding_difference(self) -> None:
        self.assertEqual(
            3,
            _longest_common_run(
                ["a", "b", "c", "d"],
                ["x", "b", "c", "d"],
            ),
        )

    @patch("concert_preflight._frame_hashes")
    def test_seek_assessment_requires_matching_continuous_frames(self, hashes) -> None:
        hashes.side_effect = [
            ["a", "b", "c"],
            ["a", "b", "c"],
            ["d", "e", "f"],
            ["x", "y", "z"],
            ["g", "h", "i"],
            ["g", "h", "i"],
        ]
        result = assess_source_seek(Path("ffmpeg"), Path("source.mp4"), 100.0)
        self.assertEqual("failed", result["status"])
        self.assertEqual("failed", result["checks"][1]["status"])


class PreflightRoutingTests(unittest.TestCase):
    @patch("concert_preflight.write_json")
    @patch("concert_preflight.subprocess.run")
    @patch("concert_preflight.transcode_preview_proxy")
    @patch("concert_preflight.assess_source_seek")
    @patch("concert_preflight.probe_media")
    @patch("concert_preflight.resolve_binary")
    def test_failed_source_seek_builds_and_verifies_preview_proxy(
        self,
        resolve_binary,
        probe_media,
        assess_source_seek,
        transcode_preview_proxy,
        subprocess_run,
        write_json,
    ) -> None:
        del subprocess_run, write_json
        probe = {
            "format": {"duration": "120.0"},
            "streams": [
                {"codec_type": "video", "index": 0},
                {"codec_type": "audio", "index": 1},
            ],
        }
        probe_media.return_value = probe
        resolve_binary.side_effect = [Path("ffmpeg"), Path("ffprobe")]
        assess_source_seek.side_effect = [
            {"status": "failed", "checks": []},
            {"status": "passed", "checks": []},
        ]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"source")

            def create_proxy(_ffmpeg, _source, output_path):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"proxy")

            transcode_preview_proxy.side_effect = create_proxy
            report = run_preflight(source, root / "concert")

        self.assertEqual("transcoded_preview", report["review_media_kind"])
        self.assertEqual("passed", report["review_media_seek_check"]["status"])
        transcode_preview_proxy.assert_called_once()

    def test_manifest_binds_verified_preview_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            concert = root / "concert"
            source = root / "source.mp4"
            source.write_bytes(b"source")
            audio = root / "audio.flac"
            audio.write_bytes(b"audio")
            proxy = concert / "run" / "runtime" / "source-preview.mp4"
            proxy.parent.mkdir(parents=True)
            proxy.write_bytes(b"proxy")
            sections = root / "sections.json"
            write_json(
                sections,
                {
                    "sections": [
                        {
                            "id": "mc_01",
                            "type": "mc",
                            "start": 1.0,
                            "end": 9.0,
                        }
                    ]
                },
            )
            write_json(
                concert / "run" / "evidence" / "preflight.json",
                {
                    "review_media": str(proxy),
                    "review_media_kind": "transcoded_preview",
                    "review_media_fingerprint": file_fingerprint(proxy),
                },
            )

            manifest = create_manifest(
                concert,
                source,
                audio,
                sections,
                duration=10.0,
            )

        self.assertEqual(
            str(Path("run") / "runtime" / "source-preview.mp4"),
            manifest["review_media"],
        )
        self.assertEqual("transcoded_preview", manifest["review_media_kind"])


@unittest.skipUnless(
    os.environ.get("CONCERT_SUBTITLE_MEDIA_SMOKE") == "1",
    "需要显式启用真实媒体烟雾测试",
)
class MediaSmokeTests(unittest.TestCase):
    def test_normal_long_gop_source_does_not_trigger_preview_transcode(self) -> None:
        ffmpeg = Path(os.environ["CONCERT_SUBTITLE_FFMPEG"])
        ffprobe = Path(os.environ["CONCERT_SUBTITLE_FFPROBE"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source-long-gop.mp4"
            subprocess.run(
                [
                    str(ffmpeg),
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=duration=12:size=320x180:rate=30",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=12",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-g",
                    "150",
                    "-keyint_min",
                    "150",
                    "-sc_threshold",
                    "0",
                    "-c:a",
                    "aac",
                    str(source),
                ],
                check=True,
            )
            report = run_preflight(
                source,
                root / "concert",
                str(ffmpeg),
                str(ffprobe),
            )
            proxy = root / "proxy.mp4"
            transcode_preview_proxy(ffmpeg, source, proxy)
            proxy_duration = media_duration(probe_media(ffprobe, proxy))
            proxy_check = assess_source_seek(ffmpeg, proxy, proxy_duration)

        self.assertEqual("passed", report["source_seek_check"]["status"])
        self.assertEqual("source", report["review_media_kind"])
        self.assertLessEqual(abs(proxy_duration - 12.0), 0.5)
        self.assertEqual("passed", proxy_check["status"])


if __name__ == "__main__":
    unittest.main()

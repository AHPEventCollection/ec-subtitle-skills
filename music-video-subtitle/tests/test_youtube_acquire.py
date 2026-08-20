from __future__ import annotations

# ruff: noqa: E402
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from youtube_acquire import acquire_source


class YouTubeAcquireTests(unittest.TestCase):
    def _state(self, root: Path) -> dict[str, object]:
        return {
            "ready": True,
            "issues": [],
            "runtime_root": str(root / "runtime"),
            "profiles": {"base": {"python": str(root / "base-python.exe")}},
            "executables": {
                "ffmpeg": str(root / "runtime" / "bin" / "ffmpeg.exe"),
                "ffprobe": str(root / "runtime" / "bin" / "ffprobe.exe"),
                "javascript": str(root / "node.exe"),
            },
            "javascript": {"kind": "node", "executable": str(root / "node.exe")},
        }

    @staticmethod
    def _success(command: list[str]) -> subprocess.CompletedProcess[str]:
        home = Path(next(value.removeprefix("home:") for value in command if value.startswith("home:")))
        media = home / "source.mkv"
        media.write_bytes(b"media")
        (home / "source.ja.vtt").write_text("WEBVTT\n", encoding="utf-8")
        stdout = "\n".join(
            [
                "__MV_TITLE__=测试MV",
                "__MV_ARTIST__=测试频道",
                "__MV_URL__=https://www.youtube.com/watch?v=fixture",
                f"__MV_FILE__={media.resolve()}",
            ]
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def test_uses_bound_runtime_and_moves_staged_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            captured: list[str] = []

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                captured.extend(command)
                return self._success(command)

            with (
                patch("youtube_acquire.doctor_state", return_value=self._state(root)),
                patch("youtube_acquire.subprocess.run", side_effect=fake_run),
            ):
                result = acquire_source(mv_dir, "https://www.youtube.com/watch?v=fixture")

            self.assertTrue(result.media.is_file())
            self.assertTrue((mv_dir / "source" / "source.ja.vtt").is_file())
            self.assertFalse((mv_dir / "work" / "youtube-acquire").exists())
            self.assertEqual([str(root / "base-python.exe"), "-m", "yt_dlp"], captured[:3])
            self.assertIn("--ignore-config", captured)
            self.assertIn("--ffmpeg-location", captured)
            self.assertIn("--js-runtimes", captured)
            self.assertIn(f"node:{root / 'node.exe'}", captured)
            evidence = json.loads(result.evidence.read_text(encoding="utf-8"))
            self.assertEqual("upstream-default", evidence["selected_strategy"])

    def test_http_403_uses_one_embedded_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            commands: list[list[str]] = []

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                if len(commands) == 1:
                    return subprocess.CompletedProcess(command, 1, "", "ERROR: HTTP Error 403: Forbidden")
                return self._success(command)

            with (
                patch("youtube_acquire.doctor_state", return_value=self._state(root)),
                patch("youtube_acquire.subprocess.run", side_effect=fake_run),
            ):
                result = acquire_source(mv_dir, "https://www.youtube.com/watch?v=fixture")

            self.assertEqual(2, len(commands))
            self.assertIn("youtube:player_client=default,web_embedded", commands[1])
            self.assertEqual("youtube-embedded-fallback", result.strategy)

    def test_http_429_stops_without_client_fanout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            command_count = 0

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                nonlocal command_count
                command_count += 1
                return subprocess.CompletedProcess(command, 1, "", "ERROR: HTTP Error 429: Too Many Requests")

            with (
                patch("youtube_acquire.doctor_state", return_value=self._state(root)),
                patch("youtube_acquire.subprocess.run", side_effect=fake_run),
                self.assertRaisesRegex(RuntimeError, "rate-limited"),
            ):
                acquire_source(mv_dir, "https://www.youtube.com/watch?v=fixture")

            self.assertEqual(1, command_count)
            self.assertFalse((mv_dir / "work" / "youtube-acquire").exists())


if __name__ == "__main__":
    unittest.main()

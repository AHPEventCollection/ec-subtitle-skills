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

from youtube_acquire import _global_downloader_command, _run_download, acquire_source


class YouTubeAcquireTests(unittest.TestCase):
    def test_global_downloader_uses_powershell_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wrapper = root / "ytdlp-global.ps1"
            wrapper.touch()
            with (
                patch(
                    "youtube_acquire.shutil.which",
                    return_value=r"C:\Program Files\PowerShell\7\pwsh.exe",
                ),
                patch.dict("youtube_acquire.os.environ", {"PATH": str(root)}),
            ):
                self.assertEqual(
                    [
                        r"C:\Program Files\PowerShell\7\pwsh.exe",
                        "-NoProfile",
                        "-File",
                        str(wrapper.resolve()),
                    ],
                    _global_downloader_command(),
                )

    @staticmethod
    def _wrapper(root: Path) -> list[str]:
        return [str(root / "pwsh.exe"), "-NoProfile", "-File", str(root / "ytdlp-global.ps1")]

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
        home = Path(command[command.index("--output") + 1]).parent
        media = home / "source.mkv"
        media.write_bytes(b"media")
        (home / "source.ja.vtt").write_text("WEBVTT\n", encoding="utf-8")
        stdout = "\n".join(
            [
                "__YTDLP_GLOBAL_ID__=fixture",
                "__YTDLP_GLOBAL_TITLE__=测试MV",
                "__YTDLP_GLOBAL_CHANNEL__=测试频道",
                f"__YTDLP_GLOBAL_FILE__={media.resolve()}",
                json.dumps(
                    {
                        "status": "verified",
                        "operation": "download",
                        "authentication": "anonymous",
                        "media": {"streams": [{"codec_type": "audio", "codec_name": "aac"}]},
                    }
                ),
            ]
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def test_uses_bound_runtime_and_moves_staged_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            captured: list[str] = []

            def fake_run(
                command: list[str], **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                captured.extend(command)
                return self._success(command)

            with (
                patch("youtube_acquire.doctor_state", return_value=self._state(root)),
                patch(
                    "youtube_acquire._global_downloader_command",
                    return_value=self._wrapper(root),
                ),
                patch("youtube_acquire._run_download", side_effect=fake_run),
            ):
                result = acquire_source(
                    mv_dir, "https://www.youtube.com/watch?v=fixture"
                )

            self.assertTrue(result.media.is_file())
            self.assertTrue((mv_dir / "source" / "source.ja.vtt").is_file())
            self.assertFalse((mv_dir / "work" / "youtube-acquire").exists())
            self.assertEqual(
                self._wrapper(root), captured[:4]
            )
            self.assertIn("--auth", captured)
            self.assertEqual("auto", captured[captured.index("--auth") + 1])
            self.assertIn("--prefer-sdr", captured)
            self.assertIn("--require-vod", captured)
            self.assertIn("--stall-timeout", captured)
            self.assertIn("--write-thumbnail", captured)
            self.assertIn("--write-subs", captured)
            self.assertNotIn("--cookies-from-browser", captured)
            evidence = json.loads(result.evidence.read_text(encoding="utf-8"))
            self.assertEqual("ytdlp-global-auto", evidence["selected_strategy"])
            self.assertEqual("anonymous", evidence["authentication"])

    def test_empty_manual_subtitle_does_not_discard_downloaded_media(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"

            commands: list[list[str]] = []

            def fake_run(
                command: list[str], **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                if len(commands) == 1:
                    return subprocess.CompletedProcess(
                        command,
                        1,
                        "",
                        "ERROR: Did not get any data blocks\n"
                        'WARNING: File "work/source.ja.vtt" cannot be found',
                    )
                completed = self._success(command)
                home = Path(command[command.index("--output") + 1]).parent
                (home / "source.ja.vtt").unlink()
                return completed

            with (
                patch("youtube_acquire.doctor_state", return_value=self._state(root)),
                patch(
                    "youtube_acquire._global_downloader_command",
                    return_value=self._wrapper(root),
                ),
                patch("youtube_acquire._run_download", side_effect=fake_run),
            ):
                result = acquire_source(
                    mv_dir, "https://www.youtube.com/watch?v=fixture"
                )

            self.assertTrue(result.media.is_file())
            self.assertEqual(2, len(commands))
            self.assertIn("--write-subs", commands[0])
            self.assertNotIn("--write-subs", commands[1])
            self.assertFalse((mv_dir / "source" / "source.ja.vtt").exists())
            evidence = json.loads(result.evidence.read_text(encoding="utf-8"))
            self.assertEqual("download-failed", evidence["subtitle_status"])
            self.assertEqual("empty-data", evidence["subtitle_error"])
            self.assertEqual("ytdlp-global-auto-media-only", result.strategy)

    def test_http_403_stops_without_project_client_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            commands: list[list[str]] = []

            def fake_run(
                command: list[str], **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                return subprocess.CompletedProcess(
                    command, 1, "", "ERROR: HTTP Error 403: Forbidden"
                )

            with (
                patch("youtube_acquire.doctor_state", return_value=self._state(root)),
                patch(
                    "youtube_acquire._global_downloader_command",
                    return_value=self._wrapper(root),
                ),
                patch("youtube_acquire._run_download", side_effect=fake_run),
                self.assertRaisesRegex(RuntimeError, "client-or-challenge"),
            ):
                acquire_source(
                    mv_dir, "https://www.youtube.com/watch?v=fixture"
                )

            self.assertEqual(1, len(commands))

    def test_http_429_stops_without_client_fanout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            command_count = 0

            def fake_run(
                command: list[str], **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                nonlocal command_count
                command_count += 1
                return subprocess.CompletedProcess(
                    command, 1, "", "ERROR: HTTP Error 429: Too Many Requests"
                )

            with (
                patch("youtube_acquire.doctor_state", return_value=self._state(root)),
                patch(
                    "youtube_acquire._global_downloader_command",
                    return_value=self._wrapper(root),
                ),
                patch("youtube_acquire._run_download", side_effect=fake_run),
                self.assertRaisesRegex(RuntimeError, "rate-limited"),
            ):
                acquire_source(mv_dir, "https://www.youtube.com/watch?v=fixture")

            self.assertEqual(1, command_count)
            self.assertFalse((mv_dir / "work" / "youtube-acquire").exists())

    def test_project_has_no_isolated_auth_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            captured: list[str] = []

            def fake_run(
                command: list[str], **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                captured.extend(command)
                return self._success(command)

            with (
                patch("youtube_acquire.doctor_state", return_value=self._state(root)),
                patch(
                    "youtube_acquire._global_downloader_command",
                    return_value=self._wrapper(root),
                ),
                patch("youtube_acquire._run_download", side_effect=fake_run),
            ):
                result = acquire_source(
                    mv_dir,
                    "https://www.youtube.com/watch?v=fixture",
                )

            self.assertEqual("ytdlp-global-auto", result.strategy)
            self.assertNotIn("--cookies-from-browser", captured)
            self.assertNotIn("--extractor-args", captured)

    def test_premiere_wait_reuses_same_command_and_records_wait(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = ["wrapper", "download"]
            waiting = subprocess.CompletedProcess(command, 27, json.dumps({
                "operation": "download", "status": "video-not-ready",
                "authentication": "anonymous",
                        "media": {"streams": [{"codec_type": "audio", "codec_name": "aac"}]},
            }), "")
            ready = subprocess.CompletedProcess(command, 0, "ready", "")
            audit = {"attempts": []}
            with (
                patch("youtube_acquire._stream_download", side_effect=[waiting, ready]) as stream,
                patch("youtube_acquire.time.sleep") as sleep,
            ):
                result = _run_download(command, env={}, audit=audit, evidence=root / "audit.json")
            self.assertIs(result, ready)
            self.assertEqual(stream.call_count, 2)
            self.assertEqual(stream.call_args_list[0], stream.call_args_list[1])
            sleep.assert_called_once_with(30)
            self.assertEqual(audit["attempts"][0]["error_class"], "video-not-ready")

    def test_stall_does_not_retry_authentication_or_premiere_wait(self) -> None:
        result = subprocess.CompletedProcess([], 28, json.dumps({
            "operation": "download", "status": "download-stalled",
        }), "")
        with (
            patch("youtube_acquire._stream_download", return_value=result) as stream,
            patch("youtube_acquire.time.sleep") as sleep,
        ):
            actual = _run_download([], env={}, audit={"attempts": []}, evidence=Path("unused"))
        self.assertIs(actual, result)
        stream.assert_called_once()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()

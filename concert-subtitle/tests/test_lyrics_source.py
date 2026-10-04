from __future__ import annotations

# ruff: noqa: E402

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from common import read_json
from build_work_packages import build_packages
from index_concert import create_manifest
from lyrics_source import fetch_one


class LyricsSourceTests(unittest.TestCase):
    def test_fetch_one_prefers_netease_bilingual_before_lrclib(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary)
            with (
                patch("lyrics_source.lrclib_search") as lrclib_search,
                patch(
                    "lyrics_source.netease_search",
                    return_value=[
                        {
                            "id": 2,
                            "name": "Song",
                            "artists": [{"name": "Artist"}],
                        }
                    ],
                ),
                patch(
                    "lyrics_source.netease_lyrics",
                    return_value={
                        "lrc": {"lyric": "[00:01.00]原文"},
                        "tlyric": {"lyric": "[00:01.00]译文"},
                    },
                ),
                patch("lyrics_source.read_netease_cookie", return_value="cookie"),
            ):
                result = fetch_one("Song", "Artist", out_dir)

            lrclib_search.assert_not_called()
            self.assertEqual("netease", result["source"])
            self.assertEqual("found", result["translation_status"])
            self.assertEqual(
                "[00:01.00]原文\n",
                (out_dir / "Song.lrc").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "[00:01.00]译文\n",
                (out_dir / "Song.zh.lrc").read_text(encoding="utf-8"),
            )

    def test_fetch_one_checks_next_matching_netease_candidate(self) -> None:
        songs = [
            {
                "id": 1,
                "name": "Song",
                "artists": [{"name": "Artist"}],
                "popularity": 100,
            },
            {
                "id": 2,
                "name": "Song",
                "artists": [{"name": "Artist"}],
                "popularity": 90,
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("lyrics_source.lrclib_search") as lrclib_search,
                patch("lyrics_source.netease_search", return_value=songs),
                patch(
                    "lyrics_source.netease_lyrics",
                    side_effect=[
                        {"lrc": {"lyric": "[00:01.00]只有原文"}},
                        {
                            "lrc": {"lyric": "[00:01.00]双语原文"},
                            "tlyric": {"lyric": "[00:01.00]双语译文"},
                        },
                    ],
                ) as netease_lyrics,
                patch("lyrics_source.read_netease_cookie", return_value="cookie"),
            ):
                result = fetch_one("Song", "Artist", Path(temporary))

            lrclib_search.assert_not_called()
            self.assertEqual(2, netease_lyrics.call_count)
            self.assertEqual(2, result["metadata"]["hit"]["id"])
            self.assertEqual("found", result["translation_status"])

    def test_fetch_one_keeps_netease_original_without_translation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("lyrics_source.lrclib_search") as lrclib_search,
                patch(
                    "lyrics_source.netease_search",
                    return_value=[
                        {
                            "id": 2,
                            "name": "Song",
                            "artists": [{"name": "Artist"}],
                        }
                    ],
                ),
                patch(
                    "lyrics_source.netease_lyrics",
                    return_value={"lrc": {"lyric": "[00:01.00]原文"}},
                ),
                patch("lyrics_source.read_netease_cookie", return_value="cookie"),
            ):
                result = fetch_one("Song", "Artist", Path(temporary))

            lrclib_search.assert_not_called()
            self.assertEqual("netease", result["source"])
            self.assertEqual("not_found", result["translation_status"])
            self.assertIsNone(result["translation_file"])

    def test_fetch_one_uses_lrclib_only_when_netease_has_no_synced_original(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch(
                    "lyrics_source.netease_search",
                    return_value=[
                        {
                            "id": 2,
                            "name": "Song",
                            "artists": [{"name": "Artist"}],
                        }
                    ],
                ),
                patch(
                    "lyrics_source.netease_lyrics",
                    return_value={"lrc": {"lyric": "无时间原文"}},
                ),
                patch(
                    "lyrics_source.lrclib_search",
                    return_value=[
                        {
                            "id": 1,
                            "trackName": "Song",
                            "artistName": "Artist",
                            "plainLyrics": "LRCLIB原文",
                            "syncedLyrics": None,
                        }
                    ],
                ),
                patch("lyrics_source.read_netease_cookie", return_value="cookie"),
            ):
                result = fetch_one("Song", "Artist", Path(temporary))

            self.assertEqual("lrclib", result["source"])
            self.assertEqual("txt", result["file_type"])


class ManifestLyricsAcquisitionTests(unittest.TestCase):
    @staticmethod
    def _base_files(root: Path) -> tuple[Path, Path, Path, Path]:
        concert_dir = root / "concert"
        source = root / "source.mkv"
        audio = root / "concert.flac"
        sections = root / "sections.json"
        source.write_bytes(b"source")
        audio.write_bytes(b"audio")
        return concert_dir, source, audio, sections

    def test_manifest_auto_fetches_missing_song_lyrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            concert_dir, source, audio, sections = self._base_files(root)
            sections.write_text(
                json.dumps(
                    {
                        "sections": [
                            {
                                "id": "song_01",
                                "type": "song",
                                "title": "Song",
                                "artist": "Artist",
                                "start": 0,
                                "end": 10,
                            },
                            {
                                "id": "mc_01",
                                "type": "mc",
                                "title": "MC",
                                "start": 10,
                                "end": 20,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            def fake_fetch(
                title: str,
                artist: str,
                out_dir: Path,
                *,
                cookie_file: Path | None,
            ) -> dict:
                self.assertEqual(("Song", "Artist", None), (title, artist, cookie_file))
                out_dir.mkdir(parents=True)
                lyrics = out_dir / "Song.lrc"
                translation = out_dir / "Song.zh.lrc"
                lyrics.write_text("[00:01.00]原文\n", encoding="utf-8")
                translation.write_text("[00:01.00]译文\n", encoding="utf-8")
                return {
                    "status": "found",
                    "source": "netease",
                    "file": str(lyrics),
                    "translation_status": "found",
                    "translation_source": "netease",
                    "translation_file": str(translation),
                    "metadata": {"hit": {"id": 123}},
                }

            with patch("index_concert.fetch_one", side_effect=fake_fetch) as fetch:
                manifest = create_manifest(
                    concert_dir,
                    source,
                    audio,
                    sections,
                    duration=20,
                )

            self.assertEqual(1, fetch.call_count)
            song = manifest["sections"][0]
            mc = manifest["sections"][1]
            self.assertEqual("input\\known-lyrics\\Song.lrc", song["lyrics_path"])
            self.assertEqual(
                "input\\known-lyrics\\Song.zh.lrc",
                song["translation_path"],
            )
            self.assertEqual("netease", song["lyrics_source"])
            self.assertEqual("found", song["lyrics_status"])
            self.assertEqual("found", song["translation_status"])
            self.assertEqual("missing_lyrics_path", song["lyrics_lookup"]["reason"])
            self.assertIsNone(mc["lyrics_path"])
            self.assertEqual(manifest, read_json(concert_dir / "run" / "concert_manifest.json"))

            build_packages(concert_dir, no_audio=True)
            package = read_json(
                concert_dir / "run" / "sections" / "song_01" / "input.json"
            )
            self.assertEqual("lyrics.lrc", package["lyrics_file"])
            self.assertEqual("translation.lrc", package["translation_file"])
            self.assertEqual("netease", package["lyrics_source"])
            self.assertEqual("netease", package["translation_source"])
            self.assertEqual("found", package["lyrics_status"])
            self.assertEqual("found", package["translation_status"])

    def test_manifest_preserves_provided_lyrics_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            concert_dir, source, audio, sections = self._base_files(root)
            lyrics = root / "provided.lrc"
            translation = root / "provided.zh.lrc"
            lyrics.write_text("[00:01.00]用户原词\n", encoding="utf-8")
            translation.write_text("[00:01.00]用户译文\n", encoding="utf-8")
            sections.write_text(
                json.dumps(
                    {
                        "sections": [
                            {
                                "id": "song_01",
                                "type": "song",
                                "title": "Song",
                                "artist": "Artist",
                                "start": 0,
                                "end": 10,
                                "lyrics_path": "provided.lrc",
                                "lyrics_source": "user",
                                "translation_path": "provided.zh.lrc",
                                "translation_source": "user",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with patch("index_concert.fetch_one") as fetch:
                manifest = create_manifest(
                    concert_dir,
                    source,
                    audio,
                    sections,
                    duration=10,
                )

            fetch.assert_not_called()
            song = manifest["sections"][0]
            self.assertEqual(str(lyrics.resolve()), song["lyrics_path"])
            self.assertEqual("user", song["lyrics_source"])
            self.assertEqual("provided", song["lyrics_status"])
            self.assertEqual(str(translation.resolve()), song["translation_path"])
            self.assertEqual("user", song["translation_source"])
            self.assertEqual("provided", song["translation_status"])
            self.assertEqual("[00:01.00]用户原词\n", lyrics.read_text(encoding="utf-8"))
            self.assertEqual(
                "[00:01.00]用户译文\n",
                translation.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()

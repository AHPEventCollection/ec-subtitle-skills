from __future__ import annotations

# ruff: noqa: E402

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from assess_mc_complexity import assess_section
from build_work_packages import _extract_audio, build_packages
from build_review_clips import build_review_clips, render_review_video
from common import (
    SCHEMA_VERSION,
    canonical_hash,
    load_lyrics,
    find_project_root,
    manifest_section_contract,
    mc_has_disallowed_punctuation,
    normalize_mc_punctuation,
    read_json,
    package_input_hash,
    validate_filename_component,
    write_json,
)
from gpu_queue import (
    _unlink_with_retry,
    queue_status,
    run_queued,
    shared_queue_directory,
)
from merge_concert_subtitles import merge_concert, render_ass
from reflow_mc_events import reflow_events, reflow_section
from review_section import (
    CROWDED_MC_REVIEW_POINTS,
    REQUIRED_POINTS,
    record_review,
)
from run_section import run_section
from validate_concert import validate_concert
from validate_section import validate_section


def write_valid_song(section_dir: Path) -> None:
    section_dir.mkdir(parents=True)
    (section_dir / "lyrics.txt").write_text("一行目\n二行目\n", encoding="utf-8")
    package = {
        "schema_version": SCHEMA_VERSION,
        "section_id": "song_01",
        "section_type": "song",
        "section_start": 10.0,
        "section_end": 30.0,
        "context_start": 0.0,
        "context_end": 40.0,
        "timeline_origin": 10.0,
        "audio_context_origin": 0.0,
        "audio_path": "audio.flac",
        "lyrics_file": "lyrics.txt",
        "boundary_confidence": 0.9,
        "review_priority": "normal",
        "gpu_required": True,
    }
    package["input_hash"] = canonical_hash(package)
    write_json(section_dir / "input.json", package)
    write_json(
        section_dir / "events.json",
        {
            "schema_version": SCHEMA_VERSION,
            "section_id": "song_01",
            "events": [
                {
                    "id": "song_01_0001",
                    "start": 1.0,
                    "end": 3.0,
                    "source_text": "一行目",
                    "translation": "第一句",
                },
                {
                    "id": "song_01_0002",
                    "start": 4.0,
                    "end": 7.0,
                    "source_text": "二行目",
                    "translation": "第二句",
                },
            ],
        },
    )
    write_json(
        section_dir / "report.json",
        {
            "first_vocal_start": 1.1,
            "alignment_slope": 1.0,
            "max_correction_seconds": 0.2,
            "max_extrapolation_seconds": 0.0,
            "first_reliable_anchor_ratio": 0.05,
            "unsafe_reference_extrapolation": False,
            "instrumental_gaps": [{"start": 8.0, "end": 10.0, "confidence": 0.9}],
        },
    )


def convert_to_valid_mc(section_dir: Path) -> None:
    package = read_json(section_dir / "input.json")
    package["section_id"] = "mc_01"
    package["section_type"] = "mc"
    package["lyrics_file"] = None
    package["input_hash"] = package_input_hash(package)
    write_json(section_dir / "input.json", package)
    events = read_json(section_dir / "events.json")
    events["section_id"] = "mc_01"
    write_json(section_dir / "events.json", events)


def write_mc_assessment(
    section_dir: Path,
    *,
    classification: str = "single_speaker",
    speaker_count: int = 1,
    signals: set[str] | None = None,
    evidence_basis: set[str] | None = None,
) -> dict:
    return assess_section(
        section_dir,
        classification,
        speaker_count,
        signals or set(),
        evidence_basis or {"audio_review"},
        "",
        "main",
    )


def bind_package_to_manifest(concert_dir: Path, section_dir: Path) -> None:
    manifest = read_json(concert_dir / "run" / "concert_manifest.json")
    section_index = next(
        index
        for index, section in enumerate(manifest["sections"])
        if section["id"] == section_dir.name
    )
    section = manifest["sections"][section_index]
    package = read_json(section_dir / "input.json")
    package.update(
        {
            "concert_id": manifest["concert_id"],
            "section_id": section["id"],
            "section_type": section["type"],
            "order": section["order"],
            "title": section.get("title", ""),
            "artist": section.get("artist", ""),
            "section_start": float(section["start"]),
            "section_end": float(section["end"]),
            "context_start": float(section["context_start"]),
            "context_end": float(section["context_end"]),
            "timeline_origin": float(section["start"]),
            "audio_context_origin": float(section["context_start"]),
            "boundary_source": section.get("boundary_source", ""),
            "boundary_confidence": float(section.get("boundary_confidence", 0.5)),
            "review_priority": section.get("review_priority", "normal"),
            "gpu_required": bool(section.get("gpu_required", True)),
        }
    )
    package["manifest_section_hash"] = canonical_hash(
        manifest_section_contract(manifest, section_index)
    )
    package_without_hash = {
        key: value
        for key, value in package.items()
        if key != "input_hash"
    }
    package["input_hash"] = canonical_hash(package_without_hash)
    write_json(section_dir / "input.json", package)


class ProjectRoutingTests(unittest.TestCase):
    def test_configured_project_root_precedes_repository_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configured = Path(temporary) / "concert-project"
            with patch.dict(
                os.environ, {"CONCERT_SUBTITLE_PROJECT_ROOT": str(configured)}, clear=False
            ):
                self.assertEqual(configured.resolve(), find_project_root())

    def test_current_project_root_does_not_depend_on_skill_install_location(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "concert-project"
            (project / "tool").mkdir(parents=True)
            (project / "workspace").mkdir()
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("common.Path.cwd", return_value=project),
            ):
                self.assertEqual(project.absolute(), find_project_root())


class SchemaTests(unittest.TestCase):
    def test_all_schemas_are_json(self) -> None:
        schemas = list((SKILL_ROOT / "schemas").glob("*.json"))
        self.assertEqual(5, len(schemas))
        for path in schemas:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("https://json-schema.org/draft/2020-12/schema", data["$schema"])

    def test_whisper_profile_declares_pykakasi(self) -> None:
        runtime = json.loads(
            (SKILL_ROOT / "runtime-requirements.json").read_text(encoding="utf-8")
        )
        requirements = (SKILL_ROOT / "requirements-whisper.txt").read_text(encoding="utf-8")
        self.assertIn("pykakasi", runtime["profiles"]["whisper"]["imports"])
        self.assertIn("pykakasi==2.3.0", requirements)


class ReviewClipTests(unittest.TestCase):
    @patch("build_review_clips.render_review_video")
    @patch("build_review_clips.resolve_binary", return_value=Path("ffmpeg"))
    def test_review_video_and_ass_share_directory_and_stem(
        self,
        _resolve_binary,
        render_video,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            concert_dir = Path(temporary)
            source = concert_dir / "source.mkv"
            source.write_bytes(b"source")
            section_dir = concert_dir / "run" / "sections" / "song_01"
            write_valid_song(section_dir)
            manifest = {
                "source_media": str(source),
                "schema_version": "0.1.0",
                "duration_seconds": 30.0,
                "sections": [
                    {
                        "id": "song_01",
                        "type": "song",
                        "start": 5.0,
                        "end": 15.0,
                    }
                ],
            }
            write_json(concert_dir / "run" / "concert_manifest.json", manifest)
            events = [
                {
                    "start": 1.0,
                    "end": 2.0,
                    "source_text": "歌詞",
                    "translation": "歌词",
                }
            ]
            with (
                patch("build_review_clips.load_manifest", return_value=manifest),
                patch("build_review_clips.section_directory", return_value=section_dir),
                patch("build_review_clips.load_events", return_value=events),
            ):
                summaries = build_review_clips(concert_dir)

            self.assertEqual(1, len(render_video.call_args_list))
            self.assertEqual("full_section", summaries[0]["review_point"])
            self.assertEqual(5.0, render_video.call_args.kwargs["start"])
            self.assertEqual(15.0, render_video.call_args.kwargs["end"])
            self.assertFalse((section_dir / "structure.tsv").exists())
            self.assertIn("0:00:01.00,0:00:02.00", Path(summaries[0]["subtitle"]).read_text(encoding="utf-8-sig"))
            for call in render_video.call_args_list:
                video = call.kwargs["output_path"]
                subtitle = video.with_suffix(".ass")
                self.assertTrue(subtitle.is_file())
                self.assertEqual(video.parent, subtitle.parent)
                self.assertEqual(video.stem, subtitle.stem)

    @patch("build_review_clips.render_review_video")
    @patch("build_review_clips.resolve_binary", return_value=Path("ffmpeg"))
    def test_mc_review_is_one_full_section_pair(
        self,
        _resolve_binary,
        render_video,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            concert_dir = Path(temporary)
            source = concert_dir / "source.mkv"
            source.write_bytes(b"source")
            section_dir = concert_dir / "run" / "sections" / "mc_01"
            section_dir.mkdir(parents=True)
            manifest = {
                "source_media": str(source),
                "duration_seconds": 30.0,
                "sections": [
                    {
                        "id": "mc_01",
                        "type": "mc",
                        "start": 5.0,
                        "end": 15.0,
                    }
                ],
            }
            events = [
                {
                    "start": 1.0,
                    "end": 2.0,
                    "role": "speech",
                    "source_text": "会話",
                    "translation": "对话",
                }
            ]
            with (
                patch("build_review_clips.load_manifest", return_value=manifest),
                patch("build_review_clips.section_directory", return_value=section_dir),
                patch("build_review_clips.load_events", return_value=events),
            ):
                summaries = build_review_clips(concert_dir)

            self.assertEqual(1, len(render_video.call_args_list))
            self.assertEqual("full_section", summaries[0]["review_point"])
            self.assertEqual(5.0, render_video.call_args.kwargs["start"])
            self.assertEqual(15.0, render_video.call_args.kwargs["end"])

    def test_repeated_lyrics_do_not_shorten_full_song_review(self) -> None:
        from build_review_clips import _section_review_window

        section = {"id": "song_01", "type": "song", "start": 10.0, "end": 80.0}
        events = [
            {"start": 2.0, "end": 4.0, "source_text": "A", "role": "lyric"},
            {"start": 4.2, "end": 6.0, "source_text": "B", "role": "lyric"},
            {"start": 6.2, "end": 8.0, "source_text": "C", "role": "lyric"},
            {"start": 20.0, "end": 22.0, "source_text": "A", "role": "lyric"},
            {"start": 22.2, "end": 24.0, "source_text": "B", "role": "lyric"},
            {"start": 24.2, "end": 26.0, "source_text": "C", "role": "lyric"},
        ]

        self.assertEqual(("full_section", 10.0, 80.0), _section_review_window(section, events))

    @patch("build_review_clips.subprocess.run")
    def test_review_video_has_no_embedded_subtitle(self, run) -> None:
        render_review_video(
            source=Path("source.mp4"),
            output_path=Path("review.mp4"),
            start=1.0,
            end=3.0,
            ffmpeg=Path("ffmpeg"),
        )
        command = run.call_args.args[0]
        video_filter = command[command.index("-vf") + 1]
        self.assertTrue(video_filter.endswith("setpts=PTS-STARTPTS"))
        self.assertFalse(any("subtitles=" in part for part in command))

    @patch("build_review_clips.subprocess.run")
    def test_review_video_reencodes_and_resets_timeline(self, run) -> None:
        render_review_video(
            source=Path("source.mp4"),
            output_path=Path("review.mp4"),
            start=1.0,
            end=3.0,
            ffmpeg=Path("ffmpeg"),
        )
        command = run.call_args.args[0]
        self.assertEqual("libx264", command[command.index("-c:v") + 1])
        self.assertEqual("aac", command[command.index("-c:a") + 1])
        self.assertEqual(
            "asetpts=PTS-STARTPTS",
            command[command.index("-af") + 1],
        )
        self.assertNotIn("copy", command)

    @patch("build_work_packages.subprocess.run")
    def test_section_audio_reencodes_and_resets_timeline(self, run) -> None:
        _extract_audio(
            Path("ffmpeg"),
            Path("concert.flac"),
            Path("section.flac"),
            10.0,
            20.0,
        )
        command = run.call_args.args[0]
        self.assertEqual("flac", command[command.index("-c:a") + 1])
        self.assertEqual(
            "asetpts=PTS-STARTPTS",
            command[command.index("-af") + 1],
        )
        self.assertNotIn("copy", command)


class SectionGateTests(unittest.TestCase):
    def test_timed_lyric_credits_are_not_subtitle_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lyrics.lrc"
            path.write_text(
                "[00:00.00] 作词 : すぅ\n"
                "[00:01.00] 作曲 : クボナオキ\n"
                "[00:02.00]歌詞\n",
                encoding="utf-8",
            )
            self.assertEqual(["歌詞"], load_lyrics(path))

    def test_initial_song_title_and_artist_are_not_subtitle_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lyrics.lrc"
            path.write_text(
                "[00:00.36]ランジェリー\n"
                "[00:01.78]Silent Siren\n"
                "[00:09.54]321 GO!!!\n",
                encoding="utf-8",
            )
            self.assertEqual(
                ["321 GO!!!"],
                load_lyrics(
                    path,
                    initial_metadata=["ランジェリー", "SILENT SIREN"],
                ),
            )

    def test_valid_song_passes_automatic_gate_then_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            gate = validate_section(section_dir)
            self.assertEqual("passed", gate["automatic_result"])
            self.assertEqual("needs_review", read_json(section_dir / "status.json")["status"])

            decision = record_review(
                section_dir,
                "approve",
                REQUIRED_POINTS["song"],
                "",
                "main",
            )
            self.assertEqual("approved", decision["decision"])
            self.assertEqual("approved", read_json(section_dir / "status.json")["status"])

    def test_missing_known_lyrics_is_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            package = read_json(section_dir / "input.json")
            package["lyrics_file"] = None
            package["lyrics_hash"] = None
            package["input_hash"] = package_input_hash(package)
            write_json(section_dir / "input.json", package)

            gate = validate_section(section_dir)

            codes = {failure["code"] for failure in gate["hard_failures"]}
            self.assertIn("missing_known_lyrics", codes)

    def test_overlap_is_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            data = read_json(section_dir / "events.json")
            data["events"][1]["start"] = 2.5
            write_json(section_dir / "events.json", data)
            gate = validate_section(section_dir)
            codes = {failure["code"] for failure in gate["hard_failures"]}
            self.assertIn("overlap", codes)

    def test_unreadable_event_duration_is_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            data = read_json(section_dir / "events.json")
            data["events"][0]["end"] = 1.1
            write_json(section_dir / "events.json", data)
            gate = validate_section(section_dir)
            codes = {failure["code"] for failure in gate["hard_failures"]}
            self.assertIn("unreadable_event_duration", codes)

    def test_very_short_event_duration_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            data = read_json(section_dir / "events.json")
            data["events"][0]["end"] = 1.3
            write_json(section_dir / "events.json", data)
            gate = validate_section(section_dir)
            codes = {flag["code"] for flag in gate["review_flags"]}
            self.assertIn("very_short_event_duration", codes)
            decision = record_review(
                section_dir,
                "approve",
                REQUIRED_POINTS["song"],
                "",
                "main",
            )
            self.assertEqual("approved", decision["decision"])

    def test_empty_mc_is_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "mc_01"
            section_dir.mkdir()
            write_json(
                section_dir / "input.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "section_id": "mc_01",
                    "section_type": "mc",
                    "section_start": 0.0,
                    "section_end": 10.0,
                    "context_start": 0.0,
                    "context_end": 10.0,
                    "timeline_origin": 0.0,
                    "audio_context_origin": 0.0,
                    "audio_path": "audio.flac",
                    "lyrics_file": None,
                    "boundary_confidence": 0.9,
                    "input_hash": "b" * 64,
                },
            )
            write_json(section_dir / "events.json", {"events": []})
            write_json(section_dir / "report.json", {})
            gate = validate_section(section_dir)
            codes = {failure["code"] for failure in gate["hard_failures"]}
            self.assertIn("empty_section_events", codes)

    def test_dense_mc_event_fails_before_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "mc_01"
            write_valid_song(section_dir)
            convert_to_valid_mc(section_dir)
            write_mc_assessment(section_dir)
            data = read_json(section_dir / "events.json")
            data["events"][0]["source_text"] = "あ" * 40
            write_json(section_dir / "events.json", data)
            gate = validate_section(section_dir)
            codes = {failure["code"] for failure in gate["hard_failures"]}
            self.assertIn("overlong_mc_event", codes)

    def test_mc_requires_complexity_assessment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "mc_01"
            write_valid_song(section_dir)
            convert_to_valid_mc(section_dir)
            gate = validate_section(section_dir)
            codes = {failure["code"] for failure in gate["hard_failures"]}
            self.assertIn("missing_mc_complexity_assessment", codes)

    def test_mc_complexity_assessment_requires_audio_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "mc_01"
            write_valid_song(section_dir)
            convert_to_valid_mc(section_dir)
            with self.assertRaisesRegex(ValueError, "音频回听"):
                write_mc_assessment(
                    section_dir,
                    evidence_basis={"static_video_frames", "asr_word_timing"},
                )

    def test_mc_display_limit_is_hard_failure_before_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "mc_01"
            write_valid_song(section_dir)
            convert_to_valid_mc(section_dir)
            write_mc_assessment(section_dir)
            events = read_json(section_dir / "events.json")
            events["events"][0]["source_text"] = "長" * 29
            write_json(section_dir / "events.json", events)

            gate = validate_section(section_dir)

            failures = {
                failure["code"]: failure for failure in gate["hard_failures"]
            }
            self.assertIn("overlong_mc_event", failures)
            self.assertEqual(58, failures["overlong_mc_event"]["source_units"])
            self.assertNotIn(
                "dense_mc_event",
                {flag["code"] for flag in gate["review_flags"]},
            )

    def test_crowded_mc_requires_manual_conform_review_points(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "mc_01"
            write_valid_song(section_dir)
            convert_to_valid_mc(section_dir)
            write_mc_assessment(
                section_dir,
                classification="crowded_multi_speaker",
                speaker_count=4,
                signals={"overlapping_speech", "rapid_turn_taking"},
                evidence_basis={"continuous_av_review", "asr_word_timing"},
            )
            gate = validate_section(section_dir)
            self.assertEqual("passed", gate["automatic_result"])
            self.assertIn(
                "crowded_mc_manual_conform_required",
                {flag["code"] for flag in gate["review_flags"]},
            )
            with self.assertRaisesRegex(
                ValueError,
                "speaker_turns",
            ):
                record_review(
                    section_dir,
                    "approve",
                    REQUIRED_POINTS["mc"] | {"full_section"},
                    "",
                    "main",
                )
            decision = record_review(
                section_dir,
                "approve",
                REQUIRED_POINTS["mc"]
                | CROWDED_MC_REVIEW_POINTS
                | {"full_section"},
                "",
                "main",
            )
            self.assertEqual("approved", decision["decision"])
            self.assertIn("mc_complexity_hash", decision)

    def test_song_without_translation_is_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            events = read_json(section_dir / "events.json")
            events["events"][0]["translation"] = ""
            write_json(section_dir / "events.json", events)
            gate = validate_section(section_dir)
            codes = {failure["code"] for failure in gate["hard_failures"]}
            self.assertIn("missing_translation", codes)

    def test_live_lyric_variant_uses_canonical_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            events = read_json(section_dir / "events.json")
            events["events"][0].update(
                {
                    "source_text": "ライブ版一行目",
                    "canonical_source_text": "一行目",
                    "evidence": ["user_manual_full_av_review"],
                }
            )
            write_json(section_dir / "events.json", events)
            gate = validate_section(section_dir)
            self.assertEqual("passed", gate["automatic_result"])
            codes = {flag["code"] for flag in gate["review_flags"]}
            self.assertIn("live_lyric_variant_review_required", codes)

    def test_omitted_live_lyric_is_explicit_and_reviewable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            events = read_json(section_dir / "events.json")
            events["events"] = events["events"][:1]
            write_json(section_dir / "events.json", events)
            report = read_json(section_dir / "report.json")
            report["manual_final_conform"] = {
                "omitted_lyrics": [
                    {
                        "lyric_line_span": [2, 2],
                        "reason": "not_performed_in_live",
                        "evidence": ["user_manual_full_av_review"],
                    }
                ]
            }
            write_json(section_dir / "report.json", report)
            gate = validate_section(section_dir)
            self.assertEqual("passed", gate["automatic_result"])
            codes = {flag["code"] for flag in gate["review_flags"]}
            self.assertIn("omitted_live_lyrics_review_required", codes)

    def test_lyric_adlib_is_outside_canonical_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            events = read_json(section_dir / "events.json")
            events["events"].insert(
                1,
                {
                    "id": "song_01_adlib_0001",
                    "start": 3.2,
                    "end": 3.8,
                    "source_text": "もう一回",
                    "translation": "再来一次",
                    "role": "lyric_adlib",
                    "evidence": ["user_manual_full_av_review"],
                },
            )
            write_json(section_dir / "events.json", events)
            gate = validate_section(section_dir)
            self.assertEqual("passed", gate["automatic_result"])
            codes = {flag["code"] for flag in gate["review_flags"]}
            self.assertIn("lyric_adlib_review_required", codes)

    def test_non_finite_event_time_is_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            events = read_json(section_dir / "events.json")
            events["events"][0]["start"] = float("nan")
            write_json(section_dir / "events.json", events)
            gate = validate_section(section_dir)
            codes = {failure["code"] for failure in gate["hard_failures"]}
            self.assertIn("non_finite_time", codes)

    def test_tampered_package_hash_is_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            package = read_json(section_dir / "input.json")
            package["section_type"] = "mc"
            write_json(section_dir / "input.json", package)
            gate = validate_section(section_dir)
            codes = {failure["code"] for failure in gate["hard_failures"]}
            self.assertIn("input_hash_mismatch", codes)

    def test_high_risk_song_uses_representative_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            report = read_json(section_dir / "report.json")
            report["max_correction_seconds"] = 2.5
            write_json(section_dir / "report.json", report)
            validate_section(section_dir)
            decision = record_review(
                section_dir,
                "approve",
                REQUIRED_POINTS["song"],
                "",
                "main",
            )
            self.assertEqual("approved", decision["decision"])

    def test_missing_first_vocal_song_uses_representative_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            report = read_json(section_dir / "report.json")
            report.pop("first_vocal_start")
            write_json(section_dir / "report.json", report)
            validate_section(section_dir)
            decision = record_review(
                section_dir,
                "approve",
                REQUIRED_POINTS["song"],
                "",
                "main",
            )
            self.assertEqual("approved", decision["decision"])

    def test_low_confidence_song_uses_representative_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            report = read_json(section_dir / "report.json")
            report["review_flags"] = [
                {
                    "code": "low_confidence_transcription",
                    "detail": "fixture",
                }
            ]
            write_json(section_dir / "report.json", report)
            gate = validate_section(section_dir)
            self.assertIn(
                "low_confidence_transcription",
                {flag["code"] for flag in gate["review_flags"]},
            )
            decision = record_review(
                section_dir,
                "approve",
                REQUIRED_POINTS["song"],
                "",
                "main",
            )
            self.assertEqual("approved", decision["decision"])

    def test_lyrics_order_change_is_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            data = read_json(section_dir / "events.json")
            data["events"].reverse()
            data["events"][0]["start"] = 1.0
            data["events"][0]["end"] = 3.0
            data["events"][1]["start"] = 4.0
            data["events"][1]["end"] = 7.0
            write_json(section_dir / "events.json", data)
            gate = validate_section(section_dir)
            codes = {failure["code"] for failure in gate["hard_failures"]}
            self.assertIn("lyrics_text_or_order_changed", codes)


class EufoniusRegressionTests(unittest.TestCase):
    def _validate_fixture(self, name: str) -> set[str]:
        with tempfile.TemporaryDirectory() as temporary:
            source = SKILL_ROOT / "tests" / "fixtures" / name
            destination = Path(temporary) / name
            shutil.copytree(source, destination)
            gate = validate_section(destination)
            self.assertEqual("failed", gate["automatic_result"])
            return {failure["code"] for failure in gate["hard_failures"]}

    def test_song04_instrumental_hallucination_is_blocked(self) -> None:
        codes = self._validate_fixture("song04")
        self.assertIn("lyrics_before_vocal", codes)
        self.assertIn("lyrics_in_instrumental_gap", codes)

    def test_song05_long_backward_extrapolation_is_blocked(self) -> None:
        codes = self._validate_fixture("song05")
        self.assertIn("excessive_extrapolation", codes)
        self.assertIn("unsafe_reference_extrapolation", codes)


class SubtitleLayoutTests(unittest.TestCase):
    def test_mc_punctuation_keeps_tone_marks_and_decimal_points(self) -> None:
        source = "こんばんは、愛美です。元気ですか？待って...価格は1.5です！"
        expected = "こんばんは愛美です元気ですか？待って…価格は1.5です！"
        self.assertEqual(expected, normalize_mc_punctuation(source))
        self.assertTrue(mc_has_disallowed_punctuation(source))
        self.assertFalse(mc_has_disallowed_punctuation(expected))

    def test_ass_rendering_removes_mc_commas_and_periods_only(self) -> None:
        mc_event = {
            "id": "mc_01_0001",
            "section_type": "mc",
            "start": 1.0,
            "end": 5.0,
            "source_text": "こんばんは、愛美です。元気ですか？",
            "translation": "晚上好，我是爱美。大家好吗？",
        }
        song_event = {
            **mc_event,
            "id": "song_01_0001",
            "section_type": "song",
            "source_text": "歌、続く。",
            "translation": "歌，继续。",
        }
        mc_ass = render_ass([mc_event], "fixture")
        song_ass = render_ass([song_event], "fixture")
        self.assertIn("こんばんは愛美です元気ですか？", mc_ass)
        self.assertIn("晚上好我是爱美大家好吗？", mc_ass)
        self.assertNotIn("こんばんは、愛美です。", mc_ass)
        self.assertIn("歌、続く。", song_ass)
        self.assertIn("歌，继续。", song_ass)

    def test_ass_rendering_uses_final_bilingual_style(self) -> None:
        event = {
            "id": "mc_01_0001",
            "start": 1.0,
            "end": 5.0,
            "source_text": "あ" * 10,
            "translation": "中" * 10,
        }
        ass = render_ass([event], "fixture")
        self.assertIn("WrapStyle: 2", ass)
        self.assertIn("YCbCr Matrix: TV.709", ass)
        self.assertIn("Style: Bilingual,Arial,57", ass)
        dialogue = next(
            line for line in ass.splitlines() if line.startswith("Dialogue:")
        )
        self.assertEqual(1, dialogue.count(r"\N"))
        self.assertIn(
            r"{\fs80\fscx100\c&H007AD7FF&}" + ("中" * 10),
            dialogue,
        )
        self.assertTrue(
            dialogue.endswith(
                r"{\fs57\fscx100\c&H00FFFFFF&}" + ("あ" * 10)
            )
        )

    def test_long_ass_rendering_scales_without_automatic_wrap(self) -> None:
        event = {
            "id": "mc_01_0001",
            "start": 1.0,
            "end": 5.0,
            "source_text": "あ" * 40,
            "translation": "中" * 32,
        }
        ass = render_ass([event], "fixture")
        dialogue = next(
            line for line in ass.splitlines() if line.startswith("Dialogue:")
        )
        self.assertEqual(1, dialogue.count(r"\N"))
        self.assertIn(r"{\fs80\fscx66\c&H007AD7FF&}", dialogue)
        self.assertIn(r"{\fs57\fscx74\c&H00FFFFFF&}", dialogue)

    def test_mc_reflow_uses_word_timing_for_short_reply(self) -> None:
        events = [
            {
                "id": "mc_03_0015",
                "start": 53.84,
                "end": 58.94,
                "source_text": (
                    "ファンクラブにあげた動画で"
                    "ヒントとかも言ってたんだよね 言ってた"
                ),
                "translation": (
                    "我们在发到粉丝俱乐部的视频里也给过提示吧？给过"
                ),
                "evidence": ["word_timing_large-v3"],
            }
        ]
        segments = [
            {
                "start": 53.84,
                "end": 58.24,
                "text": (
                    "パンクラブにあげた動画で"
                    "ヒントとかも言ってたんだよね"
                ),
                "words": [
                    {
                        "start": 53.84,
                        "end": 58.24,
                        "text": (
                            "パンクラブにあげた動画で"
                            "ヒントとかも言ってたんだよね"
                        ),
                    }
                ],
            },
            {
                "start": 58.24,
                "end": 58.94,
                "text": "言ってた",
                "words": [
                    {
                        "start": 58.24,
                        "end": 58.94,
                        "text": "言ってた",
                    }
                ],
            },
        ]
        output, metrics = reflow_events(events, segments)
        self.assertEqual(2, len(output))
        self.assertEqual(1, metrics["split_event_count"])
        self.assertEqual("言ってた", output[1]["source_text"])
        self.assertEqual("给过", output[1]["translation"])
        self.assertAlmostEqual(58.24, output[1]["start"], places=2)
        self.assertEqual(0, metrics["merged_event_count"])

    def test_mc_reflow_splits_unpunctuated_clause_at_timed_segment_boundary(
        self,
    ) -> None:
        source = (
            "私たちも今日でライブ納めなので "
            "盛り上げて楽しんでいきたいと思いますよろしくね"
        )
        translation = (
            "我们今天也是今年最后一场演出，"
            "所以一起尽情热闹、尽情享受吧！"
        )
        events = [
            {
                "id": "mc_01_0001",
                "start": 10.0,
                "end": 15.0,
                "source_text": source,
                "translation": translation,
            }
        ]
        segments = [
            {
                "start": 10.0,
                "end": 11.8,
                "text": "私たちも今日でライブ納めなので",
                "words": [
                    {
                        "start": 10.0,
                        "end": 11.8,
                        "text": "私たちも今日でライブ納めなので",
                    }
                ],
            },
            {
                "start": 11.8,
                "end": 15.0,
                "text": "盛り上げて楽しんでいきたいと思いますよろしくね",
                "words": [
                    {
                        "start": 11.8,
                        "end": 15.0,
                        "text": "盛り上げて楽しんでいきたいと思いますよろしくね",
                    }
                ],
            },
        ]
        output, metrics = reflow_events(events, segments)
        self.assertEqual(2, len(output))
        self.assertEqual(1, metrics["split_event_count"])
        self.assertEqual("私たちも今日でライブ納めなので", output[0]["source_text"])
        self.assertEqual(
            "我们今天也是今年最后一场演出，",
            output[0]["translation"],
        )
        self.assertTrue(output[1]["translation"].startswith("所以"))
        self.assertEqual(source.replace(" ", ""), "".join(
            event["source_text"] for event in output
        ))
        self.assertEqual(translation, "".join(
            event["translation"] for event in output
        ))
        self.assertAlmostEqual(11.8, output[1]["start"], places=2)

    def test_mc_reflow_splits_source_two_translation_one_with_word_evidence(
        self,
    ) -> None:
        events = [
            {
                "id": "mc_01_0001",
                "start": 20.0,
                "end": 24.0,
                "source_text": "今日は最後です。楽しもう",
                "translation": "今天是最后一场，一起享受吧",
            }
        ]
        segments = [
            {
                "start": 20.0,
                "end": 21.8,
                "text": "今日は最後です",
                "words": [
                    {"start": 20.0, "end": 21.8, "text": "今日は最後です"}
                ],
            },
            {
                "start": 21.8,
                "end": 24.0,
                "text": "楽しもう",
                "words": [
                    {"start": 21.8, "end": 24.0, "text": "楽しもう"}
                ],
            },
        ]
        output, metrics = reflow_events(events, segments)
        self.assertEqual(2, len(output))
        self.assertEqual("今日は最後です。", output[0]["source_text"])
        self.assertEqual(
            "今天是最后一场，一起享受吧",
            "".join(event["translation"] for event in output),
        )
        self.assertEqual(1, metrics["split_event_count"])

    def test_mc_reflow_merges_short_unfinished_prefix(self) -> None:
        events = [
            {
                "id": "mc_01_0001",
                "start": 1.0,
                "end": 2.2,
                "source_text": "今日が最後なので",
                "translation": "因为是最后",
                "speaker": "A",
            },
            {
                "id": "mc_01_0002",
                "start": 2.5,
                "end": 5.0,
                "source_text": "最後まで楽しんでね",
                "translation": "请享受到最后吧",
                "speaker": "A",
            },
        ]
        output, metrics = reflow_events(events, [])
        self.assertEqual(1, len(output))
        self.assertEqual(1, metrics["merged_event_count"])
        self.assertEqual(0, metrics["split_event_count"])
        self.assertEqual(
            "今日が最後なので最後まで楽しんでね",
            output[0]["source_text"],
        )
        self.assertIn("timed_prefix_merge", output[0]["evidence"])

    def test_mc_reflow_does_not_merge_short_reply_or_different_speaker(
        self,
    ) -> None:
        short_reply = [
            {
                "id": "mc_01_0001",
                "start": 1.0,
                "end": 1.8,
                "source_text": "はい",
                "translation": "好的",
                "continuation": True,
            },
            {
                "id": "mc_01_0002",
                "start": 2.0,
                "end": 3.0,
                "source_text": "次です",
                "translation": "下一项",
            },
        ]
        different_speakers = [
            {
                "id": "mc_01_0001",
                "start": 1.0,
                "end": 2.0,
                "source_text": "そうなので",
                "translation": "所以是这样",
                "speaker": "A",
            },
            {
                "id": "mc_01_0002",
                "start": 2.2,
                "end": 3.0,
                "source_text": "違います",
                "translation": "不是的",
                "speaker": "B",
            },
        ]
        for events in (short_reply, different_speakers):
            with self.subTest(events=events):
                output, metrics = reflow_events(events, [])
                self.assertEqual(2, len(output))
                self.assertEqual(0, metrics["merged_event_count"])

    def test_reflow_review_detail_records_split_and_merge_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            concert_dir = Path(temporary)
            section_dir = concert_dir / "run" / "sections" / "mc_01"
            write_valid_song(section_dir)
            convert_to_valid_mc(section_dir)
            write_mc_assessment(section_dir)
            write_json(
                section_dir / "events.json",
                {
                    "events": [
                        {
                            "id": "mc_01_0001",
                            "start": 1.0,
                            "end": 2.0,
                            "source_text": "今日が最後なので",
                            "translation": "因为是最后",
                        },
                        {
                            "id": "mc_01_0002",
                            "start": 2.2,
                            "end": 4.0,
                            "source_text": "楽しんでね",
                            "translation": "请尽情享受",
                        },
                    ]
                },
            )
            write_json(section_dir / "report.json", {})
            transcript_path = (
                concert_dir
                / "run"
                / "evidence"
                / "transcripts"
                / "mc_01.formal.transcript.json"
            )
            write_json(transcript_path, {"segments": []})
            summary = reflow_section(concert_dir, "mc_01")
            report = read_json(section_dir / "report.json")
            detail = report["review_flags"][-1]["detail"]
            self.assertEqual(1, summary["merged_event_count"])
            self.assertIn("split=0", detail)
            self.assertIn("merge=1", detail)

    def test_crowded_mc_reflow_is_draft_only_and_does_not_modify_events(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            concert_dir = Path(temporary)
            section_dir = concert_dir / "run" / "sections" / "mc_01"
            write_valid_song(section_dir)
            convert_to_valid_mc(section_dir)
            write_mc_assessment(
                section_dir,
                classification="crowded_multi_speaker",
                speaker_count=5,
                signals={"speaker_identity_uncertain", "crowd_interjections"},
                evidence_basis={"audio_review", "video_review"},
            )
            before = read_json(section_dir / "events.json")
            summary = reflow_section(concert_dir, "mc_01")
            after = read_json(section_dir / "events.json")
            self.assertEqual("skipped_crowded_mc", summary["status"])
            self.assertTrue(summary["manual_conform_required"])
            self.assertEqual(before, after)


class PackageAndMergeTests(unittest.TestCase):
    def test_package_generation_uses_independent_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            concert_dir = Path(temporary) / "concert"
            run_dir = concert_dir / "run"
            run_dir.mkdir(parents=True)
            audio = concert_dir / "input.flac"
            audio.write_bytes(b"fake-flac")
            lyrics = concert_dir / "lyrics.txt"
            lyrics.write_text("歌詞\n", encoding="utf-8")
            translation = concert_dir / "translation.txt"
            translation.write_text("歌词\n", encoding="utf-8")
            write_json(
                run_dir / "concert_manifest.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "concert_id": "fixture",
                    "source_media": str(concert_dir / "source.mkv"),
                    "audio_path": "input.flac",
                    "duration_seconds": 20.0,
                    "sections": [
                        {
                            "id": "song_01",
                            "order": 1,
                            "type": "song",
                            "title": "Fixture",
                            "start": 2.0,
                            "end": 10.0,
                            "context_start": 0.0,
                            "context_end": 12.0,
                            "boundary_confidence": 0.9,
                            "lyrics_path": "lyrics.txt",
                            "translation_path": "translation.txt",
                            "translation_source": "netease",
                            "status": "planned",
                        }
                    ],
                },
            )
            summaries = build_packages(concert_dir, no_audio=True)
            self.assertEqual("prepared", summaries[0]["action"])
            package = read_json(run_dir / "sections" / "song_01" / "input.json")
            self.assertFalse(package.get("structure_review_required", False))
            self.assertEqual("audio.flac", package["audio_path"])
            self.assertEqual("translation.txt", package["translation_file"])
            self.assertEqual("netease", package["translation_source"])
            self.assertEqual(64, len(package["translation_hash"]))
            self.assertEqual(64, len(package["input_hash"]))
            self.assertTrue(package["gpu_required"])
            self.assertEqual(64, len(package["manifest_section_hash"]))

    def test_merge_requires_approval_and_renders_chinese_above_japanese(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            concert_dir = Path(temporary) / "concert"
            section_dir = concert_dir / "run" / "sections" / "song_01"
            write_valid_song(section_dir)
            write_json(
                concert_dir / "run" / "concert_manifest.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "concert_id": "fixture",
                    "source_media": str(concert_dir / "source.mkv"),
                    "audio_path": "audio.flac",
                    "duration_seconds": 40.0,
                    "sections": [
                        {
                            "id": "song_01",
                            "order": 1,
                            "type": "song",
                            "title": "Fixture",
                            "artist": "Fixture Artist",
                            "start": 10.0,
                            "end": 30.0,
                            "context_start": 0.0,
                            "context_end": 40.0,
                            "status": "planned",
                        }
                    ],
                },
            )
            bind_package_to_manifest(concert_dir, section_dir)
            validate_section(section_dir)
            record_review(
                section_dir,
                "approve",
                REQUIRED_POINTS["song"] | {"full_section"},
                "",
                "main",
            )
            report = merge_concert(concert_dir)
            self.assertEqual(2, report["event_count"])
            ass_path = Path(report["outputs"]["ass"])
            progress_ass_path = Path(report["outputs"]["song_progress_ass"])
            ass_bytes = ass_path.read_bytes()
            ass = ass_bytes.decode("utf-8-sig")
            self.assertIn(r"第一句\N{\fs57", ass)
            self.assertTrue(ass_bytes.startswith(b"\xef\xbb\xbf"))
            self.assertIn(r"{\fs80\fscx100\c&H007AD7FF&}", ass)
            self.assertEqual(
                {ass_path.name},
                {path.name for path in ass_path.parent.iterdir()},
            )
            self.assertTrue(Path(report["evidence"]["events"]).is_file())
            self.assertTrue(Path(report["evidence"]["report"]).is_file())
            self.assertTrue(progress_ass_path.is_file())
            validation = validate_concert(concert_dir)
            self.assertEqual("passed", validation["result"])

    def test_merge_rejects_result_modified_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            concert_dir = Path(temporary) / "concert"
            section_dir = concert_dir / "run" / "sections" / "song_01"
            write_valid_song(section_dir)
            write_json(
                concert_dir / "run" / "concert_manifest.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "concert_id": "fixture",
                    "source_media": str(concert_dir / "source.mkv"),
                    "audio_path": "audio.flac",
                    "duration_seconds": 40.0,
                    "sections": [
                        {
                            "id": "song_01",
                            "order": 1,
                            "type": "song",
                            "title": "Fixture",
                            "artist": "Fixture Artist",
                            "start": 10.0,
                            "end": 30.0,
                            "context_start": 0.0,
                            "context_end": 40.0,
                            "status": "planned",
                        }
                    ],
                },
            )
            bind_package_to_manifest(concert_dir, section_dir)
            validate_section(section_dir)
            record_review(
                section_dir,
                "approve",
                REQUIRED_POINTS["song"] | {"full_section"},
                "",
                "main",
            )
            result = read_json(section_dir / "events.json")
            result["events"][0]["start"] += 0.5
            write_json(section_dir / "events.json", result)
            with self.assertRaisesRegex(ValueError, "全场合并gate失败"):
                merge_concert(concert_dir)

    def test_merge_rejects_manifest_changed_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            concert_dir = Path(temporary) / "concert"
            section_dir = concert_dir / "run" / "sections" / "song_01"
            write_valid_song(section_dir)
            manifest_path = concert_dir / "run" / "concert_manifest.json"
            write_json(
                manifest_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "concert_id": "fixture",
                    "source_media": str(concert_dir / "source.mkv"),
                    "audio_path": "audio.flac",
                    "duration_seconds": 40.0,
                    "sections": [
                        {
                            "id": "song_01",
                            "order": 1,
                            "type": "song",
                            "title": "Fixture",
                            "artist": "Fixture Artist",
                            "start": 10.0,
                            "end": 30.0,
                            "context_start": 0.0,
                            "context_end": 40.0,
                            "status": "planned",
                        }
                    ],
                },
            )
            bind_package_to_manifest(concert_dir, section_dir)
            validate_section(section_dir)
            record_review(
                section_dir,
                "approve",
                REQUIRED_POINTS["song"] | {"full_section"},
                "",
                "main",
            )
            manifest = read_json(manifest_path)
            manifest["sections"][0]["start"] = 11.0
            manifest["sections"][0]["end"] = 31.0
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(ValueError, "全场合并gate失败"):
                merge_concert(concert_dir)

    def test_validate_concert_requires_formal_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            concert_dir = Path(temporary) / "concert"
            section_dir = concert_dir / "run" / "sections" / "song_01"
            write_valid_song(section_dir)
            write_json(
                concert_dir / "run" / "concert_manifest.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "concert_id": "fixture",
                    "source_media": str(concert_dir / "source.mkv"),
                    "audio_path": "audio.flac",
                    "duration_seconds": 40.0,
                    "sections": [
                        {
                            "id": "song_01",
                            "order": 1,
                            "type": "song",
                            "title": "Fixture",
                            "artist": "Fixture Artist",
                            "start": 10.0,
                            "end": 30.0,
                            "context_start": 0.0,
                            "context_end": 40.0,
                            "status": "planned",
                        }
                    ],
                },
            )
            bind_package_to_manifest(concert_dir, section_dir)
            validate_section(section_dir)
            record_review(
                section_dir,
                "approve",
                REQUIRED_POINTS["song"] | {"full_section"},
                "",
                "main",
            )
            validation = validate_concert(concert_dir)
            self.assertEqual("failed", validation["result"])
            codes = {failure["code"] for failure in validation["failures"]}
            self.assertIn("missing_formal_output", codes)

    def test_validate_concert_rejects_subtitle_content_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            concert_dir = Path(temporary) / "concert"
            section_dir = concert_dir / "run" / "sections" / "song_01"
            write_valid_song(section_dir)
            write_json(
                concert_dir / "run" / "concert_manifest.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "concert_id": "fixture",
                    "source_media": str(concert_dir / "source.mkv"),
                    "audio_path": "audio.flac",
                    "duration_seconds": 40.0,
                    "sections": [
                        {
                            "id": "song_01",
                            "order": 1,
                            "type": "song",
                            "title": "Fixture",
                            "artist": "Fixture Artist",
                            "start": 10.0,
                            "end": 30.0,
                            "context_start": 0.0,
                            "context_end": 40.0,
                            "status": "planned",
                        }
                    ],
                },
            )
            bind_package_to_manifest(concert_dir, section_dir)
            validate_section(section_dir)
            record_review(
                section_dir,
                "approve",
                REQUIRED_POINTS["song"] | {"full_section"},
                "",
                "main",
            )
            report = merge_concert(concert_dir)
            Path(report["outputs"]["ass"]).write_text(
                Path(report["outputs"]["ass"])
                .read_text(encoding="utf-8")
                .replace("第一句", "Tampered"),
                encoding="utf-8",
                newline="\r\n",
            )
            validation = validate_concert(concert_dir)
            codes = {failure["code"] for failure in validation["failures"]}
            self.assertIn("ass_content_mismatch", codes)

    def test_neighbor_boundary_change_invalidates_approved_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            concert_dir = Path(temporary) / "concert"
            run_dir = concert_dir / "run"
            run_dir.mkdir(parents=True)
            (concert_dir / "input.flac").write_bytes(b"fake-flac")
            manifest_path = run_dir / "concert_manifest.json"
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "concert_id": "fixture",
                "source_media": str(concert_dir / "source.mkv"),
                "audio_path": "input.flac",
                "duration_seconds": 30.0,
                "sections": [
                    {
                        "id": "song_01",
                        "order": 1,
                        "type": "song",
                        "title": "Song",
                        "start": 0.0,
                        "end": 10.0,
                        "context_start": 0.0,
                        "context_end": 12.0,
                        "boundary_confidence": 0.9,
                        "status": "planned",
                    },
                    {
                        "id": "mc_01",
                        "order": 2,
                        "type": "mc",
                        "title": "MC",
                        "start": 10.0,
                        "end": 20.0,
                        "context_start": 8.0,
                        "context_end": 22.0,
                        "boundary_confidence": 0.9,
                        "status": "planned",
                    },
                ],
            }
            write_json(manifest_path, manifest)
            build_packages(concert_dir, no_audio=True)
            for section_id in ("song_01", "mc_01"):
                section_dir = run_dir / "sections" / section_id
                (section_dir / "audio.flac").write_bytes(b"fake-flac")
                write_json(section_dir / "events.json", {"events": [{"id": "old"}]})
                write_json(section_dir / "report.json", {"old": True})
                status = read_json(section_dir / "status.json")
                status["status"] = "approved"
                write_json(section_dir / "status.json", status)

            manifest["sections"][1]["start"] = 11.0
            write_json(manifest_path, manifest)
            summaries = build_packages(
                concert_dir,
                section_ids={"mc_01"},
                no_audio=True,
            )
            actions = {
                summary["section_id"]: summary["action"]
                for summary in summaries
            }
            self.assertEqual("prepared", actions["song_01"])
            self.assertEqual("prepared", actions["mc_01"])
            self.assertFalse(
                (run_dir / "sections" / "song_01" / "events.json").exists()
            )
            archived = list(
                (
                    run_dir
                    / "sections"
                    / "song_01"
                    / "evidence"
                    / "stale-results"
                ).rglob("events.json")
            )
            self.assertEqual(1, len(archived))

    def test_unsafe_concert_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_filename_component("../escaped", "concert_id")


class RunSectionTests(unittest.TestCase):
    def test_gpu_required_package_cannot_bypass_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            section_dir.mkdir()
            (section_dir / "audio.flac").write_bytes(b"fake-flac")
            package = {
                "section_id": "song_01",
                "audio_path": "audio.flac",
                "gpu_required": True,
            }
            package["input_hash"] = package_input_hash(package)
            write_json(section_dir / "input.json", package)
            with self.assertRaisesRegex(ValueError, "必须使用--gpu"):
                run_section(
                    section_dir,
                    [sys.executable, "-c", "raise SystemExit(0)"],
                    False,
                    None,
                    0,
                )

    def test_approved_section_cannot_rerun_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            section_dir = Path(temporary) / "song_01"
            write_valid_song(section_dir)
            (section_dir / "audio.flac").write_bytes(b"fake-flac")
            write_json(
                section_dir / "status.json",
                {"status": "approved", "history": []},
            )
            with self.assertRaisesRegex(ValueError, "拒绝重复执行"):
                run_section(
                    section_dir,
                    [sys.executable, "-c", "raise SystemExit(0)"],
                    True,
                    Path(temporary) / "runtime",
                    0,
                )


class GpuQueueTests(unittest.TestCase):
    def test_shared_queue_uses_external_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_root = Path(temporary) / "subtitle-runtime"
            with patch.dict(
                os.environ,
                {
                    "SUBTITLE_RUNTIME_ROOT": str(runtime_root),
                    "CONCERT_SUBTITLE_GPU_QUEUE_DIR": "",
                },
                clear=False,
            ):
                self.assertEqual(
                    runtime_root.resolve()
                    / "locks"
                    / "concert-subtitle-gpu-queue",
                    shared_queue_directory(),
                )

    def test_shared_queue_reads_project_runtime_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary) / "concert-project"
            runtime_root = Path(temporary) / "subtitle-runtime"
            (project_root / "workspace").mkdir(parents=True)
            (project_root / "tool").mkdir()
            write_json(
                project_root / ".subtitle-runtime.json",
                {"runtime_root": str(runtime_root)},
            )
            with patch.dict(
                os.environ,
                {
                    "CONCERT_SUBTITLE_PROJECT_ROOT": str(project_root),
                    "SUBTITLE_RUNTIME_ROOT": "",
                    "CONCERT_SUBTITLE_GPU_QUEUE_DIR": "",
                },
                clear=False,
            ):
                self.assertEqual(
                    runtime_root.resolve()
                    / "locks"
                    / "concert-subtitle-gpu-queue",
                    shared_queue_directory(),
                )

    def test_shared_queue_refuses_skill_local_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary) / "concert-project"
            project_root.mkdir()
            with patch.dict(
                os.environ,
                {
                    "CONCERT_SUBTITLE_PROJECT_ROOT": str(project_root),
                    "SUBTITLE_RUNTIME_ROOT": "",
                    "CONCERT_SUBTITLE_GPU_QUEUE_DIR": "",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(RuntimeError, "未绑定机器级GPU队列目录"):
                    shared_queue_directory()

    def test_transient_windows_unlink_failure_is_retried(self) -> None:
        path = Mock()
        path.unlink.side_effect = [PermissionError("busy"), None]

        _unlink_with_retry(path, attempts=2, delay=0)

        self.assertEqual(2, path.unlink.call_count)

    def test_queue_releases_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            returncode = run_queued(
                runtime,
                "fixture",
                [sys.executable, "-c", "raise SystemExit(0)"],
                timeout=5,
                poll_interval=0.05,
                queue_dir_override=runtime / "shared",
            )
            self.assertEqual(0, returncode)
            status = queue_status(runtime, runtime / "shared")
            self.assertFalse(status["locked"])
            self.assertEqual([], status["waiting"])

    def test_different_concerts_share_one_gpu_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / "shared"
            results: list[int] = []

            def worker(index: int) -> None:
                results.append(
                    run_queued(
                        root / f"concert-{index}",
                        f"task-{index}",
                        [
                            sys.executable,
                            "-c",
                            "import time; time.sleep(0.3)",
                        ],
                        timeout=5,
                        poll_interval=0.02,
                        queue_dir_override=shared,
                    )
                )

            started = time.monotonic()
            threads = [
                threading.Thread(target=worker, args=(index,))
                for index in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            elapsed = time.monotonic() - started
            self.assertEqual([0, 0], sorted(results))
            self.assertGreaterEqual(elapsed, 0.5)


if __name__ == "__main__":
    unittest.main()

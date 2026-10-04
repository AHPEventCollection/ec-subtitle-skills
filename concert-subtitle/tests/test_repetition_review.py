from __future__ import annotations

# ruff: noqa: E402

import copy
import json
import math
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "repetition"
sys.path.insert(0, str(SCRIPTS))

from common import (
    SCHEMA_VERSION,
    canonical_hash,
    file_sha256,
    load_lyrics,
    read_json,
    render_srt,
    write_json,
)
from repetition_review import (
    apply_proposal,
    decide_proposal,
    import_reference,
    prepare_section,
    propose_section,
)
from subtitle_core import (
    RepetitionPolicy,
    RhythmPolicy,
    build_rhythm_evidence,
    detect_repetition_group,
    fallback_sample,
    match_timing_only_reference,
    normalized_cross_correlation,
    onset_energy_envelope,
    onset_pattern_correlation,
    representative_cycle,
)
from subtitle_core.evaluation import SubtitleEvent


def fixture_events() -> list[dict]:
    return read_json(FIXTURE / "events.json")["events"]


def write_wav(path: Path, duration: float = 24.0) -> None:
    sample_rate = 16_000
    count = round(duration * sample_rate)
    time = np.arange(count) / sample_rate
    signal = 0.12 * np.sin(2 * math.pi * 220 * time)
    for onset in (5.0, 7.0, 16.0, 18.0):
        first = round(onset * sample_rate)
        last = min(count, first + round(0.08 * sample_rate))
        signal[first:last] += np.hanning(last - first) * 0.65
    pcm = np.clip(signal, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes((pcm * 32767).astype("<i2").tobytes())


def write_concert(root: Path, events: list[dict] | None = None) -> Path:
    concert = root / "concert"
    section_dir = concert / "run" / "sections" / "song_01"
    section_dir.mkdir(parents=True)
    source_events = copy.deepcopy(events or fixture_events())
    write_json(
        concert / "run" / "concert_manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "concert_id": "fixture-repetition",
            "source_media": str(concert / "source.mkv"),
            "audio_path": "run/evidence/concert.flac",
            "duration_seconds": 30.0,
            "sections": [
                {
                    "id": "song_01",
                    "order": 1,
                    "type": "song",
                    "title": "Fixture",
                    "artist": "Fixture Artist",
                    "start": 2.0,
                    "end": 26.0,
                    "context_start": 2.0,
                    "context_end": 26.0,
                    "boundary_source": "fixture",
                    "boundary_confidence": 1.0,
                    "status": "approved",
                }
            ],
        },
    )
    package = {
        "schema_version": SCHEMA_VERSION,
        "concert_id": "fixture-repetition",
        "section_id": "song_01",
        "section_type": "song",
        "order": 1,
        "title": "Fixture",
        "artist": "Fixture Artist",
        "section_start": 2.0,
        "section_end": 26.0,
        "context_start": 2.0,
        "context_end": 26.0,
        "timeline_origin": 2.0,
        "audio_context_origin": 2.0,
        "boundary_source": "fixture",
        "boundary_confidence": 1.0,
        "review_priority": "normal",
        "gpu_required": False,
        "audio_path": "audio.wav",
        "lyrics_file": "lyrics.lrc",
    }
    package["input_hash"] = canonical_hash(package)
    write_json(section_dir / "input.json", package)
    write_json(
        section_dir / "events.json",
        {
            "schema_version": SCHEMA_VERSION,
            "section_id": "song_01",
            "events": source_events,
        },
    )
    write_json(section_dir / "report.json", {"fixture": True})
    write_json(section_dir / "status.json", {"status": "approved", "history": []})
    (section_dir / "lyrics.lrc").write_bytes((FIXTURE / "lyrics.lrc").read_bytes())
    write_wav(section_dir / "audio.wav")
    return concert


def write_import_srt(
    concert: Path,
    artifact_dir: Path,
    start_adjustment: float = 0.1,
    end_adjustment: float = 0.1,
) -> Path:
    groups = read_json(artifact_dir / "repetition_groups.json")
    ids = groups["representative"]["sample_event_ids"]
    events = read_json(concert / "run" / "sections" / "song_01" / "events.json")[
        "events"
    ]
    by_id = {event["id"]: event for event in events}
    origin = float(groups["representative"]["clip_start"])
    shifted = [
        {
            **by_id[event_id],
            "start": float(by_id[event_id]["start"]) - origin + start_adjustment,
            "end": float(by_id[event_id]["end"]) - origin + end_adjustment,
        }
        for event_id in ids
    ]
    path = artifact_dir / "reviewed.srt"
    path.write_text(render_srt(shifted), encoding="utf-8")
    return path


class RepetitionCoreTests(unittest.TestCase):
    def test_detects_longest_stable_repeated_block(self) -> None:
        group = detect_repetition_group(
            fixture_events(),
            known_lyrics=load_lyrics(FIXTURE / "lyrics.lrc"),
        )
        self.assertIsNotNone(group)
        assert group is not None
        self.assertEqual(2, group["anchor_event_count"])
        self.assertEqual(2, len(group["occurrences"]))
        self.assertTrue(group["stable"])

    def test_speech_breaks_contiguous_repetition(self) -> None:
        events = fixture_events()
        events[6]["role"] = "speech"
        self.assertIsNone(detect_repetition_group(events))

    def test_representative_cycle_expands_to_previous_structure_boundary(self) -> None:
        events = fixture_events()
        group = detect_repetition_group(events)
        assert group is not None
        sample = representative_cycle(events, group, 1, 24.0)
        self.assertEqual(["e5", "e6", "e7", "e8"], sample["sample_event_ids"])
        self.assertEqual(3.0, sample["padding_seconds"])
        self.assertLessEqual(sample["clip_duration"], 180.0)

    def test_fallback_sample_disables_anchor(self) -> None:
        events = fixture_events()[:4]
        sample = fallback_sample(events, 24.0, RepetitionPolicy())
        self.assertIsNone(sample["anchor_start_event_index"])
        self.assertFalse(sample["complete_anchor"])

    def test_onset_envelope_and_correlation_detect_same_pattern(self) -> None:
        signal = np.zeros(4000, dtype=np.float32)
        signal[500:600] = 1.0
        signal[2000:2100] = 0.8
        envelope = onset_energy_envelope(signal, frame_samples=100, hop_samples=20)
        shifted = np.roll(envelope, 4)
        correlation, lag = normalized_cross_correlation(
            envelope,
            shifted,
            max_lag_frames=8,
        )
        self.assertGreaterEqual(correlation, 0.95)
        self.assertEqual(4, lag)

    def test_onset_pattern_correlation_tolerates_boundary_phase(self) -> None:
        reference = np.zeros(800, dtype=np.float64)
        target = np.zeros(840, dtype=np.float64)
        reference[np.arange(40, 760, 80)] = 1.0
        target[np.arange(55, 815, 80)] = 1.0
        correlation = onset_pattern_correlation(reference, target)
        self.assertGreaterEqual(correlation, 0.75)

    def test_more_than_three_percent_duration_drift_is_outlier(self) -> None:
        events = fixture_events()
        events[7]["end"] = 19.7
        group = detect_repetition_group(events)
        assert group is not None
        pcm = np.sin(np.linspace(0, 500, 24 * 16_000)).astype(np.float32) * 0.1
        rhythm = build_rhythm_evidence(
            events,
            group,
            pcm,
            16_000,
            24.0,
            RhythmPolicy(),
        )
        non_representative = [
            item
            for item in rhythm["mappings"]
            if item["occurrence_index"] != rhythm["representative_occurrence_index"]
        ][0]
        self.assertGreater(non_representative["duration_drift_ratio"], 0.03)
        self.assertIn(
            "duration_drift_exceeds_3_percent",
            non_representative["outlier_reasons"],
        )

    def test_reference_rejects_event_count_change(self) -> None:
        expected = fixture_events()[:2]
        reference = [SubtitleEvent(0, 1.0, 2.0, expected[0]["source_text"])]
        result = match_timing_only_reference(
            expected,
            reference,
            full_concert=False,
        )
        self.assertFalse(result["accepted"])
        self.assertEqual("event_count_changed", result["outliers"][0]["code"])


class RepetitionWorkflowTests(unittest.TestCase):
    def prepare_fixture(self, root: Path, events: list[dict] | None = None):
        concert = write_concert(root, events)
        artifact = root / "artifacts"
        review = root / "review"
        summary = prepare_section(
            concert,
            "song_01",
            artifact_dir=artifact,
            review_dir=review,
            render_video=False,
        )
        return concert, artifact, review, summary

    def test_prepare_writes_hash_bound_groups_rhythm_and_editable_ass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            concert, artifact, review, summary = self.prepare_fixture(Path(temporary))
            del concert
            groups = read_json(artifact / "repetition_groups.json")
            rhythm = read_json(artifact / "rhythm_map.json")
            self.assertTrue(summary["propagation_enabled"])
            self.assertEqual(64, len(groups["artifact_hash"]))
            self.assertEqual(groups["artifact_hash"], rhythm["repetition_groups_hash"])
            self.assertTrue((review / "representative.ass").is_file())
            self.assertTrue(groups["representative"]["single_visible_axis"])

    def test_import_rejects_text_change_without_touching_formal_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            concert, artifact, _, _ = self.prepare_fixture(root)
            events_path = concert / "run" / "sections" / "song_01" / "events.json"
            before = events_path.read_bytes()
            reference = write_import_srt(concert, artifact)
            content = reference.read_text(encoding="utf-8").replace("Aメロ一", "改詞")
            reference.write_text(content, encoding="utf-8")
            result = import_reference(
                concert,
                "song_01",
                reference,
                artifact_dir=artifact,
            )
            self.assertFalse(result["accepted"])
            self.assertEqual(before, events_path.read_bytes())

    def test_propose_is_applied_false_and_does_not_modify_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            concert, artifact, review, _ = self.prepare_fixture(root)
            events_path = concert / "run" / "sections" / "song_01" / "events.json"
            before = events_path.read_bytes()
            reference = write_import_srt(concert, artifact)
            imported = import_reference(
                concert,
                "song_01",
                reference,
                artifact_dir=artifact,
            )
            self.assertTrue(imported["accepted"])
            proposal = propose_section(
                concert,
                "song_01",
                artifact_dir=artifact,
                review_dir=review,
                render_outliers=False,
            )
            self.assertFalse(proposal["applied"])
            self.assertEqual(before, events_path.read_bytes())
            self.assertGreater(proposal["estimated_review_reduction_event_count"], 0)

    def test_start_and_end_propagation_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            concert, artifact, review, _ = self.prepare_fixture(root)
            reference = write_import_srt(
                concert,
                artifact,
                start_adjustment=0.8,
                end_adjustment=0.2,
            )
            imported = import_reference(
                concert,
                "song_01",
                reference,
                artifact_dir=artifact,
            )
            self.assertTrue(imported["accepted"])
            proposal = propose_section(
                concert,
                "song_01",
                artifact_dir=artifact,
                review_dir=review,
                render_outliers=False,
            )
            target = next(
                change
                for change in proposal["changes"]
                if change["mode"] == "same_song_propagation"
            )
            self.assertIn("start_adjustment_exceeds_0_75_seconds", target["start"]["outlier_reasons"])
            self.assertIn("end", target["eligible_boundaries"])

    def test_propose_lists_more_than_three_percent_drift_as_outlier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events = fixture_events()
            events[7]["end"] = 19.7
            concert, artifact, review, summary = self.prepare_fixture(root, events)
            self.assertFalse(summary["propagation_enabled"])
            reference = write_import_srt(concert, artifact)
            imported = import_reference(
                concert,
                "song_01",
                reference,
                artifact_dir=artifact,
            )
            self.assertTrue(imported["accepted"])
            proposal = propose_section(
                concert,
                "song_01",
                artifact_dir=artifact,
                review_dir=review,
                render_outliers=False,
            )
            self.assertIn(
                "duration_drift_exceeds_3_percent",
                {item["code"] for item in proposal["outliers"]},
            )

    def test_apply_rejects_proposal_tampered_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            concert, artifact, review, _ = self.prepare_fixture(root)
            reference = write_import_srt(concert, artifact)
            import_reference(concert, "song_01", reference, artifact_dir=artifact)
            propose_section(
                concert,
                "song_01",
                artifact_dir=artifact,
                review_dir=review,
                render_outliers=False,
            )
            proposal_path = artifact / "proposal.json"
            decide_proposal(
                concert,
                "song_01",
                "approve",
                file_sha256(proposal_path),
                representative_reviewed=True,
                all_outliers_reviewed=True,
                reviewer="test",
                notes="",
                artifact_dir=artifact,
                require_assets=False,
            )
            proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
            proposal["changes"][0]["start"]["proposed"] += 0.2
            proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "证据哈希不一致"):
                apply_proposal(concert, "song_01", artifact_dir=artifact)

    def test_apply_archives_old_results_and_marks_section_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            concert, artifact, review, _ = self.prepare_fixture(root)
            reference = write_import_srt(concert, artifact)
            import_reference(concert, "song_01", reference, artifact_dir=artifact)
            propose_section(
                concert,
                "song_01",
                artifact_dir=artifact,
                review_dir=review,
                render_outliers=False,
            )
            proposal_path = artifact / "proposal.json"
            decide_proposal(
                concert,
                "song_01",
                "approve",
                file_sha256(proposal_path),
                representative_reviewed=True,
                all_outliers_reviewed=True,
                reviewer="test",
                notes="",
                artifact_dir=artifact,
                require_assets=False,
            )
            result = apply_proposal(concert, "song_01", artifact_dir=artifact)
            section_dir = concert / "run" / "sections" / "song_01"
            self.assertTrue(result["applied"])
            self.assertEqual("stale", read_json(section_dir / "status.json")["status"])
            archived = list((section_dir / "evidence" / "stale-results").rglob("events.json"))
            self.assertEqual(1, len(archived))
            self.assertTrue((section_dir / "events.json").is_file())


if __name__ == "__main__":
    unittest.main()

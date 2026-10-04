from __future__ import annotations

# ruff: noqa: E402

import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from evaluate_subtitle_timing import build_report as build_eval_report
from subtitle_core import (
    BoundaryPolicy,
    SubtitleEvent,
    TimedEvent,
    TranscriptSegment,
    TranscriptWord,
    align_events,
    compute_metrics,
    load_transcript,
    propose_boundaries,
    similarity,
)
from suggest_lyric_boundaries import build_report as build_boundary_report


class TranscriptTests(unittest.TestCase):
    def test_loads_v2_words_and_probability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "transcript.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "subsup.transcript.v2",
                        "segments": [
                            {
                                "start": 1.0,
                                "end": 2.0,
                                "text": "空",
                                "words": [
                                    {
                                        "start": 1.1,
                                        "end": 1.8,
                                        "text": "空",
                                        "probability": 0.92,
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            segments = load_transcript(path)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].words[0].text, "空")
        self.assertEqual(segments[0].words[0].probability, 0.92)

    def test_loads_v1_without_words(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "transcript.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "subsup.transcript.v1",
                        "segments": [{"start": 1, "end": 2, "text": "声"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            segments = load_transcript(path)

        self.assertEqual(segments[0].words, ())


class TextAndBoundaryTests(unittest.TestCase):
    def test_kana_folding_matches_kanji_reading(self) -> None:
        self.assertGreaterEqual(similarity("空空", "そらそら"), 0.8)

    def test_boundary_proposals_are_monotonic_and_review_only(self) -> None:
        transcript = [
            TranscriptSegment(
                start=9.5,
                end=13.0,
                text="空 空 高く",
                words=(
                    TranscriptWord(9.5, 10.0, "そら"),
                    TranscriptWord(10.1, 10.6, "空"),
                    TranscriptWord(11.0, 11.5, "高く"),
                ),
            )
        ]
        events = [
            TimedEvent("line_1", 9.8, 10.8, "空空"),
            TimedEvent("line_2", 10.8, 12.2, "高く"),
        ]
        proposals = propose_boundaries(
            events,
            transcript,
            policy=BoundaryPolicy(min_score=0.6),
        )

        self.assertEqual(proposals[0].matched_text, "そら空")
        self.assertEqual(proposals[1].matched_text, "高く")
        self.assertLessEqual(proposals[0].proposed_end, proposals[1].proposed_start)
        self.assertEqual([item.event_id for item in proposals], ["line_1", "line_2"])

    def test_low_confidence_keeps_original_boundaries(self) -> None:
        transcript = [
            TranscriptSegment(
                start=1.0,
                end=2.0,
                text="別の言葉",
                words=(TranscriptWord(1.0, 2.0, "別の言葉"),),
            )
        ]
        proposal = propose_boundaries(
            [TimedEvent("line_1", 1.2, 1.8, "一致しない")],
            transcript,
            policy=BoundaryPolicy(min_score=0.9),
        )[0]

        self.assertEqual(proposal.status, "unmatched_keep_original")
        self.assertEqual((proposal.proposed_start, proposal.proposed_end), (1.2, 1.8))

    def test_large_adjustment_is_recorded_but_keeps_original(self) -> None:
        transcript = [
            TranscriptSegment(
                start=1.0,
                end=2.0,
                text="空高く",
                words=(TranscriptWord(1.0, 2.0, "空高く"),),
            )
        ]
        proposal = propose_boundaries(
            [TimedEvent("line_1", 3.0, 4.0, "空高く")],
            transcript,
        )[0]

        self.assertEqual(proposal.status, "large_adjustment_keep_original")
        self.assertEqual((proposal.proposed_start, proposal.proposed_end), (3.0, 4.0))
        self.assertEqual((proposal.candidate_start, proposal.candidate_end), (1.0, 2.0))
        self.assertIn(
            "start_adjustment_exceeds_policy_without_strong_edge",
            proposal.rejected_reason or "",
        )
        self.assertIn(
            "end_adjustment_exceeds_policy_without_strong_edge",
            proposal.rejected_reason or "",
        )

    def test_strong_sentence_starts_can_move_far_and_define_handoff(self) -> None:
        transcript = [
            TranscriptSegment(
                start=1.0,
                end=5.5,
                text="まだまだ足りないようにもっともっと可愛く",
                words=(
                    TranscriptWord(1.0, 1.5, "まだ", 0.90),
                    TranscriptWord(1.5, 2.0, "まだ", 0.92),
                    TranscriptWord(2.0, 2.3, "足", 0.90),
                    TranscriptWord(2.3, 2.6, "り", 0.99),
                    TranscriptWord(2.6, 3.0, "ない", 0.99),
                    TranscriptWord(3.0, 3.5, "ように", 0.80),
                    TranscriptWord(3.5, 4.0, "も", 0.95),
                    TranscriptWord(4.0, 4.5, "っと", 0.99),
                    TranscriptWord(4.5, 5.0, "もっと", 0.95),
                    TranscriptWord(5.0, 5.5, "可愛く", 0.90),
                ),
            )
        ]
        proposals = propose_boundaries(
            [
                TimedEvent("line_1", 3.2, 5.2, "まだまだ足りないGrowin"),
                TimedEvent("line_2", 5.3, 6.0, "もっともっと可愛く"),
            ],
            transcript,
        )

        self.assertEqual(proposals[0].proposed_start, 1.0)
        self.assertEqual(proposals[0].proposed_end, 3.48)
        self.assertEqual(
            proposals[0].boundary_resolution,
            "end_from_next_sentence_start",
        )
        self.assertEqual(proposals[1].proposed_start, 3.5)
        self.assertGreaterEqual(proposals[1].start_probability or 0.0, 0.9)

    def test_only_conflicting_sentence_edge_is_rejected(self) -> None:
        transcript = [
            TranscriptSegment(
                start=1.8,
                end=3.0,
                text="空高く",
                words=(TranscriptWord(1.8, 3.0, "空高く"),),
            )
        ]
        proposals = propose_boundaries(
            [
                TimedEvent("line_1", 1.0, 2.0, "一致しない"),
                TimedEvent("line_2", 2.2, 3.5, "空高く"),
            ],
            transcript,
            policy=BoundaryPolicy(min_score=0.9),
        )

        self.assertEqual(proposals[1].status, "suggested_partial")
        self.assertEqual(
            (proposals[1].proposed_start, proposals[1].proposed_end),
            (2.2, 3.0),
        )
        self.assertTrue(proposals[1].would_overlap)
        self.assertEqual(
            proposals[1].rejected_reason,
            "start_candidate_overlaps_previous",
        )

    def test_word_chunk_is_refined_to_sentence_edge_syllables(self) -> None:
        transcript = [
            TranscriptSegment(
                start=1.0,
                end=4.0,
                text="青い空高く",
                words=(TranscriptWord(1.0, 4.0, "青い空高く"),),
            )
        ]
        proposal = propose_boundaries(
            [TimedEvent("line_1", 2.4, 4.2, "空高く")],
            transcript,
        )[0]

        self.assertEqual(proposal.status, "suggested")
        self.assertTrue(proposal.start_anchored)
        self.assertTrue(proposal.end_anchored)
        self.assertGreater(proposal.proposed_start, 1.5)
        self.assertNotEqual(proposal.proposed_start, 1.0)

    def test_boundary_report_does_not_modify_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            section_dir = root / "run" / "sections" / "song_01"
            section_dir.mkdir(parents=True)
            events_path = section_dir / "events.json"
            events_payload = {
                "events": [
                    {
                        "id": "song_01_0001",
                        "start": 1.0,
                        "end": 2.0,
                        "source_text": "空",
                        "translation": "天空",
                    },
                    {
                        "id": "song_01_0002",
                        "start": 2.0,
                        "end": 2.5,
                        "source_text": "話す",
                        "translation": "说话",
                        "role": "speech",
                    },
                ]
            }
            events_path.write_text(
                json.dumps(events_payload, ensure_ascii=False),
                encoding="utf-8",
            )
            original = events_path.read_bytes()
            transcript_path = root / "transcript.json"
            transcript_path.write_text(
                json.dumps(
                    {
                        "segments": [
                            {
                                "start": 1.1,
                                "end": 1.8,
                                "text": "そら",
                                "words": [
                                    {"start": 1.1, "end": 1.8, "text": "そら"}
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report = build_boundary_report(
                section_dir=section_dir,
                transcript_path=transcript_path,
                policy=BoundaryPolicy(),
            )

            self.assertEqual(events_path.read_bytes(), original)
        self.assertEqual(report["event_count"], 1)
        self.assertFalse(report["applied"])
        self.assertTrue(report["text_preserved"])


class EvaluationTests(unittest.TestCase):
    def test_aligns_one_machine_event_to_two_reference_events(self) -> None:
        hypothesis = [SubtitleEvent(0, 1.2, 4.3, "前半後半")]
        reference = [
            SubtitleEvent(0, 1.0, 2.0, "前半"),
            SubtitleEvent(1, 2.1, 4.0, "後半"),
        ]
        alignment = align_events(hypothesis, reference)
        metrics = compute_metrics(
            alignment,
            hypothesis_events=1,
            reference_events=2,
        )

        self.assertEqual(alignment.pairs[0].relation, "1:N")
        self.assertEqual(metrics.split_pairs, 1)
        self.assertEqual(metrics.start.median, 0.2)
        self.assertEqual(metrics.end.median, 0.3)
        self.assertEqual(metrics.coverage, 1.0)

    def test_cli_report_uses_last_bilingual_line_for_ass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hypothesis = root / "machine.json"
            reference = root / "human.ass"
            hypothesis.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "start": 1.2,
                                "end": 2.2,
                                "source_text": "空",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            reference.write_text(
                "[Events]\n"
                "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,"
                "天空\\N空\n",
                encoding="utf-8",
            )
            report = build_eval_report(
                hypothesis_path=hypothesis,
                reference_path=reference,
                hypothesis_line="last",
                reference_line="last",
                sub_frame_rate=None,
            )

        self.assertEqual(report["metrics"]["coverage"], 1.0)
        self.assertEqual(report["metrics"]["start"]["median"], 0.2)


if __name__ == "__main__":
    unittest.main()

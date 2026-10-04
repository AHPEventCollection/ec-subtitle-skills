from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from detect_embedded_speech import detect_candidates  # noqa: E402
from validate_section import _validate_lyric_event_sequence  # noqa: E402


class EmbeddedSpeechDetectionTests(unittest.TestCase):
    def test_detects_prelude_and_instrumental_speech(self) -> None:
        lyrics = [
            {
                "start": 10.0,
                "end": 14.0,
                "source_text": "太陽より先に Wake up!",
            },
            {
                "start": 20.0,
                "end": 24.0,
                "source_text": "キラキラ光る薬指を見つけに",
            },
        ]
        transcript = [
            {
                "start": 1.0,
                "end": 3.0,
                "text": "みなさんこんにちは",
                "words": [{"probability": 0.95}],
            },
            {
                "start": 10.1,
                "end": 13.7,
                "text": "太陽より先にウェイクアップ",
                "words": [{"probability": 0.92}],
            },
            {
                "start": 16.0,
                "end": 18.0,
                "text": "みんな声出せるの",
                "words": [{"probability": 0.9}],
            },
        ]
        candidates = detect_candidates(
            transcript,
            lyrics,
            [{"start": 15.0, "end": 19.0}],
        )
        self.assertEqual(2, len(candidates))
        self.assertEqual("prelude", candidates[0]["location"])
        self.assertEqual("instrumental_gap", candidates[1]["location"])
        self.assertEqual("speech", candidates[0]["role"])

    def test_ignores_short_or_lyric_like_segments(self) -> None:
        lyrics = [
            {
                "start": 10.0,
                "end": 14.0,
                "source_text": "太陽より先に Wake up!",
            }
        ]
        transcript = [
            {"start": 1.0, "end": 1.3, "text": "はい"},
            {
                "start": 10.1,
                "end": 13.8,
                "text": "太陽より先に Wake up!",
            },
        ]
        self.assertEqual([], detect_candidates(transcript, lyrics, []))

    def test_adjacent_lyric_lines_can_form_one_semantic_event(self) -> None:
        failures = _validate_lyric_event_sequence(
            ["息もつけない恋に", "溺れチャイナ"],
            [
                {
                    "source_text": "息もつけない恋に 溺れチャイナ",
                    "lyric_line_span": [1, 2],
                }
            ],
        )
        self.assertEqual([], failures)

    def test_lyric_line_span_cannot_skip_lines(self) -> None:
        failures = _validate_lyric_event_sequence(
            ["一", "二", "三"],
            [
                {"source_text": "一", "lyric_line_span": [1, 1]},
                {"source_text": "三", "lyric_line_span": [3, 3]},
            ],
        )
        self.assertTrue(
            any(item["code"] == "invalid_lyric_line_span" for item in failures)
        )


if __name__ == "__main__":
    unittest.main()

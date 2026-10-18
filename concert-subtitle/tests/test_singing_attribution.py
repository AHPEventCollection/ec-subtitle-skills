from __future__ import annotations

# ruff: noqa: E402
import copy
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from common import read_json, write_json
from merge_concert_subtitles import render_ass
from review_section import record_review
from singing_attribution import singing_text_runs, validate_singing_events
from validate_section import validate_section
from test_skill import write_valid_song


def solo(text: str, performer: str, color: str = "#52B8D8") -> dict:
    return {"text": text, "mode": "solo", "status": "confirmed", "performer_ids": [performer],
            "color": color, "evidence": [{"basis": "continuous_av_review", "reference": "evidence/notes.txt#sample"}]}


def event() -> dict:
    return {"id": "song_01_1", "section_type": "song", "start": 1.25, "end": 4.8,
            "source_text": "君の声（Hey!）響け", "translation": "你的声音回响吧",
            "singing_parts": [solo("君の声", "singer_a"),
                              {"text": "（Hey!）", "mode": "group", "status": "candidate"},
                              solo("響け", "singer_b", "#DA8040")]}


class SingingAttributionTests(unittest.TestCase):
    def test_phrase_handoff_changes_only_japanese_color(self) -> None:
        row = event()
        before = copy.deepcopy(row)
        plain = {k: v for k, v in row.items() if k != "singing_parts"}
        colored_ass = render_ass([row], "sample")
        base_ass = render_ass([plain], "sample")
        colored = next(line.split(",", 9) for line in colored_ass.splitlines() if line.startswith("Dialogue:"))
        base = next(line.split(",", 9) for line in base_ass.splitlines() if line.startswith("Dialogue:"))
        self.assertEqual(base[:9], colored[:9])
        self.assertEqual(base[9].split(r"\N")[0], colored[9].split(r"\N")[0])
        self.assertEqual(re.sub(r"\{[^}]*\}", "", base[9]), re.sub(r"\{[^}]*\}", "", colored[9]))
        self.assertIn(r"\c&H00D8B852&}君の声", colored[9])
        self.assertIn(r"\c&H00FFFFFF&}（Hey!）", colored[9])
        self.assertIn(r"\c&H004080DA&}響け", colored[9])
        self.assertEqual(before, row)

    def test_more_than_four_singers_and_group_members(self) -> None:
        rows = []
        for number in range(7):
            row = {"source_text": "歌", "singing_parts": [solo("歌", f"singer_{number}")]}
            rows.append(row)
        self.assertEqual([], validate_singing_events(rows))
        rows[0]["singing_parts"] = [{"text": "歌", "mode": "group", "status": "confirmed",
                                      "performer_ids": [f"singer_{n}" for n in range(7)],
                                      "evidence": [{"basis": "audio_review", "reference": "notes#group"}]}]
        self.assertEqual([("歌", "#FFFFFF")], singing_text_runs(rows[0]))

    def test_candidate_unknown_and_uncolored_solo_remain_white(self) -> None:
        for part in (solo("歌", "a"), {"text": "歌", "mode": "unknown", "status": "candidate"}):
            part["status"] = "candidate"
            self.assertEqual([("歌", "#FFFFFF")], singing_text_runs({"source_text": "歌", "singing_parts": [part]}))
        part = solo("歌", "a")
        part["color"] = None
        self.assertEqual([("歌", "#FFFFFF")], singing_text_runs({"source_text": "歌", "singing_parts": [part]}))

    def test_text_evidence_identity_and_mc_errors_are_rejected(self) -> None:
        bad_rows = []
        for key, value in (("text", "错字"), ("performer_ids", ["a", "b"]),
                           ("color", "red"), ("mode", []), ("status", "approved"),
                           ("evidence", []),
                           ("evidence", [{"basis": "camera_cut", "reference": "frame.jpg"}])):
            row = event()
            row["singing_parts"][0][key] = value
            bad_rows.append(row)
        mc = event()
        mc["section_type"] = "mc"
        bad_rows.append(mc)
        speech = event()
        speech["role"] = "speech"
        bad_rows.append(speech)
        for row in bad_rows:
            with self.subTest(row=row):
                self.assertTrue(validate_singing_events([row]))
                with self.assertRaises(ValueError):
                    render_ass([row], "sample")

    def test_same_identity_cannot_change_color_between_events(self) -> None:
        a, b = event(), event()
        b["singing_parts"][0]["color"] = "#FFFFFF"
        self.assertTrue(validate_singing_events([a, b]))
        with self.assertRaises(ValueError):
            render_ass([a, b], "sample")

    def test_section_gate_and_review_require_attribution_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            section = root / "run/sections/song_01"
            write_valid_song(section)
            data = read_json(section / "events.json")
            data["events"][0]["singing_parts"] = [solo("一行目", "singer_a")]
            write_json(section / "events.json", data)
            result = validate_section(section)
            self.assertEqual("passed", result["automatic_result"])
            with self.assertRaisesRegex(ValueError, "singing_attribution"):
                record_review(section, "approve", {"full_section"}, "", "test")
            decision = record_review(section, "approve", {"full_section", "singing_attribution"}, "fixture only", "test")
            self.assertEqual("approved", decision["decision"])
            data["events"][0]["singing_parts"][0]["color"] = "#DA8040"
            write_json(section / "events.json", data)
            with self.assertRaisesRegex(ValueError, "变化"):
                record_review(section, "approve", {"full_section", "singing_attribution"}, "", "test")


if __name__ == "__main__":
    unittest.main()

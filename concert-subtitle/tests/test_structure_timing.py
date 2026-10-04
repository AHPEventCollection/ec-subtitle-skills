from __future__ import annotations

import copy
import array
import math
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from structure_timing import (  # noqa: E402
    FIELDS, beat_anchors, choose_cycle, cycle_window, interpolate, load_structure,
    prepare_review, proposed_review, transfer, write_srt, write_tsv, audio_onsets, rhythm_hypotheses,
)


def sample() -> tuple[list[dict], list[dict]]:
    # Long pauses inside the first verse previously yielded a 15-second chorus.
    texts = ["主歌一", "主歌二", "过渡一", "过渡二", "副歌一", "副歌二",
             "二番主歌一", "二番主歌二", "二番过渡一", "二番过渡二", "副歌改词一", "副歌改词二"]
    times = [(10,14),(20,24),(30,34),(40,44),(50,54),(55,59),
             (94,98),(106,110),(118,122),(128,132),(140,144),(146,150)]
    events = [dict(id=str(i), start=a, end=b, source_text=t, translation=f"译{i}")
              for i, (t, (a,b)) in enumerate(zip(texts, times), 1)]
    rows = []
    for cycle, offset, origin, scale in [("1", 0, 10, 1.0), ("2", 6, 90, 1.25)]:
        for part, role, first, last, start, end, beats in [
            ("A", "verse", 1,2,0,20,16), ("B", "prechorus",3,4,20,40,16),
            ("C", "chorus",5,6,40,50,8)]:
            rows.append(dict(cycle=cycle, part=part, role=role, first=first+offset, last=last+offset,
                start=origin+start*scale, end=origin+end*scale, beats=beats, complete="yes", evidence="independent audio and lyrics reviewed"))
    return events, rows


class StructureTimingTests(unittest.TestCase):
    def test_full_cycle_includes_verse_despite_long_pauses(self):
        _, rows = sample()
        self.assertEqual((7.0, 63.0), cycle_window(choose_cycle(rows), 180))

    def test_chorus_only_cannot_pass_as_complete_cycle(self):
        _, rows = sample()
        with self.assertRaisesRegex(ValueError, "完整"):
            choose_cycle([row for row in rows if row["role"] == "chorus"])

    def test_incomplete_first_cycle_can_choose_complete_second(self):
        _, rows = sample()
        rows[0]["complete"] = "no"
        self.assertEqual("2", choose_cycle(rows)[0]["cycle"])
        with self.assertRaisesRegex(ValueError, "完整"):
            choose_cycle(rows, "1")

    def test_long_cycle_is_not_cut_to_duration_cap(self):
        _, rows = sample()
        for row in rows[:3]:
            row["start"] *= 4
            row["end"] *= 4
        self.assertGreater(cycle_window(choose_cycle(rows[:3]), 300)[1] - 37, 180)

    def test_different_lyrics_transfer_full_template_not_old_axis_delta(self):
        events, rows = sample()
        reviewed = copy.deepcopy(events[:6])
        reviewed[0].update(start=11, end=15)
        result, usage = transfer(events, rows, choose_cycle(rows), reviewed, [])
        self.assertAlmostEqual(91.25, result[6]["start"])
        self.assertAlmostEqual(96.25, result[6]["end"])
        self.assertEqual(events[6]["source_text"], result[6]["source_text"])
        self.assertEqual(events[10]["translation"], result[10]["translation"])
        self.assertEqual(6, sum(r["status"] == "mapped" for r in usage))
        self.assertEqual(len(events), len(usage))

    def test_phrase_landmarks_keep_sub_beat_offsets(self):
        onsets = [0.0] * 1000
        onsets[410] = 1.0
        anchors, support = beat_anchors(dict(start=0.0, end=8.0, beats=8), onsets)
        self.assertEqual(1, support)
        self.assertEqual([0.0, 4.1, 8.0], anchors)
        mapped = interpolate(4.2, [0,4,8], [10,15,18])
        self.assertAlmostEqual(15.15, mapped)

    def test_structural_change_is_local_and_other_parts_still_transfer(self):
        events, rows = sample()
        rows[3]["beats"] = 12
        result, usage = transfer(events, rows, choose_cycle(rows), events[:6], [])
        self.assertEqual(events[6], result[6])
        self.assertEqual("local_review", usage[6]["status"])
        self.assertEqual("mapped", usage[8]["status"])
        self.assertEqual("1-2", usage[6]["reference"])

    def test_pack_rejects_changed_structure_and_preserves_manual_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events, rows = sample()
            plan, output = root / "structure.tsv", root / "review"
            write_tsv(plan, rows, FIELDS)
            prepare_review(events, plan, 180, output)
            result, _ = proposed_review(events, plan, 180, output, [])
            edited = copy.deepcopy(result)
            edited[6]["start"] += 0.1
            write_srt(output / "structure-candidate.srt", edited)
            before = (output / "structure-candidate.srt").read_bytes()
            with self.assertRaisesRegex(ValueError, "拒绝覆盖"):
                proposed_review(events, plan, 180, output, [])
            self.assertEqual(before, (output / "structure-candidate.srt").read_bytes())
            proposed_review(events, plan, 180, output, [], write=False)
            rows[0]["end"] += 0.1
            write_tsv(plan, rows, FIELDS)
            with self.assertRaises(ValueError):
                proposed_review(events, plan, 180, output, [], write=False)

    def test_changed_reference_invalidates_old_transfer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events, rows = sample()
            plan, output = root / "structure.tsv", root / "review"
            write_tsv(plan, rows, FIELDS)
            prepare_review(events, plan, 180, output)
            proposed_review(events, plan, 180, output, [])
            revised = copy.deepcopy(events[:6])
            revised[0]["start"] += .1
            write_srt(output / "reference.srt", revised)
            with self.assertRaisesRegex(ValueError, "已变化"):
                proposed_review(events, plan, 180, output, [], write=False)

    def test_fake_or_speech_structure_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            events, rows = sample()
            path = Path(temporary) / "structure.tsv"
            write_tsv(path, rows, FIELDS)
            events[0]["role"] = "speech"
            with self.assertRaisesRegex(ValueError, "讲话"):
                load_structure(path, events, 180)

    def test_reference_cannot_leave_own_part_even_without_target(self):
        events, rows = sample()
        reviewed = copy.deepcopy(events[:6])
        reviewed[1]["end"] = 30.1
        reviewed[2]["start"] = 31
        with self.assertRaisesRegex(ValueError, "结构边界"):
            transfer(events[:6], rows[:3], rows[:3], reviewed, [])

    def test_rhythm_keeps_tempo_between_old_50ms_bins(self):
        onsets = [0.0]*2400
        for beat in range(39):
            index = round(beat*60/97*100)
            if index < len(onsets):
                onsets[index] = 1.0
        hints = rhythm_hypotheses(onsets, 0, 24)
        self.assertTrue(any(abs(hint["bpm"]-97) < 1 for hint in hints), hints)

    def test_target_overlap_keeps_other_transfers_and_marks_both_lines(self):
        events, rows = sample()
        rows[4]["complete"] = "no"
        events[9]["end"] = 141
        events[10]["start"] = 142
        result, usage = transfer(events, rows, choose_cycle(rows), events[:6], [])
        self.assertEqual(140, result[10]["start"])
        self.assertEqual("local_review", usage[10]["status"])
        self.assertEqual("local_review", usage[9]["status"])
        self.assertIn("overlaps_previous_line_10", usage[10]["reason"])
        self.assertEqual("mapped", usage[6]["status"])

    def test_overlapping_reference_cannot_propagate(self):
        events, rows = sample()
        reviewed = copy.deepcopy(events[:6])
        reviewed[0]["end"] = 21
        with self.assertRaisesRegex(ValueError, "代表段自身有重叠"):
            transfer(events, rows, choose_cycle(rows), reviewed, [])

    @unittest.skipUnless(shutil.which("ffmpeg"), "runtime ffmpeg required")
    def test_real_wav_decoding_offset_and_rhythm_hypotheses(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pulses.wav"
            # Two-second context precedes sixteen seconds of 120-BPM pulses.
            samples = array.array("h", [0] * (18*8000))
            for beat in range(32):
                for i in range(320):
                    samples[(2*8000)+beat*4000+i] = round(22000*math.sin(i*.4)*math.exp(-i/80))
            if sys.byteorder != "little":
                samples.byteswap()
            with wave.open(str(path), "wb") as handle:
                handle.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                handle.writeframes(samples.tobytes())
            onsets = audio_onsets(path, Path(shutil.which("ffmpeg")), offset=2)
            self.assertAlmostEqual(16, len(onsets)/100, places=2)
            self.assertGreater(onsets[0], 0)
            hints = rhythm_hypotheses(onsets, 0, 16)
            self.assertTrue(any(h["bpm"] == 120 and h["estimated_beats"] == 32 for h in hints))
            self.assertEqual([], rhythm_hypotheses([0.0]*1600, 0, 16))


if __name__ == "__main__":
    unittest.main()

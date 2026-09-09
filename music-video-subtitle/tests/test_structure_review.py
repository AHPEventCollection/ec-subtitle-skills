from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from mv_pipeline import build_subtitle  # noqa: E402
from structure_review import (  # noqa: E402
    start_structure_review, prepare_structure, propose_structure, validate_structure_promotion,
    preview_structure,
)
from structure_timing import FIELDS, write_srt, write_tsv, read_tsv, normalized_events  # noqa: E402
from test_structure_timing import sample  # noqa: E402


class MvStructureIntegrationTests(unittest.TestCase):
    def test_conflicting_target_is_previewable_but_cannot_promote_until_fixed(self):
        with tempfile.TemporaryDirectory() as temporary:
            mv = Path(temporary) / "workspace" / "mvs" / "fixture"
            source = mv / "source" / "source.mkv"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"unchanged video")
            initial = mv / "review" / "alignment" / "candidates" / "sofa-only.srt"
            initial.parent.mkdir(parents=True)
            events, rows = sample()
            rows[4]["complete"] = "no"
            events[9]["end"] = 141
            events[10]["start"] = 142
            write_srt(initial, events)
            start_structure_review(mv, initial)
            write_tsv(mv / "review" / "structure.tsv", rows, FIELDS)
            with patch("structure_review.probe_media", return_value=SimpleNamespace(duration=180)), patch(
                    "structure_review._onsets", return_value=[]):
                prepare_structure(mv, initial, "1")
                candidate = propose_structure(mv)
                self.assertTrue((mv / "review" / "candidate-preview" / "source.srt").exists())
                with self.assertRaisesRegex(ValueError, "重叠"):
                    validate_structure_promotion(mv, candidate)
                revised = normalized_events(read_tsv(mv / "review" / "structure" / "candidate-events.tsv"))
                revised[10]["start"] = 141.1
                write_srt(candidate, revised)
                before = candidate.read_bytes()
                preview_structure(mv)
                validate_structure_promotion(mv, candidate)
                self.assertEqual(before, candidate.read_bytes())
                self.assertEqual(before, (mv / "review" / "candidate-preview" / "source.srt").read_bytes())
                self.assertFalse((mv / "subtitle" / "master.srt").exists())

    def test_official_source_is_revalidated_before_structural_prepare(self):
        with tempfile.TemporaryDirectory() as temporary:
            mv = Path(temporary)
            candidate = mv / "official-subtitle.srt"
            with patch("structure_review.ensure_workspace", return_value=mv), patch(
                    "official_subtitle.validate_official_candidate", side_effect=ValueError("frozen source changed")) as validate:
                with self.assertRaisesRegex(ValueError, "frozen source"):
                    prepare_structure(mv, candidate)
                validate.assert_called_once_with(mv, candidate)

    def test_structure_candidate_is_used_and_master_remains_protected(self):
        with tempfile.TemporaryDirectory() as temporary:
            mv = Path(temporary) / "workspace" / "mvs" / "fixture"
            source = mv / "source" / "source.mkv"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"unchanged full video")
            initial = mv / "review" / "alignment" / "candidates" / "sofa-only.srt"
            initial.parent.mkdir(parents=True)
            events, rows = sample()
            write_srt(initial, events)
            start_structure_review(mv, initial)
            with self.assertRaisesRegex(ValueError, "完整代表段"):
                validate_structure_promotion(mv, initial)
            write_tsv(mv / "review" / "structure.tsv", rows, FIELDS)
            with patch("structure_review.probe_media", return_value=SimpleNamespace(duration=180)):
                prepare_structure(mv, initial, "2")
                reference = mv / "review" / "structure" / "reference.srt"
                self.assertIn("00:01:", reference.read_text(encoding="utf-8-sig"))
                with patch("structure_review._onsets", return_value=[]):
                    candidate = propose_structure(mv)
                    validate_structure_promotion(mv, candidate)
                    # Switching to an older reference must refresh the paired SRT.
                    prepare_structure(mv, initial, "2")
                    paired = mv / "review" / "candidate-preview" / "source.srt"
                    self.assertEqual(reference.read_bytes(), paired.read_bytes())
                    preview_structure(mv)
                    self.assertEqual(candidate.read_bytes(), paired.read_bytes())
                    self.assertFalse((mv / "subtitle" / "master.srt").exists())
                    (mv / "review" / "alignment" / "candidate-check-sofa.md").write_text("passed", encoding="utf-8")
                    master = build_subtitle(mv, candidate)
                    before = master.read_bytes()
                    with self.assertRaises(FileExistsError):
                        build_subtitle(mv, candidate)
                    self.assertEqual(before, master.read_bytes())
            self.assertEqual(b"unchanged full video", source.read_bytes())
            self.assertFalse(list((mv / "review" / "structure").glob("*.json")))


if __name__ == "__main__":
    unittest.main()

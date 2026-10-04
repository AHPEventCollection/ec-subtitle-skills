from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from common import read_json, write_json, package_input_hash, canonical_hash, manifest_section_contract  # noqa: E402
from review_section import record_review  # noqa: E402
from structure_review import prepare_section, propose_section, apply_section  # noqa: E402
from structure_timing import FIELDS, write_tsv  # noqa: E402
from test_skill import write_valid_song  # noqa: E402
from validate_section import validate_section  # noqa: E402


class ConcertStructureIntegrationTests(unittest.TestCase):
    def test_full_review_apply_invalidates_old_approval_and_allows_fresh_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            concert = Path(temporary)
            section = concert / "run" / "sections" / "song_01"
            write_valid_song(section)
            package = read_json(section / "input.json")
            package.update(concert_id="fixture", order=1, title="", artist="", boundary_source="fixture")
            package["structure_review_required"] = True
            manifest = dict(
                schema_version="0.1.0", duration_seconds=40, concert_id="fixture", audio_path="mix.flac",
                source_media=str(concert / "source.mkv"), sections=[dict(
                    id="song_01", type="song", start=10, end=30, order=1, context_start=0, context_end=40,
                    boundary_source="fixture", boundary_confidence=.9)])
            package["manifest_section_hash"] = canonical_hash(manifest_section_contract(manifest, 0))
            package["input_hash"] = package_input_hash(package)
            write_json(section / "input.json", package)
            write_json(concert / "run" / "concert_manifest.json", manifest)
            write_tsv(section / "structure.tsv", [
                dict(cycle="1", part="A", role="verse", first=1, last=1,
                     start=0, end=3.5, beats=4, complete="yes", evidence="fixture audio"),
                dict(cycle="1", part="C", role="chorus", first=2, last=2,
                     start=3.5, end=8, beats=4, complete="yes", evidence="fixture audio"),
            ], FIELDS)
            gate = validate_section(section)
            self.assertNotIn("complete_cycle_transfer_review_required", [f["code"] for f in gate["review_flags"]])
            with self.assertRaisesRegex(ValueError, "full_section"):
                record_review(section, "approve", {"representative_verse_to_chorus"}, "", "fixture")
            record_review(section, "approve", {"full_section"}, "", "fixture")
            original = (section / "events.json").read_bytes()
            prepare_section(concert, "song_01", render_video=False)
            with patch("structure_review.audio_onsets", return_value=[0.0]*2000):
                propose_section(concert, "song_01")
                self.assertEqual(original, (section / "events.json").read_bytes())
                with self.assertRaisesRegex(ValueError, "复核"):
                    apply_section(concert, "song_01", reviewed=False)
                apply_section(concert, "song_01", reviewed=True)
            self.assertEqual("stale", read_json(section / "status.json")["status"])
            gate = validate_section(section)
            self.assertEqual("passed", gate["automatic_result"])
            record_review(section, "approve", {"full_section"}, "", "fixture")
            # Post-apply review refresh uses the applied axis, not the stale initial snapshot.
            prepare_section(concert, "song_01", render_video=False)
            plan = section / "structure.tsv"
            plan.write_text(plan.read_text(encoding="utf-8").replace("fixture audio", "changed evidence"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "失效"):
                record_review(section, "approve", {"full_section"}, "", "fixture")

    def test_legacy_song_without_structure_can_be_reviewed_as_full_section(self):
        with tempfile.TemporaryDirectory() as temporary:
            section = Path(temporary) / "song_01"
            write_valid_song(section)
            package = read_json(section / "input.json")
            package["structure_review_required"] = True
            package["input_hash"] = package_input_hash(package)
            write_json(section / "input.json", package)
            gate = validate_section(section)
            self.assertNotIn("complete_cycle_transfer_review_required", [f["code"] for f in gate["review_flags"]])
            self.assertFalse((section / "structure.tsv").exists())
            decision = record_review(section, "approve", {"full_section"}, "", "fixture")
            self.assertEqual("approved", decision["decision"])


if __name__ == "__main__":
    unittest.main()

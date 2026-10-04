from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from verified_archive import archive_directory, cleanup_verified_source, verify_report  # noqa: E402


class VerifiedArchiveTests(unittest.TestCase):
    def make_source(self, root: Path) -> Path:
        source = root / "concert-workspace"
        (source / "output").mkdir(parents=True)
        (source / "output" / "final.mp4").write_bytes(b"video")
        (source / "manifest.json").write_text("{}\n", encoding="utf-8")
        return source

    def test_archive_writes_verified_report_without_removing_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_source(root)
            destination = root / "archive"
            report = root / "archive-report.json"

            self.assertEqual(report.resolve(), archive_directory(source, destination, report))

            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(2, payload["file_count"])
            self.assertTrue(source.is_dir())
            self.assertEqual(b"video", (destination / "output" / "final.mp4").read_bytes())
            self.assertEqual(payload, verify_report(report))

    def test_cleanup_removes_only_the_report_source_after_reverification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_source(root)
            destination = root / "archive"
            report = root / "archive-report.json"
            archive_directory(source, destination, report)

            self.assertEqual(source.resolve(), cleanup_verified_source(report, source))

            self.assertFalse(source.exists())
            self.assertTrue(destination.is_dir())

    def test_changed_archive_blocks_cleanup_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_source(root)
            destination = root / "archive"
            report = root / "archive-report.json"
            archive_directory(source, destination, report)
            (destination / "output" / "final.mp4").write_bytes(b"tampered")

            with self.assertRaises(ValueError):
                cleanup_verified_source(report, source)

            self.assertTrue(source.is_dir())

    def test_nested_destination_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_source(root)
            report = root / "archive-report.json"

            with self.assertRaises(ValueError):
                archive_directory(source, source / "archive", report)


if __name__ == "__main__":
    unittest.main()

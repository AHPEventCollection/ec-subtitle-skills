from __future__ import annotations

# ruff: noqa: E402

import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from archive_concert import DEFAULT_ARCHIVE_TOOL, archive_concert, resolve_archive_tool


class ArchiveRoutingTests(unittest.TestCase):
    def test_unfinished_project_archives_without_manifest_or_render(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "unfinished"
            source.mkdir()
            notes = source / "notes.txt"
            notes.write_text("operator draft", encoding="utf-8")
            destination = root / "archive"
            report = archive_concert(source, destination, archive_tool=DEFAULT_ARCHIVE_TOOL)
            self.assertTrue(report.is_file())
            self.assertEqual((destination / "notes.txt").read_bytes(), notes.read_bytes())
            self.assertTrue(notes.is_file())
            self.assertFalse((source / "output").exists())

    def test_invalid_or_missing_render_receipt_target_does_not_block_archive(self) -> None:
        receipts = ("broken json", json.dumps({"status": "failed", "output": "missing.mp4"}))
        for receipt in receipts:
            with self.subTest(receipt=receipt), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "unfinished"
                path = source / "run/evidence/render-receipt.json"
                path.parent.mkdir(parents=True)
                path.write_text(receipt, encoding="utf-8")
                # Even an invalid manifest must remain an archived fact, not a gate.
                (source / "concert_manifest.json").write_text("unfinished", encoding="utf-8")
                destination = root / "archive"
                archive_concert(source, destination, archive_tool=DEFAULT_ARCHIVE_TOOL)
                self.assertEqual((destination / "run/evidence/render-receipt.json").read_bytes(), path.read_bytes())

    def test_external_current_file_is_preserved_with_stale_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "unfinished"
            external = root / "external.mp4"
            external.write_bytes(b"current unfinished render")
            path = source / "run/evidence/render-receipt.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "status": "failed", "output": str(external),
                "output_sha256": "stale", "manifest_sha256": "stale",
            }), encoding="utf-8")
            destination = root / "archive"
            archive_concert(source, destination, archive_tool=DEFAULT_ARCHIVE_TOOL)
            self.assertEqual((destination / "output/external.mp4").read_bytes(), external.read_bytes())
            self.assertEqual((destination / "run/evidence/render-receipt.json").read_bytes(), path.read_bytes())
            self.assertTrue(external.is_file())

    def test_explicit_archive_tool_must_be_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tool = Path(temporary) / "verified_archive.py"
            tool.write_text("# fixture\n", encoding="utf-8")
            self.assertEqual(tool.resolve(), resolve_archive_tool(tool))

    def test_environment_can_override_default_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tool = Path(temporary) / "verified_archive.py"
            tool.write_text("# fixture\n", encoding="utf-8")
            with patch.dict(os.environ, {"MEDIA_WORKFLOW_ARCHIVE_TOOL": str(tool)}):
                self.assertEqual(tool.resolve(), resolve_archive_tool())

    def test_default_points_to_packaged_archive_tool(self) -> None:
        expected = SKILL_ROOT / "scripts" / "verified_archive.py"
        self.assertEqual(expected.resolve(), DEFAULT_ARCHIVE_TOOL.resolve())
        self.assertTrue(DEFAULT_ARCHIVE_TOOL.is_file())


if __name__ == "__main__":
    unittest.main()

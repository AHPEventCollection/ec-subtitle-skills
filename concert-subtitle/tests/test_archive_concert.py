from __future__ import annotations

# ruff: noqa: E402

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from archive_concert import DEFAULT_ARCHIVE_TOOL, resolve_archive_tool


class ArchiveRoutingTests(unittest.TestCase):
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

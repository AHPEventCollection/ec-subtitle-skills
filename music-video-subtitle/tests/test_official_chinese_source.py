from __future__ import annotations

# ruff: noqa: E402
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from chinese_source import OFFICIAL_CREDIT, read_chinese_source, record_chinese_source
from mv_pipeline import build_parser


class OfficialChineseSourceTests(unittest.TestCase):
    def test_official_credit_roundtrip_without_model_attribution(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace" / "mvs" / "fixture"
            record_chinese_source(workspace, "official")
            self.assertEqual(read_chinese_source(workspace), OFFICIAL_CREDIT)
            with self.assertRaises(ValueError):
                record_chinese_source(workspace, "official", "unused-model")
            self.assertEqual(read_chinese_source(workspace), OFFICIAL_CREDIT)

    def test_cli_accepts_official_source(self):
        args = build_parser().parse_args(
            ["chinese-source", "--mv-dir", "example-mv", "--kind", "official"]
        )
        self.assertEqual(args.kind, "official")


if __name__ == "__main__":
    unittest.main()

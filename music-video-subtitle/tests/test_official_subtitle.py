from __future__ import annotations

# ruff: noqa: E402

import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import common
from common import ensure_workspace
from official_subtitle import (
    build_official_candidate,
    discover_official_subtitle,
    load_official_cues,
    prepare_official_subtitle,
)
from mv_pipeline import build_subtitle
from subtitle_io import load_cues


VTT = """WEBVTT

00:00:01.250 --> 00:00:03.500 align:start position:0%
<c.yellow>最初の一行</c>

00:04.000 --> 00:06.750
次の一行
"""


class OfficialSubtitleTests(unittest.TestCase):
    def _workspace(self, root: Path) -> Path:
        mv_dir = root / "workspace" / "mvs" / "fixture"
        with patch.object(common, "project_root", return_value=root):
            ensure_workspace(mv_dir)
        return mv_dir

    def test_vtt_parser_preserves_official_event_times(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.ja.vtt"
            path.write_text(VTT, encoding="utf-8")
            cues = load_official_cues(path)

            self.assertEqual((1250, 3500, "最初の一行"), (cues[0].start_ms, cues[0].end_ms, cues[0].japanese))
            self.assertEqual((4000, 6750, "次の一行"), (cues[1].start_ms, cues[1].end_ms, cues[1].japanese))

    def test_prepare_discovers_official_japanese_track(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            official = mv_dir / "source" / "source.ja.vtt"
            official.write_text(VTT, encoding="utf-8")
            with patch.object(common, "project_root", return_value=root):
                self.assertEqual(official.resolve(), discover_official_subtitle(mv_dir))
                translation = prepare_official_subtitle(mv_dir)

            rows = self._rows(translation)
            self.assertEqual("1250", rows[0]["start_ms"])
            self.assertEqual("3500", rows[0]["end_ms"])
            self.assertEqual("", rows[0]["chinese"])

    def test_build_keeps_official_timeline_and_aligns_chinese(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            official = mv_dir / "source" / "source.ja.vtt"
            official.write_text(VTT, encoding="utf-8")
            with patch.object(common, "project_root", return_value=root):
                translation = prepare_official_subtitle(mv_dir)
            rows = self._rows(translation)
            rows[0]["chinese"] = "第一句"
            rows[1]["chinese"] = "第二句"
            self._write_rows(translation, rows)

            with patch.object(common, "project_root", return_value=root):
                candidate = build_official_candidate(mv_dir)
                master = build_subtitle(mv_dir, candidate)
            cues = load_cues(candidate)
            master_cues = load_cues(master)

            self.assertEqual((1.25, 3.5, "第一句", "最初の一行"), (cues[0].start, cues[0].end, cues[0].chinese, cues[0].japanese))
            self.assertEqual(cues, master_cues)
            self.assertTrue((mv_dir / "review" / "alignment" / "candidate-check-official.md").is_file())

    def test_build_rejects_changed_official_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            official = mv_dir / "source" / "source.ja.vtt"
            official.write_text(VTT, encoding="utf-8")
            with patch.object(common, "project_root", return_value=root):
                translation = prepare_official_subtitle(mv_dir)
            rows = self._rows(translation)
            rows[0]["start_ms"] = "1300"
            rows[0]["chinese"] = "第一句"
            rows[1]["chinese"] = "第二句"
            self._write_rows(translation, rows)

            with (
                patch.object(common, "project_root", return_value=root),
                self.assertRaisesRegex(ValueError, "只允许填写chinese列"),
            ):
                build_official_candidate(mv_dir)

    def test_master_promotion_rechecks_official_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            official = mv_dir / "source" / "source.ja.vtt"
            official.write_text(VTT, encoding="utf-8")
            with patch.object(common, "project_root", return_value=root):
                translation = prepare_official_subtitle(mv_dir)
            rows = self._rows(translation)
            rows[0]["chinese"] = "第一句"
            rows[1]["chinese"] = "第二句"
            self._write_rows(translation, rows)
            with patch.object(common, "project_root", return_value=root):
                candidate = build_official_candidate(mv_dir)
            candidate.write_text(
                candidate.read_text(encoding="utf-8-sig").replace(
                    "00:00:01,250",
                    "00:00:01,300",
                ),
                encoding="utf-8",
            )

            with (
                patch.object(common, "project_root", return_value=root),
                self.assertRaisesRegex(ValueError, "官方候选与冻结正文、时间轴或中文翻译不一致"),
            ):
                build_subtitle(mv_dir, candidate)

    @staticmethod
    def _rows(path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle, delimiter="\t"))

    @staticmethod
    def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()

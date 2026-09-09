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
    apply_official_translations,
    build_official_candidate,
    discover_official_subtitle,
    load_official_cues,
    prepare_netease_reference,
    prepare_official_subtitle,
)
from lyrics_source import Candidate
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

            self.assertEqual(
                (1250, 3500, "最初の一行"),
                (cues[0].start_ms, cues[0].end_ms, cues[0].japanese),
            )
            self.assertEqual(
                (4000, 6750, "次の一行"),
                (cues[1].start_ms, cues[1].end_ms, cues[1].japanese),
            )

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

            self.assertEqual(
                (1.25, 3.5, "第一句", "最初の一行"),
                (cues[0].start, cues[0].end, cues[0].chinese, cues[0].japanese),
            )
            self.assertEqual(cues, master_cues)
            self.assertTrue(
                (
                    mv_dir / "review" / "alignment" / "candidate-check-official.md"
                ).is_file()
            )

    def test_apply_chinese_only_file_preserves_frozen_official_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            official = mv_dir / "source" / "source.ja.vtt"
            official.write_text(VTT, encoding="utf-8")
            with patch.object(common, "project_root", return_value=root):
                prepare_official_subtitle(mv_dir)
            chinese = mv_dir / "review" / "official-subtitle" / "chinese.tsv"
            chinese.write_text(
                "line\tchinese\n1\t第一句\n2\t第二句\n",
                encoding="utf-8",
            )

            with patch.object(common, "project_root", return_value=root):
                translation = apply_official_translations(mv_dir, "GPT-5.6-Sol")

            source_rows = self._rows(
                mv_dir / "review" / "official-subtitle" / "source-cues.tsv"
            )
            translated_rows = self._rows(translation)
            self.assertEqual(
                [
                    {key: value for key, value in row.items() if key != "chinese"}
                    for row in source_rows
                ],
                [
                    {key: value for key, value in row.items() if key != "chinese"}
                    for row in translated_rows
                ],
            )
            self.assertEqual(
                ["第一句", "第二句"], [row["chinese"] for row in translated_rows]
            )
            self.assertEqual(
                "中文歌词来源：翻译模型（GPT-5.6-Sol）",
                (mv_dir / "subtitle" / "chinese-source.txt")
                .read_text(encoding="utf-8-sig")
                .strip(),
            )

    def test_build_accepts_audited_timeline_correction(self) -> None:
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

            with patch.object(common, "project_root", return_value=root):
                candidate = build_official_candidate(mv_dir)

            cues = load_cues(candidate)
            self.assertEqual(1.3, cues[0].start)
            report = (
                mv_dir / "review" / "alignment" / "candidate-check-official.md"
            ).read_text(encoding="utf-8")
            self.assertIn("相对官方字幕的时间修订　1条", report)

    def test_build_still_rejects_changed_official_japanese(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            official = mv_dir / "source" / "source.ja.vtt"
            official.write_text(VTT, encoding="utf-8")
            with patch.object(common, "project_root", return_value=root):
                translation = prepare_official_subtitle(mv_dir)
            rows = self._rows(translation)
            rows[0]["japanese"] = "改写正文"
            rows[0]["chinese"] = "第一句"
            rows[1]["chinese"] = "第二句"
            self._write_rows(translation, rows)

            with (
                patch.object(common, "project_root", return_value=root),
                self.assertRaisesRegex(ValueError, "冻结的行号或日文正文"),
            ):
                build_official_candidate(mv_dir)

    def test_build_rejects_overlapping_timing_correction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            official = mv_dir / "source" / "source.ja.vtt"
            official.write_text(VTT, encoding="utf-8")
            with patch.object(common, "project_root", return_value=root):
                translation = prepare_official_subtitle(mv_dir)
            rows = self._rows(translation)
            rows[0]["chinese"] = "第一句"
            rows[1]["start_ms"] = "3400"
            rows[1]["chinese"] = "第二句"
            self._write_rows(translation, rows)

            with (
                patch.object(common, "project_root", return_value=root),
                self.assertRaisesRegex(ValueError, "与上一条重叠"),
            ):
                build_official_candidate(mv_dir)

    def test_netease_reference_prefills_matching_chinese(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            official = mv_dir / "source" / "source.ja.vtt"
            official.write_text(VTT, encoding="utf-8")
            with patch.object(common, "project_root", return_value=root):
                prepare_official_subtitle(mv_dir)
                report = prepare_netease_reference(
                    mv_dir,
                    Candidate(
                        song={"id": 1, "name": "Song"},
                        original="[00:10.00]最初の一行\n[00:20.00]次の一行",
                        translation="[00:10.00]第一句\n[00:20.00]第二句",
                        match_score=12,
                        lyric_lines=2,
                        translated_lines=2,
                        coverage=1.0,
                        missing_timestamps=(),
                    ),
                )
                apply_official_translations(mv_dir)
                candidate = build_official_candidate(mv_dir)

            chinese_rows = self._rows(
                mv_dir / "review" / "official-subtitle" / "translation.tsv"
            )
            self.assertEqual(
                ["第一句", "第二句"], [row["chinese"] for row in chinese_rows]
            )
            self.assertEqual(
                "中文歌词来源：网易云音乐",
                (mv_dir / "subtitle" / "chinese-source.txt")
                .read_text(encoding="utf-8-sig")
                .strip(),
            )
            self.assertTrue(report.is_file())
            self.assertTrue(
                (
                    mv_dir
                    / "review"
                    / "official-subtitle"
                    / "netease-chinese.tsv"
                ).is_file()
            )
            candidate_cues = load_cues(candidate)
            self.assertEqual(
                (1.25, 4.0),
                (candidate_cues[0].start, candidate_cues[1].start),
            )

    def test_partial_netease_reference_records_hybrid_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            official = mv_dir / "source" / "source.ja.vtt"
            official.write_text(VTT, encoding="utf-8")
            with patch.object(common, "project_root", return_value=root):
                prepare_official_subtitle(mv_dir)
                prepare_netease_reference(
                    mv_dir,
                    Candidate(
                        song={"id": 1, "name": "Song"},
                        original="[00:10.00]最初の一行\n[00:20.00]次の一行",
                        translation="[00:10.00]第一句",
                        match_score=12,
                        lyric_lines=2,
                        translated_lines=1,
                        coverage=0.5,
                        missing_timestamps=(20000,),
                    ),
                )
            chinese = mv_dir / "review" / "official-subtitle" / "chinese.tsv"
            chinese.write_text(
                "line\tchinese\n1\t第一句\n2\t模型补译\n",
                encoding="utf-8",
            )

            with patch.object(common, "project_root", return_value=root):
                apply_official_translations(mv_dir, "GPT-5.6")

            self.assertEqual(
                "中文歌词来源：网易云音乐、翻译模型（GPT-5.6）",
                (mv_dir / "subtitle" / "chinese-source.txt")
                .read_text(encoding="utf-8-sig")
                .strip(),
            )

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
                self.assertRaisesRegex(
                    ValueError, "官方候选与审定工作表不一致"
                ),
            ):
                build_subtitle(mv_dir, candidate)

    @staticmethod
    def _rows(path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle, delimiter="\t"))

    def test_adjacent_lines_merge_as_netease_without_model_credit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            (mv_dir / "source" / "source.ja.vtt").write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n最初の一行次の一行\n",
                encoding="utf-8",
            )
            with patch.object(common, "project_root", return_value=root):
                prepare_official_subtitle(mv_dir)
                prepare_netease_reference(mv_dir, Candidate(
                    song={"id": 1, "name": "Song"},
                    original="[00:10.00]最初の一行\n[00:12.00]次の一行",
                    translation="[00:10.00]第一句\n[00:12.00]第二句",
                    match_score=12, lyric_lines=2, translated_lines=2,
                    coverage=1.0, missing_timestamps=(),
                ))
                rows = self._rows(mv_dir / "review/official-subtitle/chinese.tsv")
                self.assertEqual(rows[0]["chinese"], "第一句 第二句")
                # Joining punctuation is editing, not a new translation.
                rows[0]["chinese"] = "第一句，第二句"
                self._write_rows(mv_dir / "review/official-subtitle/chinese.tsv", rows)
                apply_official_translations(mv_dir)
                build_official_candidate(mv_dir)
            audit = self._rows(mv_dir / "review/official-subtitle/netease-match.tsv")
            self.assertEqual((audit[0]["netease_line"], audit[0]["netease_line_end"]), ("1", "2"))
            self.assertEqual(
                (mv_dir / "subtitle/chinese-source.txt").read_text(encoding="utf-8-sig").strip(),
                "中文歌词来源：网易云音乐",
            )

    def test_missing_second_translation_does_not_accept_prefix(self) -> None:
        from official_subtitle import OfficialCue, match_netease_reference

        candidate = Candidate(
            song={"id": 1, "name": "Song"},
            original="[00:10.00]最初の一行\n[00:12.00]次の一行",
            translation="[00:10.00]第一句", match_score=12, lyric_lines=2,
            translated_lines=1, coverage=0.5, missing_timestamps=(12000,),
        )
        match = match_netease_reference([OfficialCue(1000, 5000, "最初の一行次の一行")], candidate)[0]
        self.assertEqual(match.netease_line_end, 2)
        self.assertEqual(match.netease_chinese, "")

    @staticmethod
    def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()

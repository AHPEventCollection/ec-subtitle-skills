from __future__ import annotations

# ruff: noqa: E402

import csv
import sys
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import common
from alignment_workflow import (
    _reading_source,
    build_parser,
    build_sofa_candidate,
    prepare_alignment,
    preflight_alignment,
    validate_alignment_candidates,
)
from subtitle_io import Cue, render_srt


class AlignmentWorkflowTests(unittest.TestCase):
    def test_reading_source_applies_common_terms_and_pronoun_context(self) -> None:
        self.assertEqual(
            "ぜつめつきぐしゅのふたりときみはめまい",
            _reading_source("絶滅危惧種の2人と君は目眩"),
        )
        self.assertEqual("田中君は", _reading_source("田中君は"))

    def test_known_lyrics_default_to_pure_sofa(self) -> None:
        preflight = build_parser().parse_args(["preflight", "--mv-dir", "fixture"])
        validate = build_parser().parse_args(["validate", "--mv-dir", "fixture"])

        self.assertEqual("sofa", preflight.mode)
        self.assertEqual("sofa", validate.mode)

    def _workspace(self, root: Path, *, with_master: bool = False) -> Path:
        mv_dir = root / "workspace" / "mvs" / "fixture"
        with patch.object(common, "project_root", return_value=root):
            common.ensure_workspace(mv_dir)
        (mv_dir / "lyrics" / "original.lrc").write_text(
            "[00:01.00]最初の一行\n[00:04.00]次の一行\n",
            encoding="utf-8",
        )
        (mv_dir / "lyrics" / "chinese.lrc").write_text(
            "[00:01.00]第一句\n[00:04.00]第二句\n",
            encoding="utf-8",
        )
        if with_master:
            (mv_dir / "subtitle" / "master.srt").write_text(
                render_srt(
                    [
                        Cue(1, 3, "第一句", "最初の一行"),
                        Cue(4, 6, "第二句", "次の一行"),
                    ]
                ),
                encoding="utf-8",
            )
        return mv_dir

    def _prepare(self, root: Path, mv_dir: Path) -> Path:
        with (
            patch.object(common, "project_root", return_value=root),
            patch("alignment_workflow._reading", side_effect=("さいしょ", "つぎ")),
            patch("alignment_workflow._phones", side_effect=(["s", "a"], ["ts", "u"])),
        ):
            return prepare_alignment(mv_dir)

    def test_prepare_strips_all_timeline_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            audit = self._prepare(root, mv_dir)

            text = (mv_dir / "review" / "alignment" / "text-only.tsv").read_text(
                encoding="utf-8-sig"
            )
            header = text.splitlines()[0].split("\t")
            self.assertEqual(
                ["line", "role", "japanese", "chinese", "reading", "phones"],
                header,
            )
            self.assertNotIn("start", header)
            self.assertNotIn("timestamp", header)
            self.assertIn(
                "forbidden_fields_absent　true", audit.read_text(encoding="utf-8")
            )

    def test_sparse_pronunciation_override_requires_exact_lyric(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            (mv_dir / "lyrics" / "pronunciation-overrides.tsv").write_text(
                "line\tjapanese\treading\treason\n1\t別の歌詞\tべつ\t確認済み\n",
                encoding="utf-8",
            )
            with (
                patch.object(common, "project_root", return_value=root),
                self.assertRaisesRegex(ValueError, "正文与当前歌词不一致"),
            ):
                prepare_alignment(mv_dir)

    def test_sofa_candidate_excludes_ap_and_sp_from_body(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            self._prepare(root, mv_dir)
            htk = mv_dir / "work" / "sofa-input" / "htk" / "words" / "vocals.lab"
            htk.parent.mkdir(parents=True)
            htk.write_text(
                "0 1000000 AP\n1000000 2000000 s\n2000000 3000000 a\n"
                "3000000 4000000 SP\n4000000 5000000 ts\n5000000 6000000 u\n",
                encoding="utf-8",
            )
            with patch.object(common, "project_root", return_value=root):
                candidate = build_sofa_candidate(mv_dir, htk)

            self.assertTrue(candidate.is_file())
            report = candidate.with_name("sofa-conversion.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("AP静音　1", report)
            self.assertIn("SP静音　1", report)

    def test_sofa_candidate_rejects_unknown_body_phone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            self._prepare(root, mv_dir)
            htk = mv_dir / "work" / "sofa-input" / "htk" / "words" / "vocals.lab"
            htk.parent.mkdir(parents=True)
            htk.write_text("0 1000000 zzz\n", encoding="utf-8")
            with (
                patch.object(common, "project_root", return_value=root),
                self.assertRaisesRegex(ValueError, "音素与无时间输入不一致"),
            ):
                build_sofa_candidate(mv_dir, htk)

    def test_sofa_preflight_refuses_original_mix_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            self._prepare(root, mv_dir)
            (mv_dir / "source" / "source.mkv").write_bytes(b"source")

            with (
                patch.object(common, "project_root", return_value=root),
                self.assertRaisesRegex(FileNotFoundError, "禁止退回原始混音"),
            ):
                preflight_alignment(mv_dir, "sofa")

    def test_sofa_preflight_accepts_audio_only_vocals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            self._prepare(root, mv_dir)
            (mv_dir / "source" / "source.mkv").write_bytes(b"source")
            (mv_dir / "work" / "vocals.wav").write_bytes(b"audio-only")

            with (
                patch.object(common, "project_root", return_value=root),
                patch(
                    "alignment_workflow.probe_media",
                    return_value=SimpleNamespace(duration=10.0),
                ),
                patch("alignment_workflow._probe_duration", return_value=10.0),
            ):
                report = preflight_alignment(mv_dir, "sofa")

            self.assertIn("分离人声时长　10.000秒", report.read_text(encoding="utf-8"))

    def test_candidate_validation_detects_master_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root, with_master=True)
            self._prepare(root, mv_dir)
            review = mv_dir / "review" / "alignment"
            (review / "preflight-whisper.md").write_text("passed", encoding="utf-8")
            candidates = review / "candidates"
            candidates.mkdir()
            (candidates / "whisper-only.srt").write_text(
                render_srt([Cue(1, 3, "第一句", "最初の一行")]),
                encoding="utf-8",
            )
            (mv_dir / "subtitle" / "master.srt").write_text(
                render_srt([Cue(2, 4, "第二句", "次の一行")]),
                encoding="utf-8",
            )

            with (
                patch.object(common, "project_root", return_value=root),
                self.assertRaisesRegex(ValueError, "发生变化"),
            ):
                validate_alignment_candidates(mv_dir, "whisper")

    def test_compare_requires_mode_specific_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            self._prepare(root, mv_dir)
            review = mv_dir / "review" / "alignment"
            (review / "preflight-compare.md").write_text("passed", encoding="utf-8")
            candidates = review / "candidates"
            candidates.mkdir()
            value = render_srt([Cue(1, 3, "第一句", "最初の一行")])
            for name in ("whisper-only.srt", "sofa-only.srt", "combined.srt"):
                (candidates / name).write_text(value, encoding="utf-8")
            with (candidates / "three-way-comparison.tsv").open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.writer(handle, delimiter="\t")
                writer.writerow(
                    ("whisper_start", "sofa_start", "combined_start", "review_flags")
                )

            with patch.object(common, "project_root", return_value=root):
                report = validate_alignment_candidates(mv_dir, "compare")

            self.assertIn("人工复对　必需", report.read_text(encoding="utf-8"))

    def test_candidate_validation_rejects_unconfirmed_pronunciation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = self._workspace(root)
            self._prepare(root, mv_dir)
            review = mv_dir / "review" / "alignment"
            (review / "preflight-whisper.md").write_text("passed", encoding="utf-8")
            (review / "pronunciation-review.tsv").write_text(
                "line\tjapanese\treason\n1\tHeratbeat\t未确认读音\n",
                encoding="utf-8",
            )
            candidates = review / "candidates"
            candidates.mkdir()
            (candidates / "whisper-only.srt").write_text(
                render_srt([Cue(1, 3, "第一句", "最初の一行")]),
                encoding="utf-8",
            )

            with (
                patch.object(common, "project_root", return_value=root),
                self.assertRaisesRegex(ValueError, "未确认读音"),
            ):
                validate_alignment_candidates(mv_dir, "whisper")


if __name__ == "__main__":
    unittest.main()

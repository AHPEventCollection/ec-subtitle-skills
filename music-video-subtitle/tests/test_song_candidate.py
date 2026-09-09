from __future__ import annotations

# ruff: noqa: E402

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from song_candidate import (
    _fresh,
    _pending_pronunciations,
    _prepare_candidate_preview,
    _prepare_sofa,
    _sample_source_frames,
    _separate,
)


class SongCandidateTests(unittest.TestCase):
    def test_candidate_preview_pairs_full_source_with_same_named_srt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mv_dir = Path(temporary) / "workspace" / "mvs" / "fixture"
            source = mv_dir / "source" / "source.mkv"
            candidate = mv_dir / "review" / "alignment" / "candidates" / "sofa-only.srt"
            source.parent.mkdir(parents=True)
            candidate.parent.mkdir(parents=True)
            source.write_bytes(b"full-source")
            candidate.write_text(
                "\n".join(
                    [
                        "1",
                        "00:00:16,312 --> 00:00:20,587",
                        "第一句",
                        "最初の一行",
                        "",
                        "2",
                        "00:03:25,436 --> 00:03:31,000",
                        "第二句",
                        "次の一行",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            video, subtitle, report = _prepare_candidate_preview(
                mv_dir, source, candidate
            )

            self.assertEqual(video.parent, subtitle.parent)
            self.assertEqual(video.stem, subtitle.stem)
            self.assertEqual(b"full-source", video.read_bytes())
            self.assertEqual(candidate.read_bytes(), subtitle.read_bytes())
            self.assertIn(
                "播放器按同名文件自动加载SRT", report.read_text(encoding="utf-8")
            )
            self.assertIn("00:16.312至00:20.587", report.read_text(encoding="utf-8"))
            self.assertIn("03:25.436至03:31.000", report.read_text(encoding="utf-8"))

    def test_sofa_consumes_precomputed_phones_without_dictionary_g2p(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            review = mv_dir / "review" / "alignment"
            review.mkdir(parents=True)
            (review / "song.lab").write_text("s a SP ts u\n", encoding="utf-8")
            vocals = mv_dir / "work" / "vocals.wav"
            vocals.parent.mkdir(parents=True)
            vocals.write_bytes(b"vocals")
            python = root / "sofa" / "python.exe"
            model = root / "model" / "model.onnx"
            tool = root / "tool"
            python.parent.mkdir()
            model.parent.mkdir()
            tool.mkdir()
            python.write_bytes(b"python")
            model.write_bytes(b"model")
            (tool / "onnx_infer.py").write_text("# fixture\n", encoding="utf-8")
            manifest = {
                "profiles": {
                    "sofa": {
                        "python": str(python),
                        "artifacts": {
                            "sofa_model": str(model),
                            "sofa_tool": str(tool),
                        },
                    }
                },
                "shared": {},
            }
            captured: list[str] = []

            def fake_run(command: list[str], **_kwargs: object) -> None:
                captured.extend(command)
                htk = mv_dir / "work" / "sofa-input" / "htk" / "words" / "vocals.lab"
                htk.parent.mkdir(parents=True)
                htk.write_text("0 1000000 s\n", encoding="utf-8")

            with patch("song_candidate._run", side_effect=fake_run):
                _prepare_sofa(mv_dir, vocals, root, manifest)

            g2p_index = captured.index("--g2p")
            self.assertEqual("None", captured[g2p_index + 1])

    def test_separator_uses_bound_model_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "mix.wav"
            target = root / "vocals.wav"
            model = root / "models" / "bound.ckpt"
            python = root / "python.exe"
            audio.write_bytes(b"audio")
            model.parent.mkdir()
            model.write_bytes(b"model")
            captured: list[str] = []

            def fake_run(command: list[str], **_kwargs: object) -> None:
                captured.extend(command)
                (root / "vocals.pending.wav").write_bytes(b"vocals")

            with patch("song_candidate._run", side_effect=fake_run):
                _separate(
                    audio,
                    target,
                    python=python,
                    env={},
                    model=model,
                    use_autocast=True,
                )

            self.assertTrue(target.is_file())
            self.assertIn("--model_file_dir", captured)
            self.assertIn(str(model.parent), captured)
            self.assertIn("--use_autocast", captured)
            self.assertNotIn("--download_model_only", captured)

    def test_pending_pronunciations_counts_only_data_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mv_dir = Path(temporary)
            path = mv_dir / "review" / "alignment" / "pronunciation-review.tsv"
            path.parent.mkdir(parents=True)
            path.write_text(
                "line\tjapanese\treason\n1\tABC\t含英文\n",
                encoding="utf-8",
            )
            self.assertEqual(1, _pending_pronunciations(mv_dir))

    def test_visual_text_audit_is_bounded_to_twelve_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mv_dir = Path(temporary)
            source = mv_dir / "source.mkv"
            source.write_bytes(b"source")
            captured: list[str] = []

            def fake_run(command: list[str], **_kwargs: object) -> None:
                captured.extend(command)
                frames = mv_dir / "review" / "source-visual-audit" / "frames"
                (frames / "frame-001.jpg").write_bytes(b"frame")

            with patch("song_candidate._run", side_effect=fake_run):
                report = _sample_source_frames(mv_dir, source, "ffmpeg")

            self.assertIn("-frames:v", captured)
            self.assertIn("12", captured)
            self.assertIn("不因本项暂停", report.read_text(encoding="utf-8"))

    def test_fresh_requires_nonempty_output_newer_than_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            source.write_bytes(b"source")
            output.write_bytes(b"")
            self.assertFalse(_fresh(output, source))


if __name__ == "__main__":
    unittest.main()

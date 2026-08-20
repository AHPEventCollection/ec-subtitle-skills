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

from song_candidate import _fresh, _pending_pronunciations, _sample_source_frames, _separate


class SongCandidateTests(unittest.TestCase):
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
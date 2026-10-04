from __future__ import annotations

# ruff: noqa: E402

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from song_progress import (
    BAR_GAP,
    BAR_WIDTH,
    _subtitle_filter,
    merge_review_ass,
    render_final_video,
    render_song_progress_ass,
    review_windows,
    segment_bounds,
    song_progress_ass_failures,
    write_review_ass,
)


def sample_manifest(song_count: int = 3) -> dict:
    sections = []
    cursor = 2.0
    for index in range(song_count):
        sections.append(
            {
                "id": f"song_{index + 1:02d}",
                "type": "song",
                "title": f"Song {index + 1}",
                "artist": "Sample Artist",
                "start": cursor,
                "end": cursor + 12.0,
            }
        )
        cursor += 12.0
        if index + 1 < song_count:
            sections.append(
                {
                    "id": f"mc_{index + 1:02d}",
                    "type": "mc",
                    "title": f"MC {index + 1}",
                    "start": cursor,
                    "end": cursor + 3.0,
                }
            )
            cursor += 3.0
    return {
        "concert_id": "sample-concert",
        "duration_seconds": cursor,
        "sections": sections,
    }


class SongProgressTests(unittest.TestCase):
    def test_review_merges_caption_and_progress_into_one_ass(self) -> None:
        caption = (
            "[V4+ Styles]\n"
            "Style: Bilingual,Arial,57\n\n"
            "[Events]\n"
            "Dialogue: 0,0:00:00.00,0:00:01.00,Bilingual,,0,0,0,,字幕\n"
        )
        progress = (
            "[V4+ Styles]\n"
            "Style: ProgressDrawing,Arial,20\n\n"
            "[Events]\n"
            "Dialogue: 5,0:00:00.00,0:00:01.00,ProgressDrawing,,0,0,0,,图形\n"
        )

        merged = merge_review_ass(caption, progress)

        self.assertEqual(1, merged.count("[Events]"))
        self.assertIn("Style: Bilingual", merged)
        self.assertIn("Style: ProgressDrawing", merged)
        self.assertIn("字幕", merged)
        self.assertIn("图形", merged)

    def test_review_writes_one_external_ass_and_no_video(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            caption = root / "caption.ass"
            progress = root / "progress.ass"
            caption.write_text(
                "[V4+ Styles]\nStyle: Bilingual,Arial,57\n\n"
                "[Events]\nDialogue: 0,0:00:00.00,0:00:01.00,Bilingual,,0,0,0,,字幕\n",
                encoding="utf-8",
            )
            progress.write_text(
                "[V4+ Styles]\nStyle: ProgressDrawing,Arial,20\n\n"
                "[Events]\nDialogue: 5,0:00:00.00,0:00:01.00,ProgressDrawing,,0,0,0,,图形\n",
                encoding="utf-8",
            )
            with patch(
                "song_progress._video_inputs",
                return_value=(sample_manifest(1), source, caption, progress),
            ) as video_inputs:
                output = write_review_ass(root)

            review_dir = root / "run" / "review"
            self.assertEqual(output, review_dir / "song-progress-review.ass")
            self.assertEqual([], list(review_dir.glob("*.mp4")))
            self.assertEqual([], list(review_dir.glob("*.mkv")))
            self.assertEqual(1, len(list(review_dir.glob("*.ass"))))
            self.assertTrue((review_dir / "song-progress-review-windows.tsv").is_file())
            video_inputs.assert_called_once_with(root.resolve(), review=True)

    def test_final_render_keeps_original_media_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "original.mp4"
            caption = root / "caption.ass"
            progress = root / "progress.ass"
            with (
                patch(
                    "song_progress._video_inputs",
                    return_value=(sample_manifest(1), source, caption, progress),
                ) as video_inputs,
                patch("song_progress.resolve_binary", return_value=Path("ffmpeg")),
                patch("song_progress.subprocess.run") as run,
            ):
                render_final_video(root)

            video_inputs.assert_called_once_with(root.resolve())
            self.assertIn(str(source), run.call_args.args[0])

    def test_segments_fill_fixed_width_without_numeric_label(self) -> None:
        bounds = segment_bounds(17)
        widths = [end - start for start, end in bounds]

        self.assertEqual(17, len(bounds))
        self.assertLessEqual(max(widths) - min(widths), 1)
        self.assertEqual(
            BAR_WIDTH,
            sum(widths) + BAR_GAP * (len(bounds) - 1),
        )

        rendered = render_song_progress_ass(sample_manifest(17))
        self.assertNotIn("05/17", rendered)
        self.assertNotIn("%", rendered)
        self.assertIn(r"\t(0,12000,1,\fscx100)", rendered)

    def test_titles_and_bar_exist_only_inside_song_windows(self) -> None:
        rendered = render_song_progress_ass(sample_manifest(2))
        dialogue = [
            line for line in rendered.splitlines() if line.startswith("Dialogue:")
        ]

        self.assertIn("Song 1", rendered)
        self.assertIn("Sample Artist", rendered)
        self.assertTrue(
            all(line.split(",", 3)[1] != "0:00:14.00" for line in dialogue)
        )
        self.assertEqual(
            [(2.0, 6.0), (7.25, 8.75), (12.0, 14.0),
             (17.0, 21.0), (22.25, 23.75), (27.0, 29.0)],
            review_windows(sample_manifest(2)),
        )

    def test_graphics_windows_ignore_legacy_structure_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            concert = Path(temporary)
            manifest = sample_manifest(2)
            for section in manifest["sections"]:
                directory = concert / "run" / "sections" / section["id"]
                directory.mkdir(parents=True)
                (directory / "structure.tsv").write_text("obsolete structure", encoding="utf-8")
            self.assertEqual(review_windows(manifest), review_windows(manifest, concert))

    def test_stale_or_modified_layer_is_rejected(self) -> None:
        manifest = sample_manifest()
        rendered = render_song_progress_ass(manifest)

        self.assertEqual([], song_progress_ass_failures(manifest, rendered))
        self.assertEqual(
            ["missing_song_progress_ass"],
            song_progress_ass_failures(manifest, None),
        )
        self.assertEqual(
            ["song_progress_ass_stale_or_modified"],
            song_progress_ass_failures(manifest, rendered + "modified"),
        )

    def test_libass_renders_continuously_changing_bar(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg不可用")
        manifest = sample_manifest(1)
        manifest["sections"][0]["start"] = 0.0
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ass_path = root / "progress.ass"
            ass_path.write_text(
                render_song_progress_ass(manifest),
                encoding="utf-8",
                newline="",
            )
            caption_path = root / "caption.ass"
            caption_path.write_text(
                "[Script Info]\n"
                "ScriptType: v4.00+\n"
                "PlayResX: 1920\n"
                "PlayResY: 1080\n\n"
                "[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                "Style: Default,Arial,36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,40,40,40,1\n\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                "Dialogue: 0,0:00:00.00,0:00:12.00,Default,,0,0,0,,Caption\n",
                encoding="utf-8",
                newline="",
            )
            snapshots = []
            for timestamp in (7.5, 10.5):
                destination = root / f"bar-{timestamp}.png"
                command = [
                    ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=1920x1080:r=25:d=12",
                    "-vf",
                    (
                        _subtitle_filter(caption_path, ass_path)
                        + ",crop=760:6:80:62"
                    ),
                    "-ss",
                    str(timestamp),
                    "-frames:v",
                    "1",
                    "-y",
                    str(destination),
                ]
                subprocess.run(command, check=True)
                snapshots.append(hashlib.sha256(destination.read_bytes()).hexdigest())

        self.assertNotEqual(snapshots[0], snapshots[1])


if __name__ == "__main__":
    unittest.main()

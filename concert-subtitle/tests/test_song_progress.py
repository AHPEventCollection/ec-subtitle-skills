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
    active_segment_bounds,
    current_label,
    label_width,
    _subtitle_filter,
    merge_review_ass,
    render_final_video,
    render_song_progress_ass,
    review_windows,
    segment_bounds,
    song_progress_ass_failures,
    timeline_sections,
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
            source.write_bytes(b"source")
            caption.write_text("caption", encoding="utf-8")
            progress.write_text("progress", encoding="utf-8")
            destination = root / "rendered.mp4"
            with (
                patch(
                    "song_progress._video_inputs",
                    return_value=(sample_manifest(1), source, caption, progress),
                ) as video_inputs,
                patch("song_progress.resolve_binary", return_value=Path("ffmpeg")),
                patch("render_policy.default_destination", return_value=destination),
                patch("render_policy.select_encoder", return_value=("libx264", [])),
                patch("render_policy.verify_encoded_media", return_value={"format": {"duration": "14"}}),
                patch("song_progress.subprocess.run", side_effect=lambda *a, **k: destination.write_bytes(b"encoded")) as run,
            ):
                render_final_video(root)

            self.assertEqual(3, video_inputs.call_count)
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

    def test_labels_and_bar_cover_mc_and_transition_windows(self) -> None:
        rendered = render_song_progress_ass(sample_manifest(2))
        dialogue = [
            line for line in rendered.splitlines() if line.startswith("Dialogue:")
        ]

        self.assertIn("Song 1", rendered)
        self.assertNotIn("Sample Artist", rendered)
        self.assertTrue(any(line.split(",", 3)[1] == "0:00:14.00" for line in dialogue))
        for label in ("过场", "1.  Song 1", "MC1", "2.  Song 2"):
            self.assertEqual(1, sum(line.endswith("}" + label) for line in dialogue))
        self.assertEqual(4, sum(",ProgressLabel," in line for line in dialogue))
        self.assertNotIn("}曲", rendered)
        self.assertEqual(
            [(0.0, 6.0), (7.25, 8.75), (12.0, 21.0), (22.25, 23.75), (27.0, 29.0)],
            review_windows(sample_manifest(2)),
        )

    def test_explicit_recap_mv_and_independent_counters(self) -> None:
        manifest = sample_manifest(2)
        manifest["duration_seconds"] = 35
        manifest["presentation"] = {"timeline_extras": [
            {"type": "recap", "start": 0, "end": 2},
            {"type": "mv", "start": 30, "end": 35},
        ]}
        self.assertEqual(["回顾", "曲1", "MC1", "曲2", "过场", "MV"],
                         [item["label"] for item in timeline_sections(manifest)])
        self.assertEqual(3, len(manifest["sections"]))
        manifest["presentation"]["timeline_extras"][0]["end"] = 3
        with self.assertRaisesRegex(ValueError, "重叠"):
            render_song_progress_ass(manifest)

    def test_invalid_extra_intervals_and_types_are_rejected(self) -> None:
        for extra in ({"type": "song", "start": 0, "end": 1},
                      {"type": "recap", "start": 0, "end": float("nan")},
                      {"type": "mv", "start": 50, "end": 60}):
            manifest = sample_manifest(1)
            manifest["presentation"] = {"timeline_extras": [extra]}
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                render_song_progress_ass(manifest)

    def test_mc_only_timeline_is_supported(self) -> None:
        manifest = {"sections": [{"type": "mc", "start": 0, "end": 5}]}
        self.assertIn("}MC1", render_song_progress_ass(manifest))

    def test_current_cell_hugs_text_and_other_cells_fill_remaining_width(self) -> None:
        short = label_width("2.  口唇")
        long = label_width("1.  the other end of the globe")
        self.assertLess(short, long)
        for requested in (short, long, 9999):
            bounds = active_segment_bounds(30, 4, requested)
            self.assertEqual(0, bounds[0][0])
            self.assertEqual(1920, bounds[-1][1])
            if requested != 9999:
                self.assertEqual(requested, bounds[4][1] - bounds[4][0])
            self.assertTrue(all(b - a >= 4 for a, b in bounds))
        self.assertEqual("1.  Song", current_label({"type": "song", "label": "曲1", "title": "Song"}))
        self.assertEqual("MC1", current_label({"type": "mc", "label": "MC1", "title": "私有备注"}))
        self.assertEqual("MV  Song", current_label({"type": "mv", "label": "MV", "title": "Song（片尾MV）"}))

    def test_program_range_and_stable_id_overrides_preserve_business_types(self) -> None:
        manifest = {"duration_seconds": 100, "sections": [
            {"id": "promo", "type": "mc", "start": 0, "end": 5},
            {"id": "intro_00", "type": "mc", "start": 5, "end": 10},
            {"id": "song_01", "type": "song", "title": "One", "start": 10, "end": 30},
            {"id": "mc_01", "type": "mc", "start": 30, "end": 40},
            {"id": "bonus_17", "type": "song", "title": "Bonus", "start": 45, "end": 80},
        ], "presentation": {
            "timeline_range": {"start": 5, "end": 80},
            "section_kinds": {"intro_00": "recap", "bonus_17": "mv"},
        }}
        timeline = timeline_sections(manifest)
        self.assertEqual(["回顾", "曲1", "MC1", "过场", "MV"], [row["label"] for row in timeline])
        self.assertEqual((5, 80), (timeline[0]["start"], timeline[-1]["end"]))
        self.assertEqual("mc", manifest["sections"][1]["type"])
        self.assertEqual("song", manifest["sections"][-1]["type"])
        self.assertEqual((40, 45), (timeline[3]["start"], timeline[3]["end"]))
        rendered = render_song_progress_ass(manifest)
        self.assertNotIn("}曲2", rendered)
        self.assertNotIn("}MC2", rendered)
        self.assertNotIn("}Bonus", rendered)
        self.assertEqual(5, review_windows(manifest)[0][0])
        self.assertEqual(80, review_windows(manifest)[-1][1])
        manifest["presentation"]["section_kinds"]["missing"] = "recap"
        with self.assertRaisesRegex(ValueError, "section ID"):
            timeline_sections(manifest)
        del manifest["presentation"]["section_kinds"]["missing"]
        manifest["presentation"]["timeline_range"]["start"] = 6
        with self.assertRaisesRegex(ValueError, "截断"):
            timeline_sections(manifest)

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
                        + ",crop=1920:4:0:30"
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

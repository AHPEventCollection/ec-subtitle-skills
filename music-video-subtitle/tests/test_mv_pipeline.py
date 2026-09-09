from __future__ import annotations

# ruff: noqa: E402
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import common
from common import MediaInfo
from mv_pipeline import (
    _available_cover_sources,
    _encoder_arguments,
    _copy_ready,
    _preview_windows,
    _read_preview_windows,
    _require_fresh,
    acquire_lyrics,
    build_parser,
    build_subtitle,
    delivery_check,
    download_source,
    finish,
    initialize,
    main,
    preview,
    stage_delivery,
)
from chinese_source import record_chinese_source
from subtitle_io import (
    Cue,
    format_review_timestamp,
    horizontal_scale,
    load_cues,
    merge_lrc,
    parse_review_timestamp,
    render_srt,
)
from youtube_acquire import AcquisitionResult

SRT = r"""1
00:00:01,000 --> 00:00:03,000
第一句\N最初の一行

2
00:00:04,000 --> 00:00:07,000
这是一条更长的中文字幕\Nこれは長い日本語字幕の確認です
"""


class SubtitleIoTests(unittest.TestCase):
    def test_encoder_defaults_preserve_x264_and_nvenc_has_separate_quality(self):
        args = build_parser().parse_args(["finish", "--mv-dir", "fixture"])
        self.assertEqual(("libx264", "medium", 18), (args.encoder, args.preset, args.crf))
        self.assertEqual(["-c:v", "libx264", "-preset", "medium", "-crf", "18"],
                         _encoder_arguments(args.encoder, args.crf, args.preset, 18, 0))
        nvenc = _encoder_arguments("h264_nvenc", 18, "medium", 19, 1700)
        self.assertNotIn("-crf", nvenc)
        self.assertEqual("19", nvenc[nvenc.index("-cq") + 1])
        self.assertEqual("p7", nvenc[nvenc.index("-preset") + 1])
        self.assertEqual("1700k", nvenc[nvenc.index("-b:v") + 1])
        self.assertEqual("3400k", nvenc[nvenc.index("-maxrate") + 1])

    def test_invalid_nvenc_options_do_not_create_master(self):
        with tempfile.TemporaryDirectory() as temporary:
            mv = Path(temporary) / "workspace/mvs/fixture"
            with self.assertRaises(ValueError):
                finish(mv, encoder="h264_nvenc", nvenc_cq=52)
            self.assertFalse((mv / "subtitle/master.srt").exists())

    def test_review_timestamp_uses_minutes_and_keeps_legacy_seconds_readable(
        self,
    ) -> None:
        self.assertEqual("03:25.436", format_review_timestamp(205.436))
        self.assertEqual("01:02:03.004", format_review_timestamp(3723.004))
        self.assertAlmostEqual(205.436, parse_review_timestamp("03:25.436"))
        self.assertAlmostEqual(205.436, parse_review_timestamp("205.436"))

    def test_pipeline_defaults_machine_alignment_to_pure_sofa(self) -> None:
        preflight = build_parser().parse_args(
            ["alignment-preflight", "--mv-dir", "fixture"]
        )
        validate = build_parser().parse_args(
            ["alignment-validate", "--mv-dir", "fixture"]
        )

        self.assertEqual("sofa", preflight.mode)
        self.assertEqual("sofa", validate.mode)

    def test_srt_becomes_chinese_above_japanese(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "master.srt"
            path.write_text(SRT, encoding="utf-8")
            cues = load_cues(path)
            self.assertEqual("第一句", cues[0].chinese)
            self.assertEqual("最初の一行", cues[0].japanese)

    def test_wide_line_fails_below_readable_compression(self) -> None:
        self.assertEqual(100, horizontal_scale("短句", 38))
        with self.assertRaises(ValueError):
            horizontal_scale("长" * 80, 38)

    def test_netease_lrc_merges_translation_and_keeps_ascii_chant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.lrc"
            chinese = root / "chinese.lrc"
            original.write_text(
                "\n".join(
                    [
                        "[00:01.00]作词：某人",
                        "[00:02.00]最初の一行",
                        "[00:05.00]Oh",
                        "[00:07.00]次の一行",
                    ]
                ),
                encoding="utf-8",
            )
            chinese.write_text(
                "\n".join(
                    [
                        "[00:02.03]第一句",
                        "[00:07.00]下一句",
                    ]
                ),
                encoding="utf-8",
            )

            cues = merge_lrc(original, chinese, duration=12)

            self.assertEqual(3, len(cues))
            self.assertEqual(
                ("第一句", "最初の一行"), (cues[0].chinese, cues[0].japanese)
            )
            self.assertEqual(("Oh", "Oh"), (cues[1].chinese, cues[1].japanese))
            rendered = render_srt(cues)
            self.assertIn("下一句\n次の一行", rendered)

    def test_preview_checks_time_coverage_and_actual_luma(self) -> None:
        cues = [
            Cue(1, 2, "早", "早い"),
            Cue(5, 7, "这是最长的一句", "これは最も長い一行です"),
            Cue(10, 12, "晚", "遅い"),
        ]
        labels = {
            item["label"] for item in _preview_windows(cues, [10.0, 200.0, 80.0], 15)
        }
        self.assertEqual(
            {
                "early_sync",
                "middle_sync",
                "late_sync",
                "long_line",
                "bright_subtitle",
                "dark_subtitle",
                "subtitle_disappearance",
            },
            labels,
        )

    def test_preview_pairs_full_video_copy_with_external_ass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            with patch.object(common, "project_root", return_value=root):
                common.ensure_workspace(mv_dir)
            source = mv_dir / "source" / "source.mkv"
            source.write_bytes(b"source")
            master = mv_dir / "subtitle" / "master.srt"
            master.write_text(
                render_srt(
                    [
                        Cue(1, 2, "早", "早い"),
                        Cue(5, 7, "这是最长的一句", "これは最も長い一行です"),
                        Cue(10, 12, "晚", "遅い"),
                    ]
                ),
                encoding="utf-8",
            )
            styled = mv_dir / "work" / "fixture.styled.ass"
            styled.write_text("[Script Info]\n", encoding="utf-8")

            with (
                patch.object(common, "project_root", return_value=root),
                patch("mv_pipeline.resolve_binary", return_value=Path("ffmpeg")),
                patch(
                    "mv_pipeline.probe_media",
                    return_value=SimpleNamespace(duration=15.0),
                ),
                patch("mv_pipeline._sample_luma", side_effect=[10.0, 200.0, 80.0] * 2),
                patch("mv_pipeline.subprocess.run") as run,
            ):
                report = preview(mv_dir)
                paired = mv_dir / "review/preview/source.mkv"
                original_stat = paired.stat()
                sentinel = paired.parent / "user-notes.md"
                sentinel.write_text("keep")
                with patch("mv_pipeline.shutil.copy2", wraps=shutil.copy2) as copier:
                    preview(mv_dir)
                    self.assertNotIn(source, [call.args[0] for call in copier.call_args_list])
                self.assertEqual(original_stat.st_mtime_ns, paired.stat().st_mtime_ns)
                self.assertTrue(sentinel.is_file())

            preview_dir = mv_dir / "review" / "preview"
            video = preview_dir / "source.mkv"
            subtitle = preview_dir / "source.ass"
            self.assertTrue(video.is_file())
            self.assertTrue(subtitle.is_file())
            self.assertEqual(video.stem, subtitle.stem)
            self.assertEqual(b"source", video.read_bytes())
            self.assertEqual("[Script Info]\n", subtitle.read_text(encoding="utf-8"))
            self.assertIn("外挂ASS", report.read_text(encoding="utf-8"))
            self.assertIn("00:00.000至00:03.000", report.read_text(encoding="utf-8"))
            self.assertNotIn("0.000至3.000秒", report.read_text(encoding="utf-8"))
            window_text = (preview_dir / "preview-windows.tsv").read_text(
                encoding="utf-8"
            )
            self.assertIn("00:00.000\t00:03.000", window_text)
            windows = _read_preview_windows(preview_dir / "preview-windows.tsv")
            self.assertTrue(
                all(item["video"].endswith("source.mkv") for item in windows)
            )
            self.assertTrue(
                all(item["subtitle"].endswith("source.ass") for item in windows)
            )
            self.assertEqual(preview_dir, report.parent)
            self.assertFalse((mv_dir / "review" / "preview.md").exists())
            self.assertFalse((mv_dir / "review" / "preview-windows.tsv").exists())
            run.assert_not_called()

    def test_preview_rejects_fake_disappearance_at_video_end(self) -> None:
        cues = [Cue(1, 5, "末句", "最後の行")]
        with self.assertRaisesRegex(ValueError, "末句字幕延续到片尾"):
            _preview_windows(cues, [100.0], 5)

    def test_freshness_check_rejects_stale_derived_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "master.srt"
            derived = root / "styled.ass"
            source.write_text("new", encoding="utf-8")
            derived.write_text("old", encoding="utf-8")
            os.utime(derived, ns=(1_000_000_000, 1_000_000_000))
            os.utime(source, ns=(2_000_000_000, 2_000_000_000))

            with self.assertRaisesRegex(ValueError, "重新运行style"):
                _require_fresh(derived, [source], "请重新运行style")


class WorkspaceRoutingTests(unittest.TestCase):
    def test_project_root_routes_workspaces_outside_the_git_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "mv-project"
            expected = (project / "workspace" / "mvs").resolve()
            with patch.dict(
                os.environ, {"MV_SUBTITLE_PROJECT_ROOT": str(project)}, clear=False
            ):
                self.assertEqual(expected, common.workspace_root())
                self.assertEqual(
                    (expected / "clip").resolve(),
                    common.validate_mv_dir(expected / "clip"),
                )

    def test_standard_project_workspace_is_inferred_without_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "mv-project"
            mv_dir = project / "workspace" / "mvs" / "clip"
            with patch.dict(
                os.environ,
                {},
                clear=True,
            ):
                self.assertEqual(mv_dir.resolve(), common.validate_mv_dir(mv_dir))
                self.assertEqual(project.resolve(), common.project_root_for_mv(mv_dir))

    def test_explicit_workspace_root_overrides_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = (root / "custom-workspaces").resolve()
            environment = {
                "MV_SUBTITLE_PROJECT_ROOT": str(root / "project"),
                "MV_SUBTITLE_WORKSPACE_ROOT": str(expected),
            }
            with patch.dict(os.environ, environment, clear=False):
                self.assertEqual(expected, common.workspace_root())


class ArtifactWorkflowTests(unittest.TestCase):
    def test_netease_lyrics_records_public_chinese_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            with (
                patch.object(common, "project_root", return_value=root),
                patch(
                    "mv_pipeline.fetch_for_workspace",
                    return_value=SimpleNamespace(coverage=1.0, translation="译文"),
                ),
            ):
                initialize(mv_dir)
                acquire_lyrics(mv_dir)

            self.assertEqual(
                "中文歌词来源：网易云音乐",
                (mv_dir / "subtitle" / "chinese-source.txt")
                .read_text(encoding="utf-8-sig")
                .strip(),
            )

    def test_publish_copy_requires_source_credit_in_every_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "copy.md"
            credit = "中文歌词来源：翻译模型（GPT-5.6-Sol）"
            path.write_text(
                "\n\n".join(
                    [
                        f"## 微博\n有效文案{credit}",
                        f"## 小红书\n有效文案{credit}",
                        f"## B站\n有效文案{credit}",
                        "## 视频号\n有效文案但没有来源",
                    ]
                ),
                encoding="utf-8",
            )
            self.assertFalse(_copy_ready(path, credit))

    def test_build_subtitle_promotes_only_checked_alignment_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            with patch.object(common, "project_root", return_value=root):
                initialize(mv_dir)
                candidate_dir = mv_dir / "review" / "alignment" / "candidates"
                candidate_dir.mkdir(parents=True)
                candidate = candidate_dir / "sofa-only.srt"
                candidate.write_text(SRT, encoding="utf-8")
                (
                    mv_dir / "review" / "alignment" / "candidate-check-sofa.md"
                ).write_text("passed", encoding="utf-8")
                build_subtitle(mv_dir, candidate)

            review = (mv_dir / "subtitle" / "review.md").read_text(encoding="utf-8")
            self.assertIn("采用候选　sofa-only.srt", review)
            self.assertIn("画面是否包含原生歌词", review)
            self.assertIn("已逐句识别并对应正文、顺序、入点和切换时刻", review)

    def test_download_is_self_contained_and_requests_official_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            media = mv_dir / "source" / "source.mkv"
            media.parent.mkdir(parents=True)
            media.write_bytes(b"media")
            evidence = mv_dir / "review" / "source-acquisition.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text("{}\n", encoding="utf-8")
            acquired = AcquisitionResult(
                media=media.resolve(),
                facts={
                    "title": "测试歌曲",
                    "artist": "测试艺人",
                    "url": "https://example.invalid/official",
                    "file": str(media.resolve()),
                },
                ffprobe="X:/shared/bin/ffprobe.exe",
                strategy="ytdlp-global-auto",
                evidence=evidence,
            )

            media_info = MediaInfo(
                duration=180.0,
                width=1920,
                height=1080,
                video_codec="h264",
                frame_rate="24/1",
                pixel_format="yuv420p",
                color_space="bt709",
                color_transfer="bt709",
                color_primaries="bt709",
                audio_codec="aac",
                audio_channels=2,
                audio_sample_rate=48000,
            )
            with (
                patch.object(common, "project_root", return_value=root),
                patch("mv_pipeline.acquire_source", return_value=acquired) as acquire,
                patch("mv_pipeline.probe_media", return_value=media_info),
                patch(
                    "mv_pipeline.prepare_official_subtitle_if_present"
                ) as prepare_official,
                patch("mv_pipeline.acquire_official_lyrics_reference") as reference,
            ):
                prepare_official.return_value = (
                    mv_dir / "review" / "official-subtitle" / "translation.tsv"
                )
                result = download_source(
                    mv_dir,
                    "https://example.invalid/official",
                )

            self.assertEqual(media.resolve(), result)
            acquire.assert_called_once_with(
                mv_dir,
                "https://example.invalid/official",
                None,
            )
            source_report = (mv_dir / "source" / "source.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("获取策略　ytdlp-global-auto", source_report)
            self.assertEqual([evidence], list(mv_dir.rglob("*.json")))
            prepare_official.assert_called_once_with(mv_dir)
            reference.assert_called_once_with(mv_dir)

    def test_build_candidate_dispatches_to_bound_base_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mv_dir = Path(temporary) / "workspace" / "mvs" / "fixture"
            with (
                patch(
                    "mv_pipeline.dispatch_script_in_profile", return_value=7
                ) as dispatch,
                patch("mv_pipeline.build_song_candidate") as build,
            ):
                result = main(["build-candidate", "--mv-dir", str(mv_dir)])

            self.assertEqual(7, result)
            self.assertEqual(("song", "base"), dispatch.call_args.args[:2])
            build.assert_not_called()

    def test_media_commands_dispatch_to_bound_base_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mv_dir = Path(temporary) / "workspace" / "mvs" / "fixture"
            for action in (
                "import-source",
                "style",
                "preview",
                "finish",
                "render",
                "validate",
                "stage-delivery",
                "delivery-status",
                "delivery-check",
                "archive",
            ):
                arguments = [action, "--mv-dir", str(mv_dir)]
                if action == "import-source":
                    arguments.extend(["--source", str(Path(temporary) / "source.mkv")])
                if action == "archive":
                    arguments.extend(
                        ["--destination", str(Path(temporary) / "archive")]
                    )
                with patch(
                    "mv_pipeline.dispatch_script_in_profile", return_value=7
                ) as dispatch:
                    result = main(arguments)
                self.assertEqual(7, result)
                self.assertEqual(("media", "base"), dispatch.call_args.args[:2])

    def test_finish_runs_preview_render_and_validation_without_second_stop(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            with patch.object(common, "project_root", return_value=root):
                initialize(mv_dir)
            candidate = (
                mv_dir / "review" / "alignment" / "candidates" / "official-subtitle.srt"
            )
            candidate.parent.mkdir(parents=True)
            candidate.write_text(SRT, encoding="utf-8")
            (
                mv_dir / "review" / "alignment" / "candidate-check-official.md"
            ).write_text(
                "passed",
                encoding="utf-8",
            )
            report = mv_dir / "review" / "final-v01-machine-check.md"
            status = mv_dir / "output" / "delivery-status.v01.md"
            video = mv_dir / "output" / "fixture.hardsub.v01.mp4"
            video.write_bytes(b"mock-rendered-video")
            calls: list[str] = []

            with (
                patch.object(common, "project_root", return_value=root),
                patch("mv_pipeline.validate_official_candidate"),
                patch(
                    "mv_pipeline.style_subtitle",
                    side_effect=lambda *args: calls.append("style"),
                ),
                patch(
                    "mv_pipeline.preview",
                    side_effect=lambda *args: calls.append("preview"),
                ),
                patch(
                    "mv_pipeline.render",
                    side_effect=lambda *args: calls.append("render") or video,
                ),
                patch(
                    "mv_pipeline.validate",
                    side_effect=lambda *args: calls.append("validate") or report,
                ),
                patch(
                    "mv_pipeline.delivery_status",
                    side_effect=lambda *args: calls.append("delivery-status") or status,
                ),
            ):
                result = finish(mv_dir, candidate)

            self.assertEqual(status, result)
            self.assertTrue((mv_dir / "subtitle" / "master.srt").is_file())
            self.assertEqual(
                ["style", "preview", "render", "validate", "delivery-status"],
                calls,
            )

    def test_finish_stops_before_render_when_preview_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            with patch.object(common, "project_root", return_value=root):
                initialize(mv_dir)
            (mv_dir / "subtitle" / "master.srt").write_text(SRT, encoding="utf-8")

            with (
                patch.object(common, "project_root", return_value=root),
                patch("mv_pipeline.style_subtitle"),
                patch("mv_pipeline.preview", side_effect=ValueError("预览异常")),
                patch("mv_pipeline.render") as render,
                self.assertRaisesRegex(ValueError, "预览异常"),
            ):
                finish(mv_dir)

            render.assert_not_called()

    def test_initialize_creates_artifact_directories_without_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            with patch.object(common, "project_root", return_value=root):
                initialize(mv_dir)

            self.assertEqual(
                {"source", "lyrics", "subtitle", "work", "review", "output"},
                {path.name for path in mv_dir.iterdir() if path.is_dir()},
            )
            self.assertEqual([], list(mv_dir.rglob("*.json")))

    def test_video_thumbnail_is_not_eligible_as_cover(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            with patch.object(common, "project_root", return_value=root):
                initialize(mv_dir)
            release_art = mv_dir / "work" / "cover-source.jpg"
            thumbnail = mv_dir / "source" / "source.webp"
            Image.new("RGB", (1000, 1000), "red").save(release_art)
            Image.new("RGB", (1280, 720), "blue").save(thumbnail)

            candidates = _available_cover_sources(mv_dir)

            self.assertEqual([release_art], candidates)
            self.assertNotIn(thumbnail, candidates)

    def test_stage_delivery_promotes_ready_copy_and_official_cover(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            with patch.object(common, "project_root", return_value=root):
                initialize(mv_dir)
            output = mv_dir / "output"
            (output / "fixture.hardsub.v01.mp4").write_bytes(b"video")
            (output / "fixture.subtitle.v01.ass").write_text("ass", encoding="utf-8")
            record_chinese_source(mv_dir, "netease")
            (mv_dir / "work" / "publish-copy.md").write_text(
                "\n\n".join(
                    [
                        "## 微博\n微博正式发布文案，已经超过最小有效长度并且没有占位内容\n中文歌词来源：网易云音乐",
                        "## 小红书\n小红书正式发布文案，已经超过最小有效长度并且没有占位内容\n中文歌词来源：网易云音乐",
                        "## B站\nB站正式发布文案，已经超过最小有效长度并且没有占位内容\n中文歌词来源：网易云音乐",
                        "## 视频号\n视频号正式发布文案，已经超过最小有效长度并且没有占位内容\n中文歌词来源：网易云音乐",
                    ]
                ),
                encoding="utf-8",
            )
            Image.new("RGB", (1000, 1000), "red").save(
                mv_dir / "work" / "cover-source.jpg"
            )

            report = stage_delivery(mv_dir)

            publish_copy = output / "fixture.publish-copy.v01.md"
            cover = output / "fixture.cover.v01.jpg"
            self.assertTrue(report.is_file())
            self.assertTrue(publish_copy.is_file())
            self.assertEqual(
                4,
                publish_copy.read_text(encoding="utf-8").count(
                    "中文歌词来源：网易云音乐"
                ),
            )
            with Image.open(cover) as image:
                self.assertEqual("JPEG", image.format)
                self.assertGreaterEqual(image.width, 720)
                self.assertGreaterEqual(image.height, 720)

    def test_stage_delivery_respects_explicit_three_platform_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            with patch.object(common, "project_root", return_value=root):
                initialize(mv_dir)
            output = mv_dir / "output"
            (output / "fixture.hardsub.v01.mp4").write_bytes(b"video")
            (output / "fixture.subtitle.v01.ass").write_text("ass", encoding="utf-8")
            record_chinese_source(mv_dir, "netease")
            (mv_dir / "work" / "publish-platforms.txt").write_text(
                "微博\n小红书\nB站\n",
                encoding="utf-8",
            )
            (mv_dir / "work" / "publish-copy.md").write_text(
                "\n\n".join(
                    [
                        f"## {platform}\n正式发布文案已经超过最小有效长度且没有占位内容\n中文歌词来源：网易云音乐"
                        for platform in ("微博", "小红书", "B站")
                    ]
                ),
                encoding="utf-8",
            )
            Image.new("RGB", (1000, 1000), "red").save(
                mv_dir / "work" / "cover-source.jpg"
            )

            report = stage_delivery(mv_dir)

            publish_copy = output / "fixture.publish-copy.v01.md"
            self.assertTrue(report.is_file())
            self.assertTrue(publish_copy.is_file())
            self.assertNotIn("## 视频号", publish_copy.read_text(encoding="utf-8"))

    def test_stage_delivery_rejects_low_resolution_cover_without_upscaling(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            with patch.object(common, "project_root", return_value=root):
                initialize(mv_dir)
            output = mv_dir / "output"
            (output / "fixture.hardsub.v01.mp4").write_bytes(b"video")
            (output / "fixture.subtitle.v01.ass").write_text("ass", encoding="utf-8")
            record_chinese_source(mv_dir, "netease")
            (mv_dir / "work" / "publish-copy.md").write_text(
                "\n\n".join(
                    [
                        f"## {platform}\n正式发布文案已经超过最小有效长度且没有占位内容\n中文歌词来源：网易云音乐"
                        for platform in ("微博", "小红书", "B站", "视频号")
                    ]
                ),
                encoding="utf-8",
            )
            Image.new("RGB", (600, 600), "red").save(
                mv_dir / "work" / "cover-source.jpg"
            )

            with self.assertRaisesRegex(ValueError, "拒绝放大低清图片"):
                stage_delivery(mv_dir)

            self.assertFalse(list((mv_dir / "output").glob("*.cover.*")))

    def test_delivery_requires_combined_platform_copy_cover_and_human_check(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            with patch.object(common, "project_root", return_value=root):
                initialize(mv_dir)
                output = mv_dir / "output"
                (output / "fixture.hardsub.v01.mp4").write_bytes(b"video")
                (output / "fixture.subtitle.v01.ass").write_text(
                    "ass", encoding="utf-8"
                )
                (output / "fixture.publish-copy.v01.md").write_text(
                    "\n\n".join(
                        [
                            "## 微博\n微博正式发布文案，已经超过最小有效长度并且没有占位内容\n中文歌词来源：网易云音乐",
                            "## 小红书\n小红书正式发布文案，已经超过最小有效长度并且没有占位内容\n中文歌词来源：网易云音乐",
                            "## B站\nB站正式发布文案，已经超过最小有效长度并且没有占位内容\n中文歌词来源：网易云音乐",
                            "## 视频号\n视频号正式发布文案，已经超过最小有效长度并且没有占位内容\n中文歌词来源：网易云音乐",
                        ]
                    ),
                    encoding="utf-8",
                )
                Image.new("RGB", (1280, 720), "white").save(
                    output / "fixture.cover.v01.png"
                )
                (mv_dir / "source" / "source.md").write_text("source", encoding="utf-8")
                (mv_dir / "lyrics" / "lookup.md").write_text("lyrics", encoding="utf-8")
                (mv_dir / "subtitle" / "master.srt").write_text(SRT, encoding="utf-8")
                record_chinese_source(mv_dir, "netease")
                (mv_dir / "review" / "final-v01-machine-check.md").write_text(
                    "passed", encoding="utf-8"
                )
                (mv_dir / "review" / "final-v01-human-check.md").write_text(
                    "- [x] checked\n", encoding="utf-8"
                )

                report = delivery_check(mv_dir)

            self.assertTrue(report.is_file())
            self.assertNotIn("- [ ]", report.read_text(encoding="utf-8"))

    def test_delivery_rejects_combined_copy_missing_platform_section(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mv_dir = root / "workspace" / "mvs" / "fixture"
            with patch.object(common, "project_root", return_value=root):
                initialize(mv_dir)
                output = mv_dir / "output"
                (output / "fixture.hardsub.v01.mp4").write_bytes(b"video")
                (output / "fixture.subtitle.v01.ass").write_text(
                    "ass", encoding="utf-8"
                )
                (output / "fixture.publish-copy.v01.md").write_text(
                    "## 微博\n完整微博文案\n\n## 小红书\n完整小红书文案\n\n## B站\n完整B站文案"
                    * 4,
                    encoding="utf-8",
                )
                Image.new("RGB", (1280, 720), "white").save(
                    output / "fixture.cover.v01.png"
                )
                (mv_dir / "source" / "source.md").write_text("source", encoding="utf-8")
                (mv_dir / "lyrics" / "lookup.md").write_text("lyrics", encoding="utf-8")
                (mv_dir / "subtitle" / "master.srt").write_text(SRT, encoding="utf-8")
                record_chinese_source(mv_dir, "netease")
                (mv_dir / "review" / "final-v01-machine-check.md").write_text(
                    "passed", encoding="utf-8"
                )
                (mv_dir / "review" / "final-v01-human-check.md").write_text(
                    "- [x] checked\n", encoding="utf-8"
                )

                with self.assertRaisesRegex(ValueError, "缺少平台章节"):
                    delivery_check(mv_dir)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from review_checks import review_check_issues  # noqa: E402


class ReviewCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.mv = Path(self.temporary.name) / "fixture"
        (self.mv / "output").mkdir(parents=True)
        (self.mv / "review").mkdir()
        self.video = self.mv / "output/fixture.hardsub.v01.mp4"
        self.subtitle = self.mv / "output/fixture.subtitle.v01.ass"
        self.video.write_bytes(b"synthetic-video-fixture")
        self.subtitle.write_text("synthetic-subtitle-fixture", encoding="utf-8")
        self.machine = self.mv / "review/final-v01-machine-check.md"
        self.human = self.mv / "review/final-v01-human-check.md"
        # Synthetic review records exercise the parser; no user media is approved.
        self.machine_text = (
            f"# 成品机器检查v01\n\n- 成品　{self.video}\n"
            "- 完整音视频解码　通过\n- 画面尺寸　与源视频一致\n"
            "- 时长差异　不超过0.25秒\n- 音轨内容　与源视频一致\n"
        )
        self.human_text = (
            "# 成品人工检查v01\n\n"
            "- [x] 已播放开头、中段、结尾并确认时间轴没有整体偏移或后段漂移\n"
            "- [x] 已查看全部抽帧并确认中文与原文字体、颜色、位置和边缘正常\n"
            "- [x] 已确认最长字幕仍可阅读\n"
            "- [x] 已确认亮场和暗场字幕清楚\n"
            "- [x] 已确认最后一句正常消失\n"
            "- [x] 已确认音画同步和拖动播放正常\n"
            "- [x] 已确认封面原尺寸和缩小显示正常\n"
        )
        self.machine.write_text(self.machine_text, encoding="utf-8")
        self.human.write_text(self.human_text, encoding="utf-8")

    def test_complete_reports_are_accepted_without_rewriting_human_record(self):
        before = self.human.stat()
        self.assertEqual({"machine": [], "human": []}, review_check_issues(self.mv, 1))
        self.assertEqual(before.st_mtime_ns, self.human.stat().st_mtime_ns)
        self.assertEqual(self.human_text, self.human.read_text(encoding="utf-8"))

    def test_empty_or_arbitrary_report_text_does_not_pass(self):
        for value in ("", " \n", "passed", "- [x] checked\n"):
            with self.subTest(value=value):
                self.machine.write_text(value, encoding="utf-8")
                self.human.write_text(value, encoding="utf-8")
                issues = review_check_issues(self.mv, 1)
                self.assertTrue(issues["machine"])
                self.assertTrue(issues["human"])

    def test_each_required_human_item_must_be_present_and_checked_once(self):
        lines = self.human_text.splitlines(keepends=True)
        for line in (item for item in lines if item.startswith("- [x]")):
            for replacement in ("", line.replace("[x]", "[ ]"), line + line):
                with self.subTest(line=line, replacement=replacement):
                    self.human.write_text(self.human_text.replace(line, replacement), encoding="utf-8")
                    self.assertTrue(review_check_issues(self.mv, 1)["human"])

    def test_existing_japanese_wording_and_uppercase_x_remain_compatible(self):
        text = self.human_text.replace("中文与原文字体", "中文与日文字体").replace("[x]", "[X]")
        self.human.write_text(text, encoding="utf-8-sig")
        self.assertFalse(review_check_issues(self.mv, 1)["human"])

    def test_failed_missing_or_conflicting_machine_result_is_rejected(self):
        for text in (
            self.machine_text.replace("- 完整音视频解码　通过\n", ""),
            self.machine_text.replace("- 完整音视频解码　通过", "- 完整音视频解码　失败"),
            self.machine_text + "- 完整音视频解码　失败\n",
            self.machine_text.replace(str(self.video), str(self.video.with_name("other.mp4"))),
        ):
            with self.subTest(text=text):
                self.machine.write_text(text, encoding="utf-8")
                self.assertTrue(review_check_issues(self.mv, 1)["machine"])

    def test_previous_version_reports_cannot_confirm_current_delivery(self):
        self.machine.write_text(self.machine_text.replace("v01", "v00"), encoding="utf-8")
        self.human.write_text(self.human_text.replace("v01", "v00"), encoding="utf-8")
        issues = review_check_issues(self.mv, 1)
        self.assertTrue(issues["machine"])
        self.assertTrue(issues["human"])

    def test_updated_video_or_subtitle_invalidates_both_reports(self):
        for artifact in (self.video, self.subtitle):
            with self.subTest(artifact=artifact):
                old_time = artifact.stat().st_mtime_ns
                later = max(self.machine.stat().st_mtime_ns, self.human.stat().st_mtime_ns) + 1_000_000_000
                os.utime(artifact, ns=(later, later))
                issues = review_check_issues(self.mv, 1)
                self.assertTrue(any("检查后文件已更新" in issue for issue in issues["machine"]))
                self.assertTrue(any("检查后文件已更新" in issue for issue in issues["human"]))
                os.utime(artifact, ns=(old_time, old_time))

    def test_failed_revalidation_cannot_reuse_old_machine_pass(self):
        from mv_pipeline import validate

        with (
            patch("mv_pipeline.ensure_workspace", return_value=self.mv),
            patch("mv_pipeline.find_source_video", return_value=self.video),
            patch("mv_pipeline.resolve_binary", return_value=Path("ffmpeg")),
            patch("mv_pipeline.subprocess.run", return_value=SimpleNamespace(returncode=1, stderr="decode failed")),
            self.assertRaisesRegex(RuntimeError, "完整解码失败"),
        ):
            validate(self.mv)
        self.assertTrue(review_check_issues(self.mv, 1)["machine"])
        self.assertEqual(self.human_text, self.human.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

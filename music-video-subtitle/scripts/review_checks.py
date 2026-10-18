from __future__ import annotations

import re
from pathlib import Path


HUMAN_CHECK_ITEMS = (
    "已播放开头、中段、结尾并确认时间轴没有整体偏移或后段漂移",
    "已查看全部抽帧并确认中文与原文字体、颜色、位置和边缘正常",
    "已确认最长字幕仍可阅读",
    "已确认亮场和暗场字幕清楚",
    "已确认最后一句正常消失",
    "已确认音画同步和拖动播放正常",
    "已确认封面原尺寸和缩小显示正常",
)
MACHINE_CHECK_RESULTS = (
    "- 完整音视频解码　通过",
    "- 画面尺寸　与源视频一致",
    "- 时长差异　不超过0.25秒",
    "- 音轨内容　与源视频一致",
)


def _read_report(
    path: Path, title: str, artifacts: tuple[Path, ...],
) -> tuple[str, list[str]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
        report_time = path.stat().st_mtime_ns
    except (OSError, UnicodeError):
        return "", [f"检查报告缺失或不可读：{path.name}"]
    issues: list[str] = []
    lines = text.strip().splitlines()
    if not lines or lines[0].strip() != title:
        issues.append(f"检查报告为空或版本标题不符：{path.name}")
    for artifact in artifacts:
        if not artifact.is_file() or artifact.stat().st_size == 0:
            issues.append(f"被检查文件缺失或为空：{artifact.name}")
        elif artifact.stat().st_mtime_ns > report_time:
            issues.append(f"检查后文件已更新，需重新检查：{artifact.name}")
    return text, issues


def review_check_issues(mv_dir: Path, version: int) -> dict[str, list[str]]:
    """Read existing reports without creating or changing human review decisions."""
    video = mv_dir / "output" / f"{mv_dir.name}.hardsub.v{version:02d}.mp4"
    subtitle = mv_dir / "output" / f"{mv_dir.name}.subtitle.v{version:02d}.ass"
    artifacts = (video, subtitle)
    machine = mv_dir / "review" / f"final-v{version:02d}-machine-check.md"
    human = mv_dir / "review" / f"final-v{version:02d}-human-check.md"
    machine_text, machine_issues = _read_report(
        machine, f"# 成品机器检查v{version:02d}", artifacts,
    )
    machine_lines = [line.strip() for line in machine_text.splitlines()]
    for expected in MACHINE_CHECK_RESULTS:
        label = expected.split("　", 1)[0] + "　"
        if [line for line in machine_lines if line.startswith(label)] != [expected]:
            machine_issues.append(f"机器检查项缺失、重复或未通过：{expected[2:]}")
    targets = [line.removeprefix("- 成品　") for line in machine_lines
               if line.startswith("- 成品　")]
    if len(targets) != 1 or Path(targets[0]).resolve() != video.resolve():
        machine_issues.append("机器检查报告没有指向本版本成品")

    human_text, human_issues = _read_report(
        human, f"# 成品人工检查v{version:02d}", artifacts,
    )
    # Accept the original Japanese-only wording in existing review documents.
    human_text = human_text.replace("中文与日文字体", "中文与原文字体")
    checks: dict[str, list[str]] = {}
    for mark, label in re.findall(r"^\s*-\s+\[([ xX])\]\s+(.+?)\s*$", human_text, re.M):
        checks.setdefault(label, []).append(mark.lower())
        if mark == " ":
            human_issues.append(f"人工检查尚未确认：{label}")
    for expected in HUMAN_CHECK_ITEMS:
        if checks.get(expected) != ["x"]:
            human_issues.append(f"人工检查项缺失、重复或未确认：{expected}")
    return {"machine": machine_issues, "human": human_issues}

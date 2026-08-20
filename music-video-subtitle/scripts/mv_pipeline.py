from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from alignment_workflow import (
    preflight_alignment,
    prepare_alignment,
    validate_alignment_candidates,
)
from common import (
    VERSION,
    ensure_workspace,
    find_master_subtitle,
    find_source_video,
    find_styled_ass,
    latest_version,
    media_summary,
    next_version,
    probe_media,
    project_root_for_mv,
    resolve_binary,
    write_text,
)
from delivery_copy import copy_delivery
from lyrics_source import fetch_for_workspace
from official_subtitle import (
    build_official_candidate,
    prepare_official_subtitle,
    prepare_official_subtitle_if_present,
    validate_official_candidate,
)
from runtime_manager import dispatch_script_in_profile
from song_candidate import build_song_candidate
from subtitle_io import Cue, load_cues, render_ass, render_srt
from youtube_acquire import acquire_source

PUBLISH_COPY_SECTIONS = ("## 微博", "## 小红书", "## B站", "## 视频号")
COPY_PLACEHOLDERS = ("TODO", "待补", "在这里填写", "尚未完成")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MP4_COPY_AUDIO = {"aac", "mp3", "alac", "ac3", "eac3"}


def _filter_path(path: Path) -> str:
    return path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")


def initialize(mv_dir: Path) -> Path:
    return ensure_workspace(mv_dir)


def import_source(mv_dir: Path, source: Path) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"源视频不存在：{source}")
    existing = [
        path
        for path in (mv_dir / "source").iterdir()
        if path.is_file() and path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
    ]
    if existing:
        raise FileExistsError("source目录已有视频，拒绝覆盖")
    destination = mv_dir / "source" / f"source{source.suffix.lower()}"
    shutil.copy2(source, destination)
    info = probe_media(destination)
    write_text(
        mv_dir / "source" / "source.md",
        "\n".join(
            [
                "# MV片源",
                "",
                f"- 导入来源　{source}",
                *media_summary(info),
                "",
            ]
        ),
    )
    return destination


def download_source(
    mv_dir: Path,
    url: str,
    runtime_root: str | None = None,
) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    source_dir = mv_dir / "source"
    acquired = acquire_source(mv_dir, url, runtime_root)
    media = acquired.media
    facts = acquired.facts
    info = probe_media(media, acquired.ffprobe)
    title = facts.get("title", "")
    artist = facts.get("artist", "")
    write_text(source_dir / "title.txt", title + "\n")
    write_text(source_dir / "artist.txt", artist + "\n")
    write_text(
        source_dir / "source.md",
        "\n".join(
            [
                "# MV片源",
                "",
                f"- 官方页面　{facts.get('url', url)}",
                f"- 标题　{title}",
                f"- 艺人或频道　{artist}",
                f"- 获取策略　{acquired.strategy}",
                f"- 取源证据　{acquired.evidence}",
                *media_summary(info),
                "",
                "片源选择以最终压制兼容为前提。检测到HDR时，压制前必须更换SDR片源或明确制定转色方案",
                "",
            ]
        ),
    )
    prepare_official_subtitle_if_present(mv_dir)
    return media


def acquire_lyrics(mv_dir: Path, cookie_file: Path | None = None) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    candidate = fetch_for_workspace(mv_dir, cookie_file)
    if candidate.coverage < 1.0 or not candidate.translation:
        raise ValueError(
            "网易云候选没有完整日中双语覆盖，已保留查询结果，请补齐chinese.lrc后继续"
        )
    return mv_dir / "lyrics" / "lookup.md"


def build_subtitle(mv_dir: Path, candidate: Path) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    destination = mv_dir / "subtitle" / "master.srt"
    if destination.exists():
        raise FileExistsError(f"审定字幕已存在，拒绝覆盖：{destination}")
    candidate = candidate.resolve()
    candidate_root = (mv_dir / "review" / "alignment" / "candidates").resolve()
    if candidate.parent != candidate_root or candidate.suffix.casefold() != ".srt":
        raise ValueError(f"候选必须是固定候选目录中的SRT：{candidate_root}")
    if not candidate.is_file():
        raise FileNotFoundError(f"候选不存在：{candidate}")
    checks = sorted((mv_dir / "review" / "alignment").glob("candidate-check-*.md"))
    if not checks:
        raise FileNotFoundError("候选尚未通过alignment-validate")
    if candidate.name == "official-subtitle.srt":
        validate_official_candidate(mv_dir, candidate)
    cues = load_cues(candidate)
    write_text(destination, render_srt(cues))
    write_text(
        mv_dir / "subtitle" / "review.md",
        "\n".join(
            [
                "# 双语字幕审定",
                "",
                f"- 采用候选　{candidate.name}",
                f"- 字幕事件　{len(cues)}",
                "- [ ] 已人工复听全部候选并明确选择当前版本",
                "- [ ] 已对照MV实际演唱检查改词、重复和语气词",
                "- [ ] 已检查画面是否包含原生歌词、逐字歌词或卡拉OK歌词",
                "- [ ] 画面含歌词时，已逐句识别并对应正文、顺序、入点和切换时刻",
                "- [ ] 已检查开头、中段、结尾和长间奏后的同步",
                "- [ ] 已检查中文含义、重复副歌和专有名词",
                "- [ ] 已检查最后一句消失时间",
                "",
                "机器候选只提供可编辑起点，完成全部人工检查后master.srt才是审定字幕",
                "",
            ]
        ),
    )
    return destination


def style_subtitle(mv_dir: Path, ffprobe_value: str | None = None) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    source = find_source_video(mv_dir)
    subtitle = find_master_subtitle(mv_dir)
    info = probe_media(source, ffprobe_value)
    title_path = mv_dir / "source" / "title.txt"
    title = (
        title_path.read_text(encoding="utf-8-sig").strip()
        if title_path.is_file()
        else mv_dir.name
    )
    play_res_x, play_res_y = info.play_res
    output = mv_dir / "work" / f"{mv_dir.name}.styled.ass"
    write_text(
        output,
        render_ass(
            load_cues(subtitle),
            title,
            play_res_x=play_res_x,
            play_res_y=play_res_y,
        ),
    )
    return output


def _sample_luma(source: Path, timestamp: float, ffmpeg: Path) -> float:
    width, height = 160, 64
    completed = subprocess.run(
        [
            str(ffmpeg),
            "-nostdin",
            "-v",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(source),
            "-vf",
            f"crop=iw:ih*0.38:0:ih*0.62,scale={width}:{height},format=gray",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    if len(completed.stdout) != width * height:
        raise RuntimeError(f"无法在{timestamp:.3f}秒采样字幕区域亮度")
    return sum(completed.stdout) / len(completed.stdout)


def _preview_windows(
    cues: list[Cue],
    luma: list[float],
    duration: float,
) -> list[dict[str, Any]]:
    if len(cues) != len(luma):
        raise ValueError("字幕事件和亮度样本数量不一致")
    longest = max(cues, key=lambda cue: len(cue.chinese) + len(cue.japanese))
    bright_index = max(range(len(luma)), key=luma.__getitem__)
    dark_index = min(range(len(luma)), key=luma.__getitem__)
    selected = [
        ("early_sync", cues[0]),
        ("middle_sync", cues[len(cues) // 2]),
        ("late_sync", cues[-1]),
        ("long_line", longest),
        ("bright_subtitle", cues[bright_index]),
        ("dark_subtitle", cues[dark_index]),
    ]
    windows: list[dict[str, Any]] = []
    for label, cue in selected:
        center = (cue.start + cue.end) / 2
        windows.append(
            {
                "label": label,
                "start": max(0.0, center - 1.5),
                "end": min(duration, center + 1.5),
            }
        )
    disappearance = cues[-1].end
    if disappearance >= duration - 0.2:
        raise ValueError(
            "末句字幕延续到片尾，无法检查消失画面；请先在master.srt中审定末句出点"
        )
    windows.append(
        {
            "label": "subtitle_disappearance",
            "start": max(0.0, disappearance - 1.2),
            "end": min(duration, disappearance + 1.2),
        }
    )
    return windows


def _require_fresh(output: Path, inputs: list[Path], instruction: str) -> None:
    newest_input = max(path.stat().st_mtime_ns for path in inputs)
    if output.stat().st_mtime_ns < newest_input:
        raise ValueError(instruction)


def preview(mv_dir: Path, ffmpeg_value: str | None = None) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    source = find_source_video(mv_dir)
    styled = find_styled_ass(mv_dir)
    master = find_master_subtitle(mv_dir)
    _require_fresh(styled, [source, master], "标准ASS已过期，请先重新运行style")
    cues = load_cues(master)
    ffmpeg = resolve_binary("ffmpeg", ffmpeg_value)
    media = probe_media(source)
    duration = media.duration
    luma = [_sample_luma(source, (cue.start + cue.end) / 2, ffmpeg) for cue in cues]
    windows = _preview_windows(cues, luma, duration)
    preview_dir = mv_dir / "review" / "preview"
    if preview_dir.exists():
        shutil.rmtree(preview_dir)
    preview_dir.mkdir(parents=True)
    for legacy_path in (
        mv_dir / "review" / "preview.md",
        mv_dir / "review" / "preview-windows.tsv",
    ):
        legacy_path.unlink(missing_ok=True)
    preview_video = preview_dir / source.name
    preview_ass = preview_dir / f"{source.stem}.ass"
    shutil.copy2(source, preview_video)
    shutil.copy2(styled, preview_ass)
    rows = ["label\tstart\tend\tvideo\tsubtitle"]
    report = [
        "# 字幕预览",
        "",
        f"- 完整视频　{preview_video}",
        f"- 同名外挂ASS　{preview_ass}",
        "- 视频保持源文件内容，不重新编码，不封装字幕轨",
        "",
    ]
    for item in windows:
        label = str(item["label"])
        start = float(item["start"])
        end = float(item["end"])
        rows.append(
            f"{label}\t{start:.3f}\t{end:.3f}\t{preview_video}\t{preview_ass}"
        )
        report.append(f"- {label}　{start:.3f}至{end:.3f}秒")
    report.extend(
        [
            "",
            "用播放器打开完整视频并加载同名ASS，按时间点检查同步、长句、明暗背景和字幕消失。调整时间轴后重新运行style和preview。生成预览材料本身不代表已经通过",
            "",
        ]
    )
    write_text(preview_dir / "preview-windows.tsv", "\n".join(rows) + "\n")
    report_path = preview_dir / "preview.md"
    write_text(report_path, "\n".join(report))
    return report_path

def render(
    mv_dir: Path,
    ffmpeg_value: str | None = None,
    ffprobe_value: str | None = None,
    crf: int = 18,
    preset: str = "medium",
) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    source = find_source_video(mv_dir)
    styled = find_styled_ass(mv_dir)
    master = find_master_subtitle(mv_dir)
    preview_report = mv_dir / "review" / "preview" / "preview.md"
    if not preview_report.is_file():
        raise FileNotFoundError("缺少预览，请先生成并观看预览")
    _require_fresh(styled, [source, master], "标准ASS已过期，请重新运行style和preview")
    _require_fresh(
        preview_report,
        [source, styled],
        "预览已过期，请重新运行preview并查看新预览",
    )
    info = probe_media(source, ffprobe_value)
    if info.hdr:
        raise ValueError("检测到HDR片源，当前流程不会盲目转为SDR，请更换SDR片源或先制定转色方案")
    if info.audio_codec and info.audio_codec not in MP4_COPY_AUDIO:
        raise ValueError(
            f"源音频{info.audio_codec}不适合直接复制到通用MP4，请更换兼容音轨片源"
        )
    output_dir = mv_dir / "output"
    version = next_version(output_dir, mv_dir.name)
    video = output_dir / f"{mv_dir.name}.hardsub.v{version:02d}.mp4"
    final_ass = output_dir / f"{mv_dir.name}.subtitle.v{version:02d}.ass"
    partial = video.with_name(f".{video.name}.partial.mp4")
    try:
        subprocess.run(
            [
                str(resolve_binary("ffmpeg", ffmpeg_value)),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                f"subtitles=filename='{_filter_path(styled)}'",
                "-c:v",
                "libx264",
                "-preset",
                preset,
                "-crf",
                str(crf),
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(partial),
            ],
            check=True,
        )
        os.replace(partial, video)
        shutil.copy2(styled, final_ass)
    finally:
        partial.unlink(missing_ok=True)
    return video


def _audio_hash(path: Path, ffmpeg: Path) -> str | None:
    completed = subprocess.run(
        [
            str(ffmpeg),
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    line = completed.stdout.strip()
    return line.split("=", 1)[1] if "=" in line else None


def _read_preview_windows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError("缺少review/preview/preview-windows.tsv")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines:
        raise ValueError("review/preview/preview-windows.tsv为空")
    headers = lines[0].split("\t")
    required = {"label", "start", "end", "video", "subtitle"}
    if not required.issubset(headers):
        raise ValueError("review/preview/preview-windows.tsv字段不完整")
    windows: list[dict[str, Any]] = []
    for line in lines[1:]:
        row = dict(zip(headers, line.split("\t"), strict=True))
        windows.append(
            {
                "label": row["label"],
                "start": float(row["start"]),
                "end": float(row["end"]),
                "video": row["video"],
                "subtitle": row["subtitle"],
            }
        )
    return windows


def _extract_frame(video: Path, timestamp: float, destination: Path, ffmpeg: Path) -> None:
    subprocess.run(
        [
            str(ffmpeg),
            "-nostdin",
            "-v",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(destination),
        ],
        check=True,
    )


def validate(
    mv_dir: Path,
    video: Path | None = None,
    ffmpeg_value: str | None = None,
    ffprobe_value: str | None = None,
) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    source = find_source_video(mv_dir)
    version = latest_version(mv_dir / "output", mv_dir.name)
    final = video.resolve() if video else mv_dir / "output" / f"{mv_dir.name}.hardsub.v{version:02d}.mp4"
    ffmpeg = resolve_binary("ffmpeg", ffmpeg_value)
    completed = subprocess.run(
        [
            str(ffmpeg),
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(final),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or completed.stderr.strip():
        raise RuntimeError("成品完整解码失败：" + completed.stderr.strip())
    source_info = probe_media(source, ffprobe_value)
    final_info = probe_media(final, ffprobe_value)
    problems: list[str] = []
    if (source_info.width, source_info.height) != (final_info.width, final_info.height):
        problems.append("画面尺寸与源视频不一致")
    if abs(source_info.duration - final_info.duration) > 0.25:
        problems.append("成品时长与源视频差异超过0.25秒")
    if source_info.audio_codec and not final_info.audio_codec:
        problems.append("成品缺少音轨")
    if _audio_hash(source, ffmpeg) != _audio_hash(final, ffmpeg):
        problems.append("音轨内容与源视频不一致")
    if problems:
        raise RuntimeError("；".join(problems))
    frames_dir = mv_dir / "review" / f"final-v{version:02d}-frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)
    windows = _read_preview_windows(
        mv_dir / "review" / "preview" / "preview-windows.tsv"
    )
    frame_lines: list[str] = []
    for item in windows:
        center = (item["start"] + item["end"]) / 2
        if item["label"] == "subtitle_disappearance":
            for suffix, timestamp in (("before", center - 0.15), ("after", center + 0.15)):
                frame = frames_dir / f"{item['label']}-{suffix}.jpg"
                _extract_frame(final, timestamp, frame, ffmpeg)
                frame_lines.append(f"- {item['label']}-{suffix}　{timestamp:.3f}秒　{frame}")
        else:
            frame = frames_dir / f"{item['label']}.jpg"
            _extract_frame(final, center, frame, ffmpeg)
            frame_lines.append(f"- {item['label']}　{center:.3f}秒　{frame}")
    report = mv_dir / "review" / f"final-v{version:02d}-machine-check.md"
    write_text(
        report,
        "\n".join(
            [
                f"# 成品机器检查v{version:02d}",
                "",
                "- 完整音视频解码　通过",
                "- 画面尺寸　与源视频一致",
                "- 时长差异　不超过0.25秒",
                "- 音轨内容　与源视频一致",
                "",
                "## 待人工查看画面",
                "",
                *frame_lines,
                "",
                "机器检查只证明文件结构和音频内容。以下人工检查完成前，不得声明成品通过",
                "",
            ]
        ),
    )
    human = mv_dir / "review" / f"final-v{version:02d}-human-check.md"
    if not human.exists():
        write_text(
            human,
            "\n".join(
                [
                    f"# 成品人工检查v{version:02d}",
                    "",
                    "- [ ] 已播放开头、中段、结尾并确认时间轴没有整体偏移或后段漂移",
                    "- [ ] 已查看全部抽帧并确认中日文字体、颜色、位置和边缘正常",
                    "- [ ] 已确认最长字幕仍可阅读",
                    "- [ ] 已确认亮场和暗场字幕清楚",
                    "- [ ] 已确认最后一句正常消失",
                    "- [ ] 已确认音画同步和拖动播放正常",
                    "- [ ] 已确认封面原尺寸和缩略图正常",
                    "",
                ]
            ),
        )
    return report


def cover_candidates(
    mv_dir: Path,
    ffmpeg_value: str | None = None,
    ffprobe_value: str | None = None,
) -> Path:
    from PIL import Image, ImageDraw, ImageOps

    mv_dir = ensure_workspace(mv_dir)
    source = find_source_video(mv_dir)
    info = probe_media(source, ffprobe_value)
    ffmpeg = resolve_binary("ffmpeg", ffmpeg_value)
    destination = mv_dir / "review" / "cover-candidates"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    timestamps = [info.duration * fraction for fraction in (0.12, 0.30, 0.50, 0.70, 0.88)]
    images: list[Path] = []
    for index, timestamp in enumerate(timestamps, start=1):
        image = destination / f"{index:02d}-{timestamp:.3f}s.jpg"
        _extract_frame(source, timestamp, image, ffmpeg)
        images.append(image)
    sheet = Image.new("RGB", (1280, 1080), "black")
    draw = ImageDraw.Draw(sheet)
    for index, image_path in enumerate(images):
        with Image.open(image_path) as opened:
            thumb = ImageOps.fit(opened.convert("RGB"), (640, 360))
        x = (index % 2) * 640
        y = (index // 2) * 360
        sheet.paste(thumb, (x, y))
        draw.rectangle((x, y, x + 185, y + 28), fill=(0, 0, 0))
        draw.text((x + 8, y + 6), image_path.stem, fill=(255, 255, 255))
    contact = destination / "contact-sheet.jpg"
    sheet.save(contact, quality=92)
    write_text(
        destination / "index.md",
        "\n".join(
            [
                "# 封面候选",
                "",
                *[f"- {path}" for path in images],
                "",
                f"- 接触表　{contact}",
                "",
                "查看原图后选择主帧，再用确定性图片编辑完成裁切、调色和标题排版。禁止生成画面、补绘人物和AI换脸",
                "",
            ]
        ),
    )
    return contact


def _copy_ready(path: Path) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8-sig").strip()
    return (
        len(text) >= 80
        and not any(marker in text for marker in COPY_PLACEHOLDERS)
        and all(section in text for section in PUBLISH_COPY_SECTIONS)
    )


def delivery_check(mv_dir: Path) -> Path:
    from PIL import Image

    mv_dir = ensure_workspace(mv_dir)
    version = latest_version(mv_dir / "output", mv_dir.name)
    output = mv_dir / "output"
    required = [
        output / f"{mv_dir.name}.hardsub.v{version:02d}.mp4",
        output / f"{mv_dir.name}.subtitle.v{version:02d}.ass",
        output / f"{mv_dir.name}.publish-copy.v{version:02d}.md",
    ]
    missing = [path for path in required if not path.is_file()]
    publish_copy = required[2]
    incomplete_copy = publish_copy.is_file() and not _copy_ready(publish_copy)
    covers = [
        path
        for path in output.glob(f"{mv_dir.name}.cover.v{version:02d}.*")
        if path.suffix.lower() in IMAGE_SUFFIXES
    ]
    if len(covers) != 1:
        missing.append(output / f"{mv_dir.name}.cover.v{version:02d}.png")
    if missing:
        raise FileNotFoundError("交付缺少文件：" + "、".join(str(path) for path in missing))
    if incomplete_copy:
        raise ValueError("合并文案为空、仍有占位内容或缺少平台章节：" + str(publish_copy))
    with Image.open(covers[0]) as cover:
        if cover.width < 720 or cover.height < 720:
            raise ValueError(f"封面尺寸不足：{cover.width}x{cover.height}")
    human = mv_dir / "review" / f"final-v{version:02d}-human-check.md"
    if not human.is_file() or "- [ ]" in human.read_text(encoding="utf-8-sig"):
        raise ValueError("成品人工检查尚未全部勾选")
    if not (mv_dir / "review" / f"final-v{version:02d}-machine-check.md").is_file():
        raise FileNotFoundError("缺少成品机器检查报告")
    for path in (
        mv_dir / "source" / "source.md",
        mv_dir / "lyrics" / "lookup.md",
        mv_dir / "subtitle" / "master.srt",
    ):
        if not path.is_file():
            raise FileNotFoundError(f"项目缺少可复用来源文件：{path}")
    report = output / f"delivery-check.v{version:02d}.md"
    write_text(
        report,
        "\n".join(
            [
                f"# MV交付检查v{version:02d}",
                "",
                "- 硬字幕MP4　齐全",
                "- 同版本ASS　齐全",
                "- 四平台合并文案　齐全",
                "- 真实画面封面　齐全",
                "- 机器检查　通过",
                "- 人工检查　通过",
                "",
                "本地source、lyrics、subtitle和review目录继续保留，用于以后修订",
                "",
            ]
        ),
    )
    return report


def archive_delivery(mv_dir: Path, destination: Path) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    delivery_check(mv_dir)
    return copy_delivery(mv_dir / "output", destination)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="单曲MV无状态完整工作流")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="action", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--mv-dir", required=True, type=Path)

    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("--mv-dir", required=True, type=Path)
    download_parser.add_argument("--url", required=True)
    download_parser.add_argument("--runtime-root")

    import_parser = subparsers.add_parser("import-source")
    import_parser.add_argument("--mv-dir", required=True, type=Path)
    import_parser.add_argument("--source", required=True, type=Path)

    lyrics_parser = subparsers.add_parser("lyrics")
    lyrics_parser.add_argument("--mv-dir", required=True, type=Path)
    lyrics_parser.add_argument("--netease-cookie-file", type=Path)

    official_prepare_parser = subparsers.add_parser("official-subtitle-prepare")
    official_prepare_parser.add_argument("--mv-dir", required=True, type=Path)
    official_prepare_parser.add_argument("--subtitle", type=Path)

    official_build_parser = subparsers.add_parser("official-subtitle-build")
    official_build_parser.add_argument("--mv-dir", required=True, type=Path)

    alignment_prepare_parser = subparsers.add_parser("alignment-prepare")
    alignment_prepare_parser.add_argument("--mv-dir", required=True, type=Path)

    alignment_preflight_parser = subparsers.add_parser("alignment-preflight")
    alignment_preflight_parser.add_argument("--mv-dir", required=True, type=Path)
    alignment_preflight_parser.add_argument(
        "--mode",
        default="sofa",
        choices=("whisper", "sofa", "combined", "compare"),
    )
    alignment_preflight_parser.add_argument("--ffprobe")

    alignment_validate_parser = subparsers.add_parser("alignment-validate")
    alignment_validate_parser.add_argument("--mv-dir", required=True, type=Path)
    alignment_validate_parser.add_argument(
        "--mode",
        default="sofa",
        choices=("whisper", "sofa", "combined", "compare"),
    )

    candidate_parser = subparsers.add_parser("build-candidate")
    candidate_parser.add_argument("--mv-dir", required=True, type=Path)
    candidate_parser.add_argument("--runtime-root")

    build_subtitle_parser = subparsers.add_parser("build-subtitle")
    build_subtitle_parser.add_argument("--mv-dir", required=True, type=Path)
    build_subtitle_parser.add_argument("--candidate", required=True, type=Path)

    style_parser = subparsers.add_parser("style")
    style_parser.add_argument("--mv-dir", required=True, type=Path)
    style_parser.add_argument("--ffprobe")

    preview_parser = subparsers.add_parser("preview")
    preview_parser.add_argument("--mv-dir", required=True, type=Path)
    preview_parser.add_argument("--ffmpeg")

    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--mv-dir", required=True, type=Path)
    render_parser.add_argument("--ffmpeg")
    render_parser.add_argument("--ffprobe")
    render_parser.add_argument("--crf", type=int, default=18)
    render_parser.add_argument("--preset", default="medium")

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--mv-dir", required=True, type=Path)
    validate_parser.add_argument("--video", type=Path)
    validate_parser.add_argument("--ffmpeg")
    validate_parser.add_argument("--ffprobe")

    cover_parser = subparsers.add_parser("cover-candidates")
    cover_parser.add_argument("--mv-dir", required=True, type=Path)
    cover_parser.add_argument("--ffmpeg")
    cover_parser.add_argument("--ffprobe")

    check_parser = subparsers.add_parser("delivery-check")
    check_parser.add_argument("--mv-dir", required=True, type=Path)

    archive_parser = subparsers.add_parser("archive")
    archive_parser.add_argument("--mv-dir", required=True, type=Path)
    archive_parser.add_argument("--destination", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(arguments)
    route = {
        "download": ("youtube-download", "base"),
        "alignment-prepare": ("song", "base"),
        "build-candidate": ("song", "base"),
    }.get(args.action)
    if route:
        dispatched = dispatch_script_in_profile(
            route[0],
            route[1],
            project_root_for_mv(args.mv_dir),
            Path(__file__),
            arguments,
            getattr(args, "runtime_root", None),
        )
        if dispatched is not None:
            return dispatched
    try:
        if args.action == "init":
            result = initialize(args.mv_dir)
        elif args.action == "download":
            result = download_source(args.mv_dir, args.url, args.runtime_root)
        elif args.action == "import-source":
            result = import_source(args.mv_dir, args.source)
        elif args.action == "lyrics":
            result = acquire_lyrics(args.mv_dir, args.netease_cookie_file)
        elif args.action == "official-subtitle-prepare":
            result = prepare_official_subtitle(args.mv_dir, args.subtitle)
        elif args.action == "official-subtitle-build":
            result = build_official_candidate(args.mv_dir)
        elif args.action == "alignment-prepare":
            result = prepare_alignment(args.mv_dir)
        elif args.action == "alignment-preflight":
            result = preflight_alignment(args.mv_dir, args.mode, args.ffprobe)
        elif args.action == "alignment-validate":
            result = validate_alignment_candidates(args.mv_dir, args.mode)
        elif args.action == "build-candidate":
            result = build_song_candidate(args.mv_dir, args.runtime_root)
        elif args.action == "build-subtitle":
            result = build_subtitle(args.mv_dir, args.candidate)
        elif args.action == "style":
            result = style_subtitle(args.mv_dir, args.ffprobe)
        elif args.action == "preview":
            result = preview(args.mv_dir, args.ffmpeg)
        elif args.action == "render":
            result = render(args.mv_dir, args.ffmpeg, args.ffprobe, args.crf, args.preset)
        elif args.action == "validate":
            result = validate(args.mv_dir, args.video, args.ffmpeg, args.ffprobe)
        elif args.action == "cover-candidates":
            result = cover_candidates(args.mv_dir, args.ffmpeg, args.ffprobe)
        elif args.action == "delivery-check":
            result = delivery_check(args.mv_dir)
        else:
            result = archive_delivery(args.mv_dir, args.destination)
    except Exception as error:
        print(f"error={error}", file=sys.stderr)
        return 2
    print(f"result={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

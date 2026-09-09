from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from alignment_workflow import (
    preflight_alignment,
    prepare_alignment,
    validate_alignment_candidates,
)
from chinese_source import read_chinese_source, record_chinese_source
from common import (
    VERSION,
    MP4_COPY_AUDIO,
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
    apply_official_translations,
    build_official_candidate,
    prepare_netease_reference,
    prepare_official_subtitle,
    prepare_official_subtitle_if_present,
    validate_official_candidate,
    write_netease_reference_failure,
)
from runtime_manager import dispatch_script_in_profile
from song_candidate import build_song_candidate, _sample_source_frames
from review_images import contact_sheet
from subtitle_io import (
    Cue,
    format_review_timestamp,
    load_cues,
    parse_review_timestamp,
    render_ass,
    render_srt,
)
from youtube_acquire import acquire_source
from youtube_cover import prepare_youtube_cover, prepared_youtube_cover, youtube_video_id

PUBLISH_COPY_PLATFORMS = ("微博", "小红书", "B站", "视频号")
PUBLISH_COPY_SECTIONS = tuple(f"## {name}" for name in PUBLISH_COPY_PLATFORMS)
COPY_PLACEHOLDERS = ("TODO", "待补", "在这里填写", "尚未完成")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _filter_path(path: Path) -> str:
    return path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")


def prepare_official_review(
    mv_dir: Path, translations: Path | None = None,
    translation_model: str | None = None,
    edits: Path | None = None,
) -> Path:
    from concurrent.futures import ThreadPoolExecutor

    mv_dir = ensure_workspace(mv_dir)
    source = find_source_video(mv_dir)
    ffmpeg = str(resolve_binary("ffmpeg"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        frames = executor.submit(_sample_source_frames, mv_dir, source, ffmpeg)
        apply_official_translations(mv_dir, translation_model, translations, edits)
        candidate = build_official_candidate(mv_dir)
        validate_official_candidate(mv_dir, candidate)
        frames.result()
    contact_sheet(
        sorted((mv_dir / "review" / "source-visual-audit" / "frames").glob("frame-*.jpg")),
        mv_dir / "review" / "source-visual-audit" / "contact-sheet.jpg",
        columns=3,
    )
    return candidate


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
        if path.is_file()
        and path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
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
    acquired = acquire_source(
        mv_dir,
        url,
        runtime_root,
    )
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
                f"- 官方字幕获取　{facts.get('subtitle_status', 'normal')}",
                f"- 取源证据　{acquired.evidence}",
                *media_summary(info),
                "",
                "片源选择以最终压制兼容为前提。检测到HDR时，压制前必须更换SDR片源或明确制定转色方案",
                "",
            ]
        ),
    )
    if prepare_official_subtitle_if_present(mv_dir) is not None:
        acquire_official_lyrics_reference(mv_dir)
    return media


def acquire_lyrics(mv_dir: Path, cookie_file: Path | None = None) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    candidate = fetch_for_workspace(mv_dir, cookie_file)
    if candidate.coverage < 1.0 or not candidate.translation:
        raise ValueError(
            "网易云候选没有完整日中双语覆盖，已保留查询结果，请补齐chinese.lrc后继续"
        )
    record_chinese_source(mv_dir, "netease")
    return mv_dir / "lyrics" / "lookup.md"


def acquire_official_lyrics_reference(
    mv_dir: Path, cookie_file: Path | None = None
) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    try:
        candidate = fetch_for_workspace(mv_dir, cookie_file)
        return prepare_netease_reference(mv_dir, candidate)
    except (LookupError, OSError, TimeoutError) as error:
        return write_netease_reference_failure(mv_dir, error)


def prepare_official_route(
    mv_dir: Path,
    subtitle: Path | None = None,
    cookie_file: Path | None = None,
) -> Path:
    translation = prepare_official_subtitle(mv_dir, subtitle)
    acquire_official_lyrics_reference(mv_dir, cookie_file)
    return translation


def build_subtitle(mv_dir: Path, candidate: Path) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    destination = mv_dir / "subtitle" / "master.srt"
    if destination.exists():
        raise FileExistsError(f"审定字幕已存在，拒绝覆盖：{destination}")
    candidate = candidate.resolve()
    candidate_root = (mv_dir / "review" / "alignment" / "candidates").resolve()
    structure_candidate = (mv_dir / "review" / "structure" / "structure-candidate.srt").resolve()
    if (candidate.parent != candidate_root and candidate != structure_candidate) or candidate.suffix.casefold() != ".srt":
        raise ValueError(f"候选必须是固定候选目录中的SRT：{candidate_root}")
    if not candidate.is_file():
        raise FileNotFoundError(f"候选不存在：{candidate}")
    from structure_review import validate_structure_promotion
    validate_structure_promotion(mv_dir, candidate)
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
        raise RuntimeError(
            f"无法在{format_review_timestamp(timestamp)}采样字幕区域亮度"
        )
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


def preview(
    mv_dir: Path,
    ffmpeg_value: str | None = None,
    ffprobe_value: str | None = None,
) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    source = find_source_video(mv_dir)
    styled = find_styled_ass(mv_dir)
    master = find_master_subtitle(mv_dir)
    _require_fresh(styled, [source, master], "标准ASS已过期，请先重新运行style")
    cues = load_cues(master)
    ffmpeg = resolve_binary("ffmpeg", ffmpeg_value)
    media = probe_media(source, ffprobe_value)
    duration = media.duration
    luma = [_sample_luma(source, (cue.start + cue.end) / 2, ffmpeg) for cue in cues]
    windows = _preview_windows(cues, luma, duration)
    preview_dir = mv_dir / "review" / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for legacy_path in (
        mv_dir / "review" / "preview.md",
        mv_dir / "review" / "preview-windows.tsv",
    ):
        legacy_path.unlink(missing_ok=True)
    preview_video = preview_dir / source.name
    preview_ass = preview_dir / f"{source.stem}.ass"
    if (not preview_video.is_file()
            or preview_video.stat().st_size != source.stat().st_size
            or preview_video.stat().st_mtime_ns != source.stat().st_mtime_ns):
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
            f"{label}\t{format_review_timestamp(start)}\t"
            f"{format_review_timestamp(end)}\t{preview_video}\t{preview_ass}"
        )
        report.append(
            f"- {label}　{format_review_timestamp(start)}至"
            f"{format_review_timestamp(end)}"
        )
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


def finish(
    mv_dir: Path,
    candidate: Path | None = None,
    ffmpeg_value: str | None = None,
    ffprobe_value: str | None = None,
    crf: int = 18,
    preset: str = "medium",
    encoder: str = "libx264",
    nvenc_cq: int = 18,
    nvenc_bitrate_kbps: int = 0,
) -> Path:
    started = time.perf_counter()
    timings: list[tuple[str, float]] = []

    def step(label: str, function: Any, *arguments: Any) -> Any:
        print(f"[MV] {label}开始", flush=True)
        before = time.perf_counter()
        result = function(*arguments)
        elapsed = time.perf_counter() - before
        timings.append((label, elapsed))
        print(f"[MV] {label}完成 {elapsed:.3f}秒", flush=True)
        return result

    # Validate options before creating any formal subtitle or media output.
    _encoder_arguments(encoder, crf, preset, nvenc_cq, nvenc_bitrate_kbps)
    mv_dir = ensure_workspace(mv_dir)
    master = mv_dir / "subtitle" / "master.srt"
    if master.is_file() and candidate is not None:
        raise ValueError(
            "master.srt已存在，直接审定该文件后运行finish，不要再次传入候选"
        )
    if not master.is_file():
        if candidate is None:
            raise FileNotFoundError(
                "master.srt不存在，首次出片必须传入已确认的--candidate"
            )
        step("采用审定候选", build_subtitle, mv_dir, candidate)
    step("标准字幕", style_subtitle, mv_dir, ffprobe_value)
    step("完整预览", preview, mv_dir, ffmpeg_value, ffprobe_value)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as executor:
        cover_task = executor.submit(prepare_youtube_cover, mv_dir)
        print("[MV] 压制期间准备YouTube封面，可同步完成work/publish-copy.md", flush=True)
        video = step("正片编码", render, mv_dir, ffmpeg_value, ffprobe_value,
                     crf, preset, encoder, nvenc_cq, nvenc_bitrate_kbps)
        step("机器验收", validate, mv_dir, video, ffmpeg_value, ffprobe_value)
        try:
            cover_task.result()
        except Exception as error:
            print(f"[MV] 封面准备未完成：{error}；保留正片，补齐后运行stage-delivery", flush=True)
    publish_copy = mv_dir / "work" / "publish-copy.md"
    if publish_copy.is_file() and _copy_ready(
        publish_copy, read_chinese_source(mv_dir), _publish_copy_sections(mv_dir), mv_dir
    ) and _available_cover_sources(mv_dir):
        step("文案与封面落盘", stage_delivery, mv_dir)
    status = delivery_status(mv_dir)
    version = latest_version(mv_dir / "output", mv_dir.name)
    report = mv_dir / "review" / f"final-v{version:02d}-timings.md"
    quality = (f"{preset} / CRF{crf}" if encoder == "libx264"
               else f"p7 / HQ / CQ{nvenc_cq} / VBR {nvenc_bitrate_kbps}kbps（0为质量模式）")
    total = time.perf_counter() - started
    write_text(report, "\n".join([
        f"# 成品阶段耗时v{version:02d}", "",
        f"- 编码器　{encoder}", f"- 参数　{quality}",
        "- 计时范围　finish业务入口至交付状态，不含外层运行时启动和人工查看", "",
        "|阶段|秒|", "|---|---:|",
        *[f"|{label}|{elapsed:.3f}|" for label, elapsed in timings],
        f"|合计|{total:.3f}|", "",
    ]))
    print(f"[MV] finish合计 {total:.3f}秒；阶段耗时：{report}", flush=True)
    print(f"[MV] 成品抽帧：{mv_dir / 'review' / f'final-v{version:02d}-contact-sheet.jpg'}", flush=True)
    return status


def _encoder_arguments(
    encoder: str, crf: int, preset: str, nvenc_cq: int, nvenc_bitrate_kbps: int,
) -> list[str]:
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
    if encoder != "h264_nvenc":
        raise ValueError(f"不支持的视频编码器：{encoder}")
    if not 0 <= nvenc_cq <= 51 or nvenc_bitrate_kbps < 0:
        raise ValueError("NVENC CQ须为0至51，码率须不小于0")
    # CQ and CRF are unrelated quality scales; do not equate identical numbers.
    arguments = [
        "-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq",
        "-rc", "vbr", "-cq", str(nvenc_cq),
        "-b:v", f"{nvenc_bitrate_kbps}k" if nvenc_bitrate_kbps else "0",
        "-multipass", "fullres", "-spatial-aq", "1", "-temporal-aq", "1",
        "-rc-lookahead", "32", "-bf", "3",
    ]
    if nvenc_bitrate_kbps:
        arguments += ["-maxrate", f"{nvenc_bitrate_kbps * 2}k",
                      "-bufsize", f"{nvenc_bitrate_kbps * 4}k"]
    return arguments


def render(
    mv_dir: Path,
    ffmpeg_value: str | None = None,
    ffprobe_value: str | None = None,
    crf: int = 18,
    preset: str = "medium",
    encoder: str = "libx264",
    nvenc_cq: int = 18,
    nvenc_bitrate_kbps: int = 0,
) -> Path:
    encoder_args = _encoder_arguments(encoder, crf, preset, nvenc_cq, nvenc_bitrate_kbps)
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
        raise ValueError(
            "检测到HDR片源，当前流程不会盲目转为SDR，请更换SDR片源或先制定转色方案"
        )
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
                *encoder_args,
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
                "start": parse_review_timestamp(row["start"]),
                "end": parse_review_timestamp(row["end"]),
                "video": row["video"],
                "subtitle": row["subtitle"],
            }
        )
    return windows


def _extract_frame(
    video: Path, timestamp: float, destination: Path, ffmpeg: Path
) -> None:
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
    final = (
        video.resolve()
        if video
        else mv_dir / "output" / f"{mv_dir.name}.hardsub.v{version:02d}.mp4"
    )
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
            for suffix, timestamp in (
                ("before", center - 0.15),
                ("after", center + 0.15),
            ):
                frame = frames_dir / f"{item['label']}-{suffix}.jpg"
                _extract_frame(final, timestamp, frame, ffmpeg)
                frame_lines.append(
                    f"- {item['label']}-{suffix}　"
                    f"{format_review_timestamp(timestamp)}　{frame}"
                )
        else:
            frame = frames_dir / f"{item['label']}.jpg"
            _extract_frame(final, center, frame, ffmpeg)
            frame_lines.append(
                f"- {item['label']}　{format_review_timestamp(center)}　{frame}"
            )
    contact_sheet(sorted(frames_dir.glob("*.jpg")),
                  mv_dir / "review" / f"final-v{version:02d}-contact-sheet.jpg")
    report = mv_dir / "review" / f"final-v{version:02d}-machine-check.md"
    write_text(
        report,
        "\n".join(
            [
                f"# 成品机器检查v{version:02d}",
                "",
                f"- 成品　{final}",
                f"- 媒体　{final_info.width}×{final_info.height} / {final_info.duration:.3f}秒 / {final_info.video_codec}+{final_info.audio_codec}",
                f"- 文件大小　{final.stat().st_size / 1048576:.2f}MiB",
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
                    "- [ ] 已确认封面原尺寸和缩小显示正常",
                    "",
                ]
            ),
        )
    print(f"[MV] 成品 {final_info.width}×{final_info.height}，"
          f"{final_info.duration:.3f}秒，{final_info.video_codec}+{final_info.audio_codec}，"
          f"{final.stat().st_size / 1048576:.2f}MiB；机器校验通过", flush=True)
    return report


def _available_cover_sources(mv_dir: Path) -> list[Path]:
    from PIL import Image

    if youtube_video_id(mv_dir):
        cover = prepared_youtube_cover(mv_dir)
        return [cover] if cover else []

    groups = (
        mv_dir / "work" / "cover-source.*",
        mv_dir / "work" / "reference" / "official-release-art*",
    )
    selected: list[Path] = []
    seen: set[Path] = set()
    for pattern in groups:
        candidates: list[tuple[int, Path]] = []
        for path in pattern.parent.glob(pattern.name):
            resolved = path.resolve()
            if (
                not path.is_file()
                or path.suffix.lower() not in IMAGE_SUFFIXES
                or resolved in seen
            ):
                continue
            try:
                with Image.open(path) as opened:
                    area = opened.width * opened.height
            except OSError:
                continue
            candidates.append((area, path))
        for _, path in sorted(candidates, key=lambda item: (-item[0], item[1].name)):
            seen.add(path.resolve())
            selected.append(path)
    return selected


def _publish_copy_sections(mv_dir: Path) -> tuple[str, ...]:
    scope = mv_dir / "work" / "publish-platforms.txt"
    if not scope.is_file():
        return PUBLISH_COPY_SECTIONS
    platforms = tuple(
        line.strip()
        for line in scope.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    )
    if not platforms:
        raise ValueError(f"发布平台声明为空：{scope}")
    if len(set(platforms)) != len(platforms):
        raise ValueError(f"发布平台声明含重复项：{scope}")
    unsupported = [name for name in platforms if name not in PUBLISH_COPY_PLATFORMS]
    if unsupported:
        raise ValueError("发布平台声明含未支持项：" + "、".join(unsupported))
    return tuple(f"## {name}" for name in platforms)


def _copy_ready(
    path: Path,
    source_credit: str,
    sections: tuple[str, ...] = PUBLISH_COPY_SECTIONS,
    mv_dir: Path | None = None,
) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8-sig").strip()
    if (
        len(text) < 80
        or any(marker in text for marker in COPY_PLACEHOLDERS)
        or not all(section in text for section in sections)
    ):
        return False
    positions = sorted(
        (text.index(section), section) for section in sections
    )
    for index, (start, section) in enumerate(positions):
        body_start = start + len(section)
        body_end = positions[index + 1][0] if index + 1 < len(positions) else len(text)
        if source_credit not in text[body_start:body_end]:
            return False
    from publish_preflight import check_title_preference, parse_copy

    return not check_title_preference(mv_dir or path.parent.parent, parse_copy(text))


def _write_versioned_cover(source: Path, destination: Path) -> None:
    from PIL import Image

    with Image.open(source) as opened:
        image = opened.convert("RGB")
        if min(image.size) < 720:
            raise ValueError(
                f"现成封面尺寸不足：{image.width}x{image.height}，拒绝放大低清图片"
            )
        partial = destination.with_name(f".{destination.stem}.partial{destination.suffix}")
        try:
            if destination.suffix.lower() in {".jpg", ".jpeg"}:
                if opened.format == "JPEG":
                    shutil.copyfile(source, partial)
                else:
                    image.save(partial, format="JPEG", quality=95, subsampling=0)
            else:
                image.save(partial, format="PNG", optimize=True)
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)


def stage_delivery(
    mv_dir: Path,
    publish_copy_source: Path | None = None,
    cover_source: Path | None = None,
    replace_cover: bool = False,
) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    output = mv_dir / "output"
    version = latest_version(output, mv_dir.name)
    source_credit = read_chinese_source(mv_dir)
    copy_sections = _publish_copy_sections(mv_dir)
    platform_label = "、".join(section.removeprefix("## ") for section in copy_sections)
    publish_source = (
        publish_copy_source.resolve()
        if publish_copy_source
        else mv_dir / "work" / "publish-copy.md"
    )
    if not publish_source.is_file():
        raise FileNotFoundError(
            f"缺少已定稿平台文案（{platform_label}），请先写入work/publish-copy.md或传入--publish-copy"
        )
    if not _copy_ready(publish_source, source_credit, copy_sections, mv_dir):
        raise ValueError(
            f"平台文案（{platform_label}）仍有占位内容、章节不完整或没有在每个平台注明中文歌词来源："
            + str(publish_source)
        )
    publish_destination = output / (f"{mv_dir.name}.publish-copy.v{version:02d}.md")
    source_text = publish_source.read_text(encoding="utf-8-sig")
    if publish_destination.is_file():
        if publish_destination.read_text(encoding="utf-8-sig") != source_text:
            raise FileExistsError(
                f"同版本正式文案已存在且内容不同：{publish_destination}"
            )
    else:
        write_text(publish_destination, source_text)

    if cover_source is None:
        prepare_youtube_cover(mv_dir)
    automatic_covers = _available_cover_sources(mv_dir)
    selected_cover = cover_source.resolve() if cover_source else None
    if selected_cover is None and automatic_covers:
        selected_cover = automatic_covers[0]
    if selected_cover is None or not selected_cover.is_file():
        raise FileNotFoundError(
            "缺少合格封面：YouTube来源采用同支视频缩略图；其他来源采用正式发行图，保存为work/cover-source.<ext>或用--cover传入"
        )
    if selected_cover.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError(f"封面源格式不支持：{selected_cover}")
    cover_suffix = ".jpg" if selected_cover.suffix.lower() in {".jpg", ".jpeg"} else ".png"
    cover_destination = output / f"{mv_dir.name}.cover.v{version:02d}{cover_suffix}"
    existing_covers = [
        path
        for path in output.glob(f"{mv_dir.name}.cover.v{version:02d}.*")
        if path.suffix.lower() in IMAGE_SUFFIXES
    ]
    if not existing_covers:
        _write_versioned_cover(selected_cover, cover_destination)
    elif len(existing_covers) > 1 or existing_covers[0] != cover_destination:
        raise FileExistsError(
            "同版本封面已存在，拒绝生成第二份：" + "、".join(map(str, existing_covers))
        )

    elif replace_cover:
        _write_versioned_cover(selected_cover, cover_destination)

    report = output / f"delivery-assets.v{version:02d}.md"
    write_text(
        report,
        "\n".join(
            [
                f"# MV交付素材v{version:02d}",
                "",
                f"- 平台文案（{platform_label}）　{publish_destination}",
                f"- 封面源　{selected_cover}",
                f"- 正式封面　{cover_destination}",
                "",
                "素材已经落到与成品相同版本。发布仍需用户明确授权",
                "",
            ]
        ),
    )
    return report


def delivery_status(mv_dir: Path) -> Path:
    from PIL import Image

    mv_dir = ensure_workspace(mv_dir)
    output = mv_dir / "output"
    version = latest_version(output, mv_dir.name)
    source_credit = read_chinese_source(mv_dir)
    copy_sections = _publish_copy_sections(mv_dir)
    platform_label = "、".join(section.removeprefix("## ") for section in copy_sections)
    video = output / f"{mv_dir.name}.hardsub.v{version:02d}.mp4"
    ass = output / f"{mv_dir.name}.subtitle.v{version:02d}.ass"
    publish_copy = output / f"{mv_dir.name}.publish-copy.v{version:02d}.md"
    covers = [
        path
        for path in output.glob(f"{mv_dir.name}.cover.v{version:02d}.*")
        if path.suffix.lower() in IMAGE_SUFFIXES
    ]
    machine = mv_dir / "review" / f"final-v{version:02d}-machine-check.md"
    human = mv_dir / "review" / f"final-v{version:02d}-human-check.md"
    copy_ready = publish_copy.is_file() and _copy_ready(
        publish_copy,
        source_credit,
        copy_sections,
    )
    cover_ready = len(covers) == 1
    if cover_ready:
        try:
            with Image.open(covers[0]) as cover:
                cover_ready = cover.width >= 720 and cover.height >= 720
        except OSError:
            cover_ready = False
    human_ready = human.is_file() and "- [ ]" not in human.read_text(
        encoding="utf-8-sig"
    )
    checks = (
        ("硬字幕MP4", video.is_file()),
        ("同版本ASS", ass.is_file()),
        (f"平台正式文案（{platform_label}）", copy_ready),
        ("本支YouTube视频封面或对应发行封面", cover_ready),
        ("机器检查", machine.is_file()),
        ("人工检查", human_ready),
    )
    complete = all(ready for _, ready in checks)
    report = output / f"delivery-status.v{version:02d}.md"
    lines = [f"# MV交付状态v{version:02d}", ""]
    lines.extend(f"- [{'x' if ready else ' '}] {label}" for label, ready in checks)
    lines.extend(
        [
            "",
            "交付齐全" if complete else "交付未完成，缺失项补齐前不得报告完成",
            "",
        ]
    )
    write_text(report, "\n".join(lines))
    return report


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
    source_credit = read_chinese_source(mv_dir)
    copy_sections = _publish_copy_sections(mv_dir)
    incomplete_copy = publish_copy.is_file() and not _copy_ready(
        publish_copy,
        source_credit,
        copy_sections,
    )
    covers = [
        path
        for path in output.glob(f"{mv_dir.name}.cover.v{version:02d}.*")
        if path.suffix.lower() in IMAGE_SUFFIXES
    ]
    if len(covers) != 1:
        missing.append(output / f"{mv_dir.name}.cover.v{version:02d}.png")
    if missing:
        raise FileNotFoundError(
            "交付缺少文件：" + "、".join(str(path) for path in missing)
        )
    if incomplete_copy:
        raise ValueError(
            "合并文案为空、仍有占位内容、缺少平台章节或未在每个平台注明中文歌词来源："
            + str(publish_copy)
        )
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
                f"- {source_credit}",
                "- 本支YouTube视频封面或对应发行封面　齐全",
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
    official_prepare_parser.add_argument("--netease-cookie-file", type=Path)

    official_reference_parser = subparsers.add_parser("official-subtitle-reference")
    official_reference_parser.add_argument("--mv-dir", required=True, type=Path)
    official_reference_parser.add_argument("--netease-cookie-file", type=Path)

    official_review_parser = subparsers.add_parser("official-subtitle-review")
    official_review_parser.add_argument("--mv-dir", required=True, type=Path)
    official_review_parser.add_argument("--translations", type=Path)
    official_review_parser.add_argument("--translation-model")
    official_review_parser.add_argument("--edits", type=Path)

    official_build_parser = subparsers.add_parser("official-subtitle-build")
    official_build_parser.add_argument("--mv-dir", required=True, type=Path)

    official_apply_parser = subparsers.add_parser("official-subtitle-apply")
    official_apply_parser.add_argument("--mv-dir", required=True, type=Path)
    official_apply_parser.add_argument("--translations", type=Path)
    official_apply_parser.add_argument("--translation-model")
    official_apply_parser.add_argument("--edits", type=Path)

    chinese_source_parser = subparsers.add_parser("chinese-source")
    chinese_source_parser.add_argument("--mv-dir", required=True, type=Path)
    chinese_source_parser.add_argument(
        "--kind",
        required=True,
        choices=("netease", "translation-model"),
    )
    chinese_source_parser.add_argument("--model")

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

    structure_prepare_parser = subparsers.add_parser("structure-prepare")
    structure_prepare_parser.add_argument("--mv-dir", required=True, type=Path)
    structure_prepare_parser.add_argument("--candidate", required=True, type=Path)
    structure_prepare_parser.add_argument("--cycle")
    structure_propose_parser = subparsers.add_parser("structure-propose")
    structure_propose_parser.add_argument("--mv-dir", required=True, type=Path)
    structure_preview_parser = subparsers.add_parser("structure-preview")
    structure_preview_parser.add_argument("--mv-dir", required=True, type=Path)
    structure_rhythm_parser = subparsers.add_parser("structure-rhythm")
    structure_rhythm_parser.add_argument("--mv-dir", required=True, type=Path)
    structure_rhythm_parser.add_argument("--candidate", required=True, type=Path)

    build_subtitle_parser = subparsers.add_parser("build-subtitle")
    build_subtitle_parser.add_argument("--mv-dir", required=True, type=Path)
    build_subtitle_parser.add_argument("--candidate", required=True, type=Path)

    style_parser = subparsers.add_parser("style")
    style_parser.add_argument("--mv-dir", required=True, type=Path)
    style_parser.add_argument("--ffprobe")

    preview_parser = subparsers.add_parser("preview")
    preview_parser.add_argument("--mv-dir", required=True, type=Path)
    preview_parser.add_argument("--ffmpeg")
    preview_parser.add_argument("--ffprobe")

    finish_parser = subparsers.add_parser("finish")
    finish_parser.add_argument("--mv-dir", required=True, type=Path)
    finish_parser.add_argument("--candidate", type=Path)
    finish_parser.add_argument("--ffmpeg")
    finish_parser.add_argument("--ffprobe")
    finish_parser.add_argument("--crf", type=int, default=18)
    finish_parser.add_argument("--preset", default="medium")
    finish_parser.add_argument("--encoder", choices=("libx264", "h264_nvenc"), default="libx264")
    finish_parser.add_argument("--nvenc-cq", type=int, default=18)
    finish_parser.add_argument("--nvenc-bitrate-kbps", type=int, default=0)

    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--mv-dir", required=True, type=Path)
    render_parser.add_argument("--ffmpeg")
    render_parser.add_argument("--ffprobe")
    render_parser.add_argument("--crf", type=int, default=18)
    render_parser.add_argument("--preset", default="medium")
    render_parser.add_argument("--encoder", choices=("libx264", "h264_nvenc"), default="libx264")
    render_parser.add_argument("--nvenc-cq", type=int, default=18)
    render_parser.add_argument("--nvenc-bitrate-kbps", type=int, default=0)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--mv-dir", required=True, type=Path)
    validate_parser.add_argument("--video", type=Path)
    validate_parser.add_argument("--ffmpeg")
    validate_parser.add_argument("--ffprobe")

    stage_parser = subparsers.add_parser("stage-delivery")
    stage_parser.add_argument("--mv-dir", required=True, type=Path)
    stage_parser.add_argument("--publish-copy", type=Path)
    stage_parser.add_argument("--cover", type=Path)
    stage_parser.add_argument("--replace-cover", action="store_true", help="按明确修改要求替换同版本、同格式封面")

    status_parser = subparsers.add_parser("delivery-status")
    status_parser.add_argument("--mv-dir", required=True, type=Path)

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
        "official-subtitle-review": ("media", "base"),
        "alignment-prepare": ("song", "base"),
        "build-candidate": ("song", "base"),
        "structure-prepare": ("media", "base"),
        "structure-propose": ("media", "base"),
        "structure-preview": ("media", "base"),
        "structure-rhythm": ("media", "base"),
        "build-subtitle": ("media", "base"),
        "import-source": ("media", "base"),
        "style": ("media", "base"),
        "preview": ("media", "base"),
        "finish": ("media", "base"),
        "render": ("media", "base"),
        "validate": ("media", "base"),
        "stage-delivery": ("media", "base"),
        "delivery-status": ("media", "base"),
        "delivery-check": ("media", "base"),
        "archive": ("media", "base"),
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
            result = download_source(
                args.mv_dir,
                args.url,
                args.runtime_root,
            )
        elif args.action == "import-source":
            result = import_source(args.mv_dir, args.source)
        elif args.action == "lyrics":
            result = acquire_lyrics(args.mv_dir, args.netease_cookie_file)
        elif args.action == "official-subtitle-prepare":
            result = prepare_official_route(
                args.mv_dir,
                args.subtitle,
                args.netease_cookie_file,
            )
        elif args.action == "official-subtitle-reference":
            result = acquire_official_lyrics_reference(
                args.mv_dir,
                args.netease_cookie_file,
            )
        elif args.action == "official-subtitle-review":
            result = prepare_official_review(args.mv_dir, args.translations, args.translation_model, args.edits)
        elif args.action == "official-subtitle-build":
            result = build_official_candidate(args.mv_dir)
        elif args.action == "official-subtitle-apply":
            result = apply_official_translations(
                args.mv_dir,
                args.translation_model,
                args.translations,
                args.edits,
            )
        elif args.action == "chinese-source":
            result = record_chinese_source(args.mv_dir, args.kind, args.model)
        elif args.action == "alignment-prepare":
            result = prepare_alignment(args.mv_dir)
        elif args.action == "alignment-preflight":
            result = preflight_alignment(args.mv_dir, args.mode, args.ffprobe)
        elif args.action == "alignment-validate":
            result = validate_alignment_candidates(args.mv_dir, args.mode)
        elif args.action == "build-candidate":
            result = build_song_candidate(args.mv_dir, args.runtime_root)
        elif args.action == "structure-prepare":
            from structure_review import prepare_structure
            result = prepare_structure(args.mv_dir, args.candidate, args.cycle)
        elif args.action == "structure-propose":
            from structure_review import propose_structure
            result = propose_structure(args.mv_dir)
        elif args.action == "structure-preview":
            from structure_review import preview_structure
            result = preview_structure(args.mv_dir)
        elif args.action == "structure-rhythm":
            from structure_review import rhythm_structure
            result = rhythm_structure(args.mv_dir, args.candidate)
        elif args.action == "build-subtitle":
            result = build_subtitle(args.mv_dir, args.candidate)
        elif args.action == "style":
            result = style_subtitle(args.mv_dir, args.ffprobe)
        elif args.action == "preview":
            result = preview(args.mv_dir, args.ffmpeg, args.ffprobe)
        elif args.action == "finish":
            result = finish(
                args.mv_dir,
                args.candidate,
                args.ffmpeg,
                args.ffprobe,
                args.crf,
                args.preset,
                args.encoder,
                args.nvenc_cq,
                args.nvenc_bitrate_kbps,
            )
        elif args.action == "render":
            result = render(
                args.mv_dir, args.ffmpeg, args.ffprobe, args.crf, args.preset,
                args.encoder, args.nvenc_cq, args.nvenc_bitrate_kbps,
            )
        elif args.action == "validate":
            result = validate(args.mv_dir, args.video, args.ffmpeg, args.ffprobe)
        elif args.action == "stage-delivery":
            result = stage_delivery(args.mv_dir, args.publish_copy, args.cover, args.replace_cover)
        elif args.action == "delivery-status":
            result = delivery_status(args.mv_dir)
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

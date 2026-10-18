from __future__ import annotations

import argparse
import math
import statistics
import subprocess
from pathlib import Path
from typing import Any

from common import (
    VERSION,
    ass_timestamp,
    canonical_hash,
    console_version,
    load_manifest,
    resolve_binary,
    resolve_review_media,
    validate_filename_component,
    write_text,
)


PLAY_RES_X = 1920
PLAY_RES_Y = 1080
BAR_X = 0
BAR_Y = 8
BAR_WIDTH = 1920
BAR_HEIGHT = 18
BAR_GAP = 4
PROGRESS_SUFFIX = "song-progress.ass"
DEFAULT_PROGRESS_BAR = {
    "x": BAR_X, "y": BAR_Y, "width": BAR_WIDTH, "height": BAR_HEIGHT,
    "layout": "duration", "mc_weight": 3, "mc_max_song_ratio": 1.2,
    "mc_label_always": True, "unplayed_alpha": 102,
}


def progress_bar_config(value: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve public defaults without mutating the supplied manifest."""
    if value is not None and not isinstance(value, dict):
        raise ValueError("progress_bar必须为对象")
    supplied = {} if value is None else value
    if set(supplied) - set(DEFAULT_PROGRESS_BAR):
        raise ValueError("progress_bar包含未知字段")
    bar = {**DEFAULT_PROGRESS_BAR, **supplied}
    if any(type(bar[key]) is not int for key in ("x", "y", "width", "height")) or not (
        0 <= bar["x"] < PLAY_RES_X and 8 <= bar["y"] < PLAY_RES_Y
        and 0 < bar["width"] <= PLAY_RES_X - bar["x"]
        and 0 < bar["height"] <= PLAY_RES_Y - bar["y"] - 8
    ):
        raise ValueError("progress_bar必须为画面内整数坐标和正尺寸，上下各保留8像素")
    if bar["layout"] not in ("duration", "label_fit"):
        raise ValueError("progress_bar.layout只允许label_fit或duration")
    for key in ("mc_weight", "mc_max_song_ratio"):
        number = bar[key]
        if key == "mc_max_song_ratio" and number is None:
            continue
        if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
            raise ValueError(f"{key}必须为正数")
    if type(bar["mc_label_always"]) is not bool:
        raise ValueError("mc_label_always必须为布尔值")
    if type(bar["unplayed_alpha"]) is not int or not 0 <= bar["unplayed_alpha"] <= 255:
        raise ValueError("unplayed_alpha必须为0至255的整数")
    return bar


def timeline_sections(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Build presentation-only timeline; never change subtitle sections or approvals."""
    sections = [dict(section) for section in manifest.get("sections", [])]
    presentation = manifest.get("presentation", {})
    if not isinstance(presentation, dict):
        raise ValueError("presentation必须为对象")
    overrides = presentation.get("section_kinds", {})
    if not isinstance(overrides, dict) or set(overrides) - {section.get("id") for section in sections}:
        raise ValueError("section_kinds必须按已有稳定section ID配置")
    extras = presentation.get("timeline_extras", [])
    if not isinstance(extras, list):
        raise ValueError("timeline_extras必须为数组")
    for extra in extras:
        if not isinstance(extra, dict) or extra.get("type") not in {"recap", "transition", "mv"}:
            raise ValueError("额外展示段type只允许recap、transition或mv")
        sections.append(dict(extra))
    if not sections:
        raise ValueError("Manifest中没有时间线分段")
    sections.sort(key=lambda section: float(section["start"]))
    duration = float(manifest.get("duration_seconds", sections[-1]["end"]))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("时间线总时长非法")
    scope = presentation.get("timeline_range", {"start": 0, "end": duration})
    if not isinstance(scope, dict) or set(scope) != {"start", "end"}:
        raise ValueError("timeline_range必须包含start和end")
    scope_start, scope_end = float(scope["start"]), float(scope["end"])
    if not math.isfinite(scope_start) or not math.isfinite(scope_end) or not 0 <= scope_start < scope_end <= duration:
        raise ValueError("timeline_range必须位于全片时长内")
    timeline: list[dict[str, Any]] = []
    cursor = scope_start
    counters = {"song": 0, "mc": 0}
    for section in sections:
        start, end = float(section["start"]), float(section["end"])
        kind = overrides.get(section.get("id"), section.get("type"))
        if kind not in {"song", "mc", "recap", "transition", "mv"}:
            raise ValueError("不支持的时间线分段类型")
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration:
            raise ValueError("时间线分段区间非法或越界")
        if end <= scope_start or start >= scope_end:
            continue
        if start < scope_start or end > scope_end:
            raise ValueError("节目展示范围边界不能截断已有分段")
        if start < cursor:
            raise ValueError("时间线分段存在时间重叠")
        if start > cursor:
            timeline.append({"type": "transition", "start": cursor, "end": start, "label": "过场"})
        if kind in counters:
            counters[kind] += 1
            label = f"曲{counters[kind]}" if kind == "song" else f"MC{counters[kind]}"
        else:
            label = {"recap": "回顾", "transition": "过场", "mv": "MV"}[kind]
        if kind == "song" and not str(section.get("title", "")).strip():
            raise ValueError("歌曲分段缺少歌名")
        timeline.append({**section, "type": kind, "label": label})
        cursor = end
    if cursor < scope_end:
        timeline.append({"type": "transition", "start": cursor, "end": scope_end, "label": "过场"})
    if len(timeline) > 80:
        raise ValueError("时间线超过80段，无法保持标签可辨识")
    return timeline


def song_sections(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    songs = [
        section
        for section in manifest.get("sections", [])
        if section.get("type") == "song"
    ]
    if not songs:
        raise ValueError("Manifest中没有歌曲分段")
    if len(songs) > 80:
        raise ValueError("歌曲数量超过80，细进度条无法保持可辨识分段")
    previous_end = -math.inf
    for index, song in enumerate(songs, start=1):
        start = float(song["start"])
        end = float(song["end"])
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise ValueError(f"第{index}首歌曲区间非法")
        if start < previous_end - 0.001:
            raise ValueError("歌曲分段存在时间重叠")
        if not str(song.get("title", "")).strip():
            raise ValueError(f"第{index}首歌曲缺少歌名")
        previous_end = end
    return songs


def segment_bounds(total: int, *, x: int = BAR_X, width: int = BAR_WIDTH) -> list[tuple[int, int]]:
    if total <= 0:
        raise ValueError("歌曲数量必须大于0")
    gap = BAR_GAP if total <= 30 else 2
    available = width - gap * (total - 1)
    if available < total * 4:
        raise ValueError("歌曲数量过多，进度条分段宽度不足")
    base_width, remainder = divmod(available, total)
    bounds = []
    cursor = x
    for index in range(total):
        width = base_width + (1 if index < remainder else 0)
        bounds.append((cursor, cursor + width))
        cursor += width + gap
    return bounds


def _ass_escape(value: str) -> str:
    return (
        value.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _rectangle(x0: int, y0: int, x1: int, y1: int) -> str:
    return f"m {x0} {y0} l {x1} {y0} {x1} {y1} {x0} {y1}"


def _dialogue(
    layer: int,
    start: float,
    end: float,
    style: str,
    text: str,
) -> str:
    return (
        f"Dialogue: {layer},{ass_timestamp(start)},{ass_timestamp(end)},"
        f"{style},,0,0,0,,{text}"
    )


def current_label(section: dict[str, Any]) -> str:
    label = section["label"]
    if section["type"] == "song":
        label = label.removeprefix("曲") + "."
    if section["type"] in {"song", "mv"}:
        label += "  " + str(section.get("title", "")).replace("（片尾MV）", "").strip()
    return label.rstrip()


def label_width(label: str) -> int:
    """Match the approved Yu Gothic/libass preview without a personal font path."""
    import os

    try:
        from PIL import ImageFont
        font = ImageFont.truetype(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "YuGothB.ttc"), 24)
        return math.ceil(font.getlength(label) * 0.8) + 16
    except (ImportError, OSError):
        # Portable fallback when the reference font is unavailable
        import unicodedata
        units = sum(1 if unicodedata.east_asian_width(ch) in {"W", "F"} else 0.55 for ch in label)
        return math.ceil(units * 24 * 0.8) + 16


def active_segment_bounds(total: int, active: int, requested: int, *,
                          x: int = BAR_X, width: int = BAR_WIDTH) -> list[tuple[int, int]]:
    if total < 1 or not 0 <= active < total:
        raise ValueError("进度段索引非法")
    gap = BAR_GAP if total <= 30 else 2
    available = width - gap * (total - 1)
    if available < 20 + (total - 1) * 4:
        raise ValueError("进度条宽度不足以容纳标签和分段")
    current = min(requested, available - (total - 1) * 4)
    if total == 1:
        return [(x, x + current)]
    small, remainder = divmod(available - current, total - 1)
    bounds, cursor, used = [], x, 0
    for index in range(total):
        cell_width = current if index == active else small + (used < remainder)
        bounds.append((cursor, cursor + cell_width))
        cursor += cell_width + gap
        if index != active:
            used += 1
    return bounds


def duration_segment_bounds(sections: list[dict[str, Any]], *, mc_weight: float = 3,
                            x: int = BAR_X, width: int = BAR_WIDTH,
                            active: int | None = None, requested: int = 0,
                            mc_max_song_ratio: float | None = 1.2) -> list[tuple[int, int]]:
    """Use time-weighted widths, expanding only the active cell to fit its label."""
    if not sections or not math.isfinite(mc_weight) or mc_weight <= 0:
        raise ValueError("时长比例进度条需要有效分段和正数MC权重")
    gap = BAR_GAP if len(sections) <= 30 else 2
    available = width - gap * (len(sections) - 1)
    weights = [(float(s["end"]) - float(s["start"])) *
               (mc_weight if s["type"] == "mc" else 1) for s in sections]
    if any(not math.isfinite(w) or w <= 0 for w in weights):
        raise ValueError("进度条分段时长必须为正数")
    if mc_max_song_ratio is not None:
        if isinstance(mc_max_song_ratio, bool) or not math.isfinite(mc_max_song_ratio) or mc_max_song_ratio <= 0:
            raise ValueError("mc_max_song_ratio必须为正数")
        song_weights = [w for s, w in zip(sections, weights) if s["type"] == "song"]
        if song_weights:
            cap = statistics.median(song_weights) * mc_max_song_ratio
            weights = [min(w, cap) if s["type"] == "mc" else w for s, w in zip(sections, weights)]
    total, cumulative, previous = sum(weights), 0.0, 0
    bounds = []
    for index, weight in enumerate(weights):
        cumulative += weight
        edge = round(available * cumulative / total)
        if edge - previous < 1:
            raise ValueError("按时长分配后有进度块不足1像素，请增加进度条宽度")
        bounds.append((x + previous + index * gap, x + edge + index * gap))
        previous = edge
    if active is not None:
        if not 0 <= active < len(sections):
            raise ValueError("进度段索引非法")
        current = min(max(requested, bounds[active][1] - bounds[active][0]),
                      available - (len(sections) - 1))
        if current > bounds[active][1] - bounds[active][0]:
            # Reserve a visible pixel for each inactive cell, then allocate the
            # remaining space proportionally. The active cell returns to its
            # duration width as soon as the next section starts.
            remaining = available - current - (len(sections) - 1)
            other_total = total - weights[active]
            cumulative, previous, cursor, bounds = 0.0, 0, x, []
            for index, weight in enumerate(weights):
                if index == active:
                    cell_width = current
                else:
                    cumulative += weight
                    edge = round(remaining * cumulative / other_total)
                    cell_width = 1 + edge - previous
                    previous = edge
                bounds.append((cursor, cursor + cell_width))
                cursor += cell_width + gap
    return bounds


def _duration_progress_events(sections: list[dict[str, Any]], bar: dict[str, Any]) -> list[str]:
    unplayed_alpha = bar.get("unplayed_alpha", DEFAULT_PROGRESS_BAR["unplayed_alpha"])
    if type(unplayed_alpha) is not int or not 0 <= unplayed_alpha <= 255:
        raise ValueError("unplayed_alpha必须为0至255的整数")
    mc_label_always = bar.get("mc_label_always", DEFAULT_PROGRESS_BAR["mc_label_always"])
    if type(mc_label_always) is not bool:
        raise ValueError("mc_label_always必须为布尔值")
    top, bottom = bar["y"], bar["y"] + bar["height"]
    events = []
    for current, section in enumerate(sections):
        start, end = float(section["start"]), float(section["end"])
        label = "MC" if mc_label_always and section["type"] == "mc" else current_label(section)
        requested = label_width(label)
        bounds = duration_segment_bounds(sections, mc_weight=float(bar.get("mc_weight", 3)),
                                         x=bar["x"], width=bar["width"],
                                         active=current, requested=requested,
                                         mc_max_song_ratio=bar.get("mc_max_song_ratio", 1.2))
        for index, (x0, x1) in enumerate(bounds):
            # The current cell starts white too; only the elapsed region is colored.
            color = "5DC9F4" if index < current else "FFFFFF"
            alpha = 0 if index < current else unplayed_alpha
            events.append(_dialogue(
                10, start, end, "ProgressDrawing",
                r"{\an7\pos(0,0)\p1\bord0\shad0" +
                f"\\1c&H{color}&\\alpha&H{alpha:02X}&}}" +
                _rectangle(x0, top - 8 if index == current else top,
                           x1, bottom + 8 if index == current else bottom),
            ))
            if mc_label_always and sections[index]["type"] == "mc" and index != current:
                scale = min(100, max(1, x1 - x0 - 4) / max(1, (label_width("MC") - 16) * 16 / 24) * 100)
                events.append(_dialogue(
                    12, start, end, "ProgressLabel",
                    f"{{\\an5\\pos({(x0 + x1) / 2:g},{(top + bottom) / 2:g})\\fs16\\fscx{scale:g}}}MC",
                ))
        x0, x1 = bounds[current]
        duration_ms = max(1, round((end - start) * 1000))
        events.append(_dialogue(
            11, start, end, "ProgressDrawing",
            r"{\an7\pos(0,0)\p1\bord0\shad0\1c&H5DC9F4&\alpha&H00&" +
            f"\\clip({x0},{top - 8},{x0},{bottom + 8})"
            f"\\t(0,{duration_ms},1,\\clip({x0},{top - 8},{x1},{bottom + 8}))}}" +
            _rectangle(x0, top - 8, x1, bottom + 8),
        ))
        scale = min(100, max(1, x1 - x0 - 16) / max(1, requested - 16) * 100)
        events.append(_dialogue(
            12, start, end, "ProgressLabel",
            f"{{\\an4\\pos({x0 + 8},{(top + bottom) / 2 - 1:g})\\fscx{scale:g}}}" + _ass_escape(label),
        ))
    return events


def render_song_progress_ass(manifest: dict[str, Any]) -> str:
    sections = timeline_sections(manifest)
    presentation = manifest.get("presentation", {})
    bar = progress_bar_config(presentation.get("progress_bar", {}))
    top, bottom = bar["y"] - 8, bar["y"] + bar["height"] + 8
    header = f"""[Script Info]
; Generated by concert-subtitle v{VERSION}
; Manifest-SHA256: {canonical_hash(manifest)}
Title: {manifest.get('concert_id', 'concert')} timeline progress
ScriptType: v4.00+
PlayResX: {PLAY_RES_X}
PlayResY: {PLAY_RES_Y}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ProgressDrawing,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: ProgressLabel,Yu Gothic,24,&H0018191C,&H0018191C,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,4,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    layout = bar["layout"]
    if layout == "duration":
        return header + "\n".join(_duration_progress_events(sections, bar)) + "\n"
    if layout != "label_fit":
        raise ValueError("progress_bar.layout只允许label_fit或duration")
    dialogue: list[str] = []
    for current, section in enumerate(sections):
        start, end = float(section["start"]), float(section["end"])
        label = current_label(section)
        requested = label_width(label)
        bounds = active_segment_bounds(len(sections), current, requested, x=bar["x"], width=bar["width"])
        for index, (x0, x1) in enumerate(bounds):
            active = index == current
            color = "5DC9F4" if index <= current else "FFFFFF"
            alpha = "18" if active else ("35" if index < current else "A0")
            shape = _rectangle(x0, top if active else bar["y"], x1,
                               bottom if active else bar["y"] + bar["height"])
            dialogue.append(_dialogue(
                10, start, end, "ProgressDrawing",
                r"{\an7\pos(0,0)\p1\bord0\shad0" + f"\\1c&H{color}&\\alpha&H{alpha}&" + "}" + shape,
            ))
        x0, x1 = bounds[current]
        duration_ms = max(1, round((end - start) * 1000))
        dialogue.append(_dialogue(
            11, start, end, "ProgressDrawing",
            f"{{\\an7\\pos({x0},{bottom - 3})\\p1\\bord0\\shad0\\1c&HFFFFFF&"
            f"\\fscx0\\t(0,{duration_ms},1,\\fscx100)}}" + _rectangle(0, 0, x1 - x0, 3),
        ))
        scale = min(100, (x1 - x0 - 16) / max(1, requested - 16) * 100)
        dialogue.append(_dialogue(
            12, start, end, "ProgressLabel",
            f"{{\\an4\\pos({x0 + 8},{(top + bottom) / 2 - 1:g})\\fscx{scale:g}}}" + _ass_escape(label),
        ))
    return header + "\n".join(dialogue) + "\n"
def progress_ass_path(concert_dir: Path, manifest: dict[str, Any]) -> Path:
    concert_id = validate_filename_component(
        str(manifest["concert_id"]),
        "concert_id",
    )
    return concert_dir / "run" / "evidence" / f"{concert_id}.{PROGRESS_SUFFIX}"


def write_song_progress_ass(concert_dir: Path) -> Path:
    concert_dir = concert_dir.resolve()
    manifest = load_manifest(concert_dir)
    output_path = progress_ass_path(concert_dir, manifest)
    write_text(
        output_path,
        render_song_progress_ass(manifest),
        encoding="utf-8",
        newline="\r\n",
    )
    return output_path


def song_progress_ass_failures(
    manifest: dict[str, Any],
    actual: str | None,
) -> list[str]:
    if actual is None:
        return ["missing_song_progress_ass"]
    expected = render_song_progress_ass(manifest)
    if actual.replace("\r\n", "\n") != expected:
        return ["song_progress_ass_stale_or_modified"]
    return []


def validate_song_progress_ass(concert_dir: Path) -> list[str]:
    concert_dir = concert_dir.resolve()
    manifest = load_manifest(concert_dir)
    output_path = progress_ass_path(concert_dir, manifest)
    if not output_path.is_file():
        return song_progress_ass_failures(manifest, None)
    try:
        actual = output_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ["song_progress_ass_not_utf8"]
    return song_progress_ass_failures(manifest, actual)


def _filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    return value.replace(":", r"\:").replace("'", r"\'")


def _video_inputs(
    concert_dir: Path,
    *,
    review: bool = False,
) -> tuple[dict[str, Any], Path, Path, Path]:
    from merge_concert_subtitles import collect_absolute_events, render_ass

    manifest, events, failures = collect_absolute_events(concert_dir, True)
    if failures:
        codes = ",".join(str(item.get("code", "unknown")) for item in failures)
        raise ValueError("全场合并gate失败：" + codes)
    source = (
        resolve_review_media(manifest, concert_dir)
        if review
        else Path(manifest["source_media"]).resolve()
    )
    caption_ass = concert_dir / "output" / f"{manifest['concert_id']}.ass"
    progress_ass = progress_ass_path(concert_dir, manifest)
    if not source.is_file():
        raise FileNotFoundError(f"源视频不存在：{source}")
    if not caption_ass.is_file():
        raise FileNotFoundError(f"正式字幕ASS不存在：{caption_ass}")
    caption_text = caption_ass.read_text(encoding="utf-8-sig")
    if caption_text.replace("\r\n", "\n") != render_ass(
        events,
        manifest["concert_id"],
    ):
        raise ValueError("正式字幕ASS与当前已批准事件不一致")
    failures = validate_song_progress_ass(concert_dir)
    if failures:
        raise ValueError("进度图形层校验失败：" + ",".join(failures))
    return manifest, source, caption_ass, progress_ass


def _subtitle_filter(caption_ass: Path, progress_ass: Path) -> str:
    return (
        f"subtitles=filename='{_filter_path(caption_ass)}',"
        f"subtitles=filename='{_filter_path(progress_ass)}'"
    )


def render_final_video(
    concert_dir: Path,
    output_path: Path | None = None,
    ffmpeg_value: str | None = None,
    crf: int = 18,
    preset: str = "medium",
    encoder: str = "auto",
) -> Path:
    from common import file_fingerprint, file_sha256, write_json
    from gpu_queue import run_queued
    from render_policy import (
        default_destination, encoder_arguments, queue_directory,
        select_encoder, verify_encoded_media,
    )

    concert_dir = concert_dir.resolve()
    manifest, source, caption_ass, progress_ass = _video_inputs(concert_dir)
    ffmpeg = resolve_binary("ffmpeg", ffmpeg_value)
    destination = default_destination(concert_dir, source, manifest["concert_id"], output_path)
    # Bind the render to the exact validated inputs; queue waiting cannot approve stale data
    inputs = {
        "manifest_sha256": canonical_hash(manifest),
        "source": file_fingerprint(source),
        "caption_sha256": file_sha256(caption_ass),
        "progress_sha256": file_sha256(progress_ass),
    }

    def ensure_current() -> None:
        current, _, _, _ = _video_inputs(concert_dir)
        if (canonical_hash(current) != inputs["manifest_sha256"]
                or file_fingerprint(source) != inputs["source"]
                or file_sha256(caption_ass) != inputs["caption_sha256"]
                or file_sha256(progress_ass) != inputs["progress_sha256"]):
            raise ValueError("压制输入已变化，停止使用过期批准结果")

    chosen, probes = select_encoder(ffmpeg, concert_dir, encoder, crf, preset)
    print(f"视频编码器：{chosen}；输出：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
        "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", _subtitle_filter(caption_ass, progress_ass),
        *encoder_arguments(chosen, crf, preset),
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(destination),
    ]
    receipt_path = concert_dir / "run" / "evidence" / "render-receipt.json"
    receipt = {
        **inputs, "output": str(destination), "encoder": chosen, "probes": probes,
        "quality_value": crf, "command": command, "status": "running",
    }
    write_json(receipt_path, receipt)
    try:
        if chosen == "libx264":
            ensure_current()
            subprocess.run(command, check=True)
        else:
            code = run_queued(
                concert_dir / "run" / "runtime", "final-video-render", command,
                on_acquired=ensure_current, queue_dir_override=queue_directory(concert_dir),
            )
            if code:
                raise subprocess.CalledProcessError(code, command)
        media = verify_encoded_media(ffmpeg, destination)
        ensure_current()
        receipt.update(status="completed", output_sha256=file_sha256(destination),
                       output_size=destination.stat().st_size,
                       duration_seconds=float(media["format"]["duration"]))
        write_json(receipt_path, receipt)
    except Exception as error:
        receipt.update(status="failed", error=str(error))
        write_json(receipt_path, receipt)
        raise
    return destination
def review_windows(manifest: dict[str, Any], concert_dir: Path | None = None) -> list[tuple[float, float]]:
    windows: list[tuple[float, float]] = []
    for song in timeline_sections(manifest):
        start = float(song["start"])
        end = float(song["end"])
        duration = end - start
        candidates = [
            (start, min(end, start + 4.0)),
            (
                max(start, start + duration / 2 - 0.75),
                min(end, start + duration / 2 + 0.75),
            ),
            (max(start, end - 2.0), end),
        ]
        for candidate_start, candidate_end in candidates:
            if candidate_end - candidate_start >= 0.2:
                windows.append((candidate_start, candidate_end))
    merged: list[tuple[float, float]] = []
    for start, end in windows:
        if merged and start <= merged[-1][1] + 0.01:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def merge_review_ass(caption_text: str, progress_text: str) -> str:
    caption_lines = caption_text.replace("\r\n", "\n").splitlines()
    progress_lines = progress_text.replace("\r\n", "\n").splitlines()
    try:
        events_index = caption_lines.index("[Events]")
    except ValueError as error:
        raise ValueError("正式字幕ASS缺少[Events]") from error
    existing_styles = {
        line.split(",", 1)[0]
        for line in caption_lines
        if line.startswith("Style:")
    }
    progress_styles = [
        line
        for line in progress_lines
        if line.startswith("Style:") and line.split(",", 1)[0] not in existing_styles
    ]
    progress_events = [
        line for line in progress_lines if line.startswith("Dialogue:")
    ]
    merged = (
        caption_lines[:events_index]
        + progress_styles
        + caption_lines[events_index:]
        + progress_events
    )
    return "\n".join(merged).rstrip() + "\n"


def write_review_ass(
    concert_dir: Path,
    output_path: Path | None = None,
) -> Path:
    concert_dir = concert_dir.resolve()
    manifest, source, caption_ass, progress_ass = _video_inputs(
        concert_dir,
        review=True,
    )
    windows = review_windows(manifest, concert_dir)
    destination = (
        output_path.resolve()
        if output_path
        else concert_dir / "run" / "review" / "song-progress-review.ass"
    )
    if destination.suffix.lower() != ".ass":
        raise ValueError("进度预览只输出单一外挂ASS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_text(
        destination,
        merge_review_ass(
            caption_ass.read_text(encoding="utf-8-sig"),
            progress_ass.read_text(encoding="utf-8-sig"),
        ),
        encoding="utf-8-sig",
        newline="\n",
    )
    windows_path = destination.with_name(f"{destination.stem}-windows.tsv")
    rows = ["index\tstart\tend\tsource\tsubtitle"]
    rows.extend(
        f"{index}\t{start:.3f}\t{end:.3f}\t{source}\t{destination}"
        for index, (start, end) in enumerate(windows, start=1)
    )
    write_text(windows_path, "\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成并烧录演唱会歌曲细进度条")
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("build", "validate", "render", "review"):
        action_parser = subparsers.add_parser(action)
        action_parser.add_argument("--concert-dir", required=True, type=Path)
        if action in {"render", "review"}:
            action_parser.add_argument("--output", type=Path)
        if action == "render":
            action_parser.add_argument("--ffmpeg")
            action_parser.add_argument("--crf", type=int, default=18)
            action_parser.add_argument("--preset", default="medium")
            action_parser.add_argument("--encoder", choices=("auto", "cpu", "h264_nvenc", "h264_qsv", "h264_amf"), default="auto")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console_version("song_progress")
    if args.action == "build":
        print(f"ass={write_song_progress_ass(args.concert_dir)}")
        return 0
    if args.action == "validate":
        failures = validate_song_progress_ass(args.concert_dir)
        print(f"result={'failed' if failures else 'passed'}")
        for failure in failures:
            print(f"failure={failure}")
        return 2 if failures else 0
    if args.action == "render":
        output = render_final_video(
            args.concert_dir,
            args.output,
            args.ffmpeg,
            args.crf,
            args.preset,
            args.encoder,
        )
        print(f"video={output}")
        return 0
    output = write_review_ass(
        args.concert_dir,
        args.output,
    )
    print(f"review_ass={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from common import VERSION
from lyrics_source import is_ascii_chant, is_metadata, parse_lrc


ASS_TAG = re.compile(r"\{[^}]*\}")
SRT_TIME = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{3})"
)
ASS_TIME = re.compile(r"(?P<h>\d+):(?P<m>\d{2}):(?P<s>\d{2}(?:\.\d+)?)")


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    chinese: str
    japanese: str


def _srt_seconds(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _ass_seconds(value: str) -> float:
    match = ASS_TIME.fullmatch(value.strip())
    if not match:
        raise ValueError(f"非法ASS时间：{value}")
    return (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + float(match.group("s"))
    )


def _clean_line(value: str) -> str:
    return ASS_TAG.sub("", value).strip()


def _is_japanese(value: str) -> bool:
    return any("\u3040" <= character <= "\u30ff" for character in value)


def _bilingual(lines: list[str]) -> tuple[str, str]:
    cleaned = [_clean_line(line) for line in lines if _clean_line(line)]
    if len(cleaned) != 2:
        raise ValueError("每条字幕必须恰好包含中文和日文两行")
    first, second = cleaned
    if _is_japanese(first) and not _is_japanese(second):
        return second, first
    return first, second


def parse_srt(path: Path) -> list[Cue]:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        time_index = next(
            (index for index, line in enumerate(lines) if SRT_TIME.fullmatch(line.strip())),
            None,
        )
        if time_index is None:
            continue
        match = SRT_TIME.fullmatch(lines[time_index].strip())
        assert match is not None
        body = "\n".join(lines[time_index + 1 :]).replace(r"\N", "\n")
        chinese, japanese = _bilingual(body.splitlines())
        cues.append(
            Cue(
                _srt_seconds(match.group("start")),
                _srt_seconds(match.group("end")),
                chinese,
                japanese,
            )
        )
    return validate_cues(cues)


def parse_ass(path: Path) -> list[Cue]:
    cues: list[Cue] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) != 10:
            raise ValueError("ASS Dialogue字段不足")
        body = fields[9].replace(r"\N", "\n").replace(r"\n", "\n")
        chinese, japanese = _bilingual(body.splitlines())
        cues.append(
            Cue(
                _ass_seconds(fields[1]),
                _ass_seconds(fields[2]),
                chinese,
                japanese,
            )
        )
    return validate_cues(cues)


def load_cues(path: Path) -> list[Cue]:
    suffix = path.suffix.lower()
    if suffix == ".srt":
        return parse_srt(path)
    if suffix in {".ass", ".ssa"}:
        return parse_ass(path)
    raise ValueError("审定字幕只支持SRT、ASS或SSA")


def validate_cues(cues: list[Cue]) -> list[Cue]:
    if not cues:
        raise ValueError("审定字幕没有可用事件")
    previous_end = -1.0
    for index, cue in enumerate(cues, start=1):
        if cue.start < 0 or cue.end <= cue.start:
            raise ValueError(f"第{index}条字幕时间非法")
        if cue.start < previous_end - 0.001:
            raise ValueError(f"第{index}条字幕与上一条重叠")
        if not cue.chinese or not cue.japanese:
            raise ValueError(f"第{index}条字幕缺少译文")
        previous_end = cue.end
    return cues


def _nearest_line(
    timestamp: int,
    lines: list[tuple[int, str]],
    tolerance_ms: int = 350,
) -> str | None:
    candidates = [
        (abs(item_timestamp - timestamp), text)
        for item_timestamp, text in lines
        if abs(item_timestamp - timestamp) <= tolerance_ms
    ]
    return min(candidates, default=(0, None), key=lambda item: item[0])[1]


def merge_lrc(
    original_path: Path,
    chinese_path: Path,
    duration: float,
) -> list[Cue]:
    originals = [
        line
        for line in parse_lrc(original_path.read_text(encoding="utf-8-sig"))
        if not is_metadata(line.text)
    ]
    chinese = [
        (line.timestamp, line.text)
        for line in parse_lrc(chinese_path.read_text(encoding="utf-8-sig"))
        if not is_metadata(line.text)
    ]
    if not originals:
        raise ValueError("网易云原文LRC没有可用歌词")
    cues: list[Cue] = []
    missing: list[str] = []
    for index, line in enumerate(originals):
        translated = _nearest_line(line.timestamp, chinese)
        if translated is None and is_ascii_chant(line.text):
            translated = line.text
        if translated is None:
            minutes, remainder = divmod(line.timestamp, 60000)
            missing.append(f"{minutes:02d}:{remainder / 1000:05.2f}")
            continue
        start = line.timestamp / 1000
        if index + 1 < len(originals):
            next_start = originals[index + 1].timestamp / 1000
            end = max(start + 0.05, next_start - 0.05)
        else:
            end = min(duration, start + 6.0)
        cues.append(Cue(start, end, translated, line.text))
    if missing:
        raise ValueError("中文歌词缺少这些时间点：" + "、".join(missing))
    return validate_cues(cues)


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, fraction = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{fraction:03d}"


def render_srt(cues: list[Cue]) -> str:
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{_srt_time(cue.start)} --> {_srt_time(cue.end)}",
                    cue.chinese,
                    cue.japanese,
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"


def _ass_time(seconds: float) -> str:
    centiseconds = int(round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole_seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{fraction:02d}"


def _escape(value: str) -> str:
    return value.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def display_units(value: str) -> float:
    return sum(
        1.0 if unicodedata.east_asian_width(character) in {"W", "F", "A"} else 0.55
        for character in value
    )


def horizontal_scale(value: str, safe_units: float) -> int:
    units = display_units(value)
    if units <= safe_units:
        return 100
    required = int(safe_units / units * 100)
    if required < 55:
        raise ValueError(f"字幕过宽，即使压缩到55%仍不可读：{value}")
    return min(100, required)


def render_ass(
    cues: list[Cue],
    title: str,
    *,
    play_res_x: int = 1920,
    play_res_y: int = 1080,
) -> str:
    header = f"""[Script Info]
; Generated by music-video-subtitle v{VERSION}
Title: {title}
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Bilingual,Microsoft YaHei,57,&H00FFFFFF,&H0000FFFF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,80,80,58,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    for cue in cues:
        chinese = _escape(cue.chinese)
        japanese = _escape(cue.japanese)
        chinese_scale = horizontal_scale(cue.chinese, 38)
        japanese_scale = horizontal_scale(cue.japanese, 56)
        text = (
            f"{{\\fnMicrosoft YaHei\\fs80\\fscx{chinese_scale}\\c&H007AD7FF&}}{chinese}"
            r"\N"
            f"{{\\fnYu Gothic\\fs57\\fscx{japanese_scale}\\c&H00FFFFFF&}}{japanese}"
        )
        events.append(
            f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},"
            f"Bilingual,,0,0,0,,{text}"
        )
    return header + "\n".join(events) + "\n"

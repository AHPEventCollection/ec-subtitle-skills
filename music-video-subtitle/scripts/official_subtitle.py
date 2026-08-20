from __future__ import annotations

import argparse
import csv
import html
import re
from dataclasses import dataclass
from pathlib import Path

from common import ensure_workspace, write_text
from subtitle_io import Cue, load_cues, render_srt


SUBTITLE_SUFFIXES = {".vtt", ".srt", ".ass", ".ssa"}
JAPANESE_NAME = re.compile(r"(?:^|[._-])(?:ja|jpn)(?:[._-]|$)", re.IGNORECASE)
TIMELINE = re.compile(
    r"(?P<start>(?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{2,3})\s*-->\s*"
    r"(?P<end>(?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{2,3})"
)
ASS_TIME = re.compile(r"(?P<h>\d+):(?P<m>\d{2}):(?P<s>\d{2}(?:\.\d+)?)")
HTML_TAG = re.compile(r"<[^>]+>")
INLINE_VTT_TIME = re.compile(r"<\d{1,2}:\d{2}:\d{2}[.,]\d{3}>")
ASS_TAG = re.compile(r"\{[^}]*\}")


@dataclass(frozen=True)
class OfficialCue:
    start_ms: int
    end_ms: int
    japanese: str


def _timestamp_ms(value: str) -> int:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"官方字幕时间非法：{value}")
    return round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)


def _ass_timestamp_ms(value: str) -> int:
    match = ASS_TIME.fullmatch(value.strip())
    if not match:
        raise ValueError(f"官方ASS时间非法：{value}")
    return round(
        (
            int(match.group("h")) * 3600
            + int(match.group("m")) * 60
            + float(match.group("s"))
        )
        * 1000
    )


def _clean_lines(lines: list[str]) -> str:
    cleaned: list[str] = []
    for raw in lines:
        value = raw.replace(r"\N", "　").replace(r"\n", "　")
        value = INLINE_VTT_TIME.sub("", value)
        value = ASS_TAG.sub("", value)
        value = HTML_TAG.sub("", value)
        value = html.unescape(value)
        value = re.sub(r"\s+", " ", value).strip()
        if value:
            cleaned.append(value)
    return "　".join(cleaned)


def _validate(cues: list[OfficialCue]) -> list[OfficialCue]:
    if not cues:
        raise ValueError("官方字幕没有可用事件")
    previous_end = -1
    for index, cue in enumerate(cues, start=1):
        if cue.start_ms < 0 or cue.end_ms <= cue.start_ms:
            raise ValueError(f"官方字幕第{index}条时间非法")
        if cue.start_ms < previous_end:
            raise ValueError(f"官方字幕第{index}条与上一条重叠，需人工确认后再使用")
        if not cue.japanese:
            raise ValueError(f"官方字幕第{index}条正文为空")
        previous_end = cue.end_ms
    return cues


def _parse_vtt_or_srt(path: Path) -> list[OfficialCue]:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    cues: list[OfficialCue] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        time_index = next(
            (index for index, line in enumerate(lines) if TIMELINE.search(line)),
            None,
        )
        if time_index is None:
            continue
        match = TIMELINE.search(lines[time_index])
        assert match is not None
        body = _clean_lines(lines[time_index + 1 :])
        if not body:
            continue
        cues.append(
            OfficialCue(
                start_ms=_timestamp_ms(match.group("start")),
                end_ms=_timestamp_ms(match.group("end")),
                japanese=body,
            )
        )
    return _validate(cues)


def _parse_ass(path: Path) -> list[OfficialCue]:
    cues: list[OfficialCue] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) != 10:
            raise ValueError("官方ASS的Dialogue字段不足")
        body = _clean_lines([fields[9]])
        if not body:
            continue
        cues.append(
            OfficialCue(
                start_ms=_ass_timestamp_ms(fields[1]),
                end_ms=_ass_timestamp_ms(fields[2]),
                japanese=body,
            )
        )
    return _validate(cues)


def load_official_cues(path: Path) -> list[OfficialCue]:
    suffix = path.suffix.casefold()
    if suffix in {".vtt", ".srt"}:
        return _parse_vtt_or_srt(path)
    if suffix in {".ass", ".ssa"}:
        return _parse_ass(path)
    raise ValueError(f"不支持的官方字幕格式：{path.suffix}")


def _contains_japanese(cues: list[OfficialCue]) -> bool:
    sample = "".join(cue.japanese for cue in cues[:20])
    return any("\u3040" <= character <= "\u30ff" for character in sample)


def discover_official_subtitle(mv_dir: Path) -> Path | None:
    source_dir = ensure_workspace(mv_dir) / "source"
    candidates = sorted(
        path.resolve()
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.casefold() in SUBTITLE_SUFFIXES
    )
    named = [path for path in candidates if JAPANESE_NAME.search(path.name)]
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        raise ValueError("发现多份官方日文字幕，请用--subtitle明确指定")
    if len(candidates) == 1 and _contains_japanese(load_official_cues(candidates[0])):
        return candidates[0]
    if candidates:
        raise ValueError("发现字幕文件但无法唯一确认官方日文轨，请用--subtitle明确指定")
    return None


def _resolve_subtitle(mv_dir: Path, subtitle: Path | None) -> Path:
    source_dir = (ensure_workspace(mv_dir) / "source").resolve()
    if subtitle is None:
        discovered = discover_official_subtitle(mv_dir)
        if discovered is None:
            raise FileNotFoundError("没有发现官方日文字幕")
        return discovered
    resolved = subtitle.resolve()
    if not resolved.is_file() or resolved.suffix.casefold() not in SUBTITLE_SUFFIXES:
        raise FileNotFoundError(f"官方字幕不存在或格式不支持：{resolved}")
    if resolved.parent != source_dir:
        raise ValueError(f"官方字幕必须位于source目录：{source_dir}")
    return resolved


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("line", "start_ms", "end_ms", "japanese", "chinese"),
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(rows)


def prepare_official_subtitle(mv_dir: Path, subtitle: Path | None = None) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    source = _resolve_subtitle(mv_dir, subtitle)
    cues = load_official_cues(source)
    if not _contains_japanese(cues):
        raise ValueError("指定字幕未检测到日文正文")
    review_dir = mv_dir / "review" / "official-subtitle"
    rows = [
        {
            "line": str(index),
            "start_ms": str(cue.start_ms),
            "end_ms": str(cue.end_ms),
            "japanese": cue.japanese,
            "chinese": "",
        }
        for index, cue in enumerate(cues, start=1)
    ]
    source_rows = review_dir / "source-cues.tsv"
    translation = review_dir / "translation.tsv"
    _write_rows(source_rows, rows)
    _write_rows(translation, rows)
    write_text(
        review_dir / "audit.md",
        "\n".join(
            [
                "# 官方字幕基准",
                "",
                f"- 来源文件　{source.name}",
                f"- 官方事件　{len(cues)}",
                "- 正文基准　官方日文字幕",
                "- 时间轴基准　官方字幕逐事件起止时间",
                "- 中文规则　只填写translation.tsv的chinese列，不得修改其他列",
                "- 机器对齐　跳过SOFA、Whisper和Combined主流程",
                "",
            ]
        ),
    )
    return translation


def prepare_official_subtitle_if_present(mv_dir: Path) -> Path | None:
    try:
        subtitle = discover_official_subtitle(mv_dir)
    except ValueError as error:
        review_dir = ensure_workspace(mv_dir) / "review" / "official-subtitle"
        write_text(review_dir / "selection-required.md", f"# 官方字幕需要选择\n\n{error}\n")
        return None
    if subtitle is None:
        return None
    return prepare_official_subtitle(mv_dir, subtitle)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少官方字幕工序文件：{path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    expected = {"line", "start_ms", "end_ms", "japanese", "chinese"}
    if not rows or set(rows[0]) != expected:
        raise ValueError(f"官方字幕TSV字段必须是：{','.join(sorted(expected))}")
    return rows


def build_official_candidate(mv_dir: Path) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    review_dir = mv_dir / "review" / "official-subtitle"
    source_rows = _read_rows(review_dir / "source-cues.tsv")
    translated_rows = _read_rows(review_dir / "translation.tsv")
    if len(source_rows) != len(translated_rows):
        raise ValueError("中文翻译事件数与官方字幕不一致")
    cues: list[Cue] = []
    for index, (source, translated) in enumerate(zip(source_rows, translated_rows), start=1):
        protected = ("line", "start_ms", "end_ms", "japanese")
        if any(source[field] != translated[field] for field in protected):
            raise ValueError(f"第{index}条修改了官方正文或时间轴，只允许填写chinese列")
        chinese = translated["chinese"].strip()
        if not chinese:
            raise ValueError(f"第{index}条缺少中文翻译")
        cues.append(
            Cue(
                start=int(source["start_ms"]) / 1000,
                end=int(source["end_ms"]) / 1000,
                chinese=chinese,
                japanese=source["japanese"],
            )
        )
    candidate_dir = mv_dir / "review" / "alignment" / "candidates"
    candidate = candidate_dir / "official-subtitle.srt"
    write_text(candidate, render_srt(cues))
    write_text(
        mv_dir / "review" / "alignment" / "candidate-check-official.md",
        "\n".join(
            [
                "# 官方字幕候选检查",
                "",
                f"- 事件数　{len(cues)}",
                "- 日文正文与官方字幕一致　通过",
                "- 起止时间与官方字幕逐条一致　通过",
                "- 中文翻译逐条齐全　通过",
                "- SOFA／Whisper机器时间轴　未使用",
                "- 人工语义复核　必需",
                "",
            ]
        ),
    )
    return candidate


def validate_official_candidate(mv_dir: Path, candidate: Path) -> None:
    mv_dir = ensure_workspace(mv_dir)
    review_dir = mv_dir / "review" / "official-subtitle"
    source_rows = _read_rows(review_dir / "source-cues.tsv")
    translated_rows = _read_rows(review_dir / "translation.tsv")
    candidate_cues = load_cues(candidate)
    if len(source_rows) != len(translated_rows) or len(source_rows) != len(candidate_cues):
        raise ValueError("官方候选事件数与冻结基准不一致")
    for index, (source, translated, cue) in enumerate(
        zip(source_rows, translated_rows, candidate_cues),
        start=1,
    ):
        expected = (
            int(source["start_ms"]),
            int(source["end_ms"]),
            source["japanese"],
            translated["chinese"].strip(),
        )
        actual = (
            round(cue.start * 1000),
            round(cue.end * 1000),
            cue.japanese,
            cue.chinese,
        )
        if actual != expected:
            raise ValueError(f"第{index}条官方候选与冻结正文、时间轴或中文翻译不一致")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="官方日文字幕优先与中文逐事件对齐")
    subparsers = parser.add_subparsers(dest="action", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--mv-dir", required=True, type=Path)
    prepare.add_argument("--subtitle", type=Path)
    build = subparsers.add_parser("build")
    build.add_argument("--mv-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "prepare":
            result = prepare_official_subtitle(args.mv_dir, args.subtitle)
        else:
            result = build_official_candidate(args.mv_dir)
    except Exception as error:
        print(f"error={error}")
        return 2
    print(f"result={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

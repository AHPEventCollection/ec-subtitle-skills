from __future__ import annotations

import argparse
import csv
import html
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from chinese_source import record_chinese_source
from common import MEDIA_SUFFIXES, ensure_workspace, find_source_video, write_text
from lyrics_source import Candidate, lyric_lines
from subtitle_io import Cue, format_review_timestamp, load_cues, render_srt


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


@dataclass(frozen=True)
class ReferenceMatch:
    official_line: int
    netease_line: int | None
    netease_timestamp_ms: int | None
    netease_japanese: str
    netease_chinese: str
    similarity: float
    position_delta: float
    projected_live_start_ms: int | None
    timing_delta_ms: int | None
    timing_hint: str
    netease_line_end: int | None = None


REFERENCE_ACCEPT_SIMILARITY = 0.72
TIMING_REVIEW_THRESHOLD_MS = 750


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


def _write_chinese_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("line", "chinese"),
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(
            {"line": row["line"], "chinese": row["chinese"]} for row in rows
        )


def _normalize_lyric(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith(("P", "S"))
    )


def _text_similarity(left: str, right: str) -> float:
    normalized_left = _normalize_lyric(left)
    normalized_right = _normalize_lyric(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0
    ratio = SequenceMatcher(None, normalized_left, normalized_right).ratio()
    if normalized_left in normalized_right or normalized_right in normalized_left:
        ratio = max(
            ratio,
            min(len(normalized_left), len(normalized_right))
            / max(len(normalized_left), len(normalized_right)),
        )
    return ratio


def _translation_for_timestamp(candidate: Candidate, timestamp: int) -> str:
    translations = lyric_lines(candidate.translation)
    nearby = [
        line for line in translations if abs(line.timestamp - timestamp) <= 500
    ]
    if not nearby:
        return ""
    return min(nearby, key=lambda line: abs(line.timestamp - timestamp)).text


def match_netease_reference(
    cues: list[OfficialCue], candidate: Candidate
) -> list[ReferenceMatch]:
    originals = lyric_lines(candidate.original)
    if not originals:
        return [
            ReferenceMatch(index, None, None, "", "", 0.0, 1.0, None, None, "unmatched")
            for index in range(1, len(cues) + 1)
        ]

    official_count = len(cues)
    lyric_count = len(originals)
    official_first = cues[0].start_ms
    official_span = max(1, cues[-1].start_ms - official_first)
    lyric_first = originals[0].timestamp
    lyric_span = max(1, originals[-1].timestamp - lyric_first)
    gap_penalty = -0.55
    scores = [[0.0] * (lyric_count + 1) for _ in range(official_count + 1)]
    moves = [[""] * (lyric_count + 1) for _ in range(official_count + 1)]
    for index in range(1, official_count + 1):
        scores[index][0] = index * gap_penalty
        moves[index][0] = "up"
    for index in range(1, lyric_count + 1):
        scores[0][index] = index * gap_penalty
        moves[0][index] = "left"

    pair_facts: dict[tuple[int, int, int], tuple[float, float]] = {}
    for official_index, cue in enumerate(cues, start=1):
        for lyric_index, lyric in enumerate(originals, start=1):
            choices = [
                (scores[official_index - 1][lyric_index] + gap_penalty, "up"),
                (scores[official_index][lyric_index - 1] + gap_penalty, "left"),
            ]
            for count in range(1, min(3, lyric_index) + 1):
                group = originals[lyric_index - count:lyric_index]
                joined = "".join(line.text for line in group)
                similarity = _text_similarity(cue.japanese, joined)
                # Join only adjacent lines whose complete Japanese matches.
                # A partial match must never pull in the following lyric.
                if count > 1 and similarity != 1.0:
                    continue
                position_delta = abs(
                    (cue.start_ms - official_first) / official_span
                    - (group[0].timestamp - lyric_first) / lyric_span
                )
                pair_facts[(official_index, lyric_index, count)] = (
                    similarity, position_delta
                )
                match_score = similarity * 2.4 - position_delta * 0.8 - 0.35
                if similarity == 1.0:
                    match_score += 0.6
                choices.append((
                    scores[official_index - 1][lyric_index - count] + match_score,
                    f"diag{count}",
                ))
            scores[official_index][lyric_index], moves[official_index][lyric_index] = max(
                choices, key=lambda item: item[0]
            )

    paired: dict[int, tuple[int, int]] = {}
    official_index = official_count
    lyric_index = lyric_count
    while official_index or lyric_index:
        move = moves[official_index][lyric_index]
        if move.startswith("diag"):
            count = int(move[4:])
            similarity, _ = pair_facts[(official_index, lyric_index, count)]
            if similarity >= 0.55:
                paired[official_index] = (lyric_index - count + 1, lyric_index)
            official_index -= 1
            lyric_index -= count
        elif move == "up":
            official_index -= 1
        elif move == "left":
            lyric_index -= 1
        else:
            break

    # Use only complete, actually paired Japanese lyrics as timing anchors.
    # Credits and unpaired leading/trailing lines must not stretch the song.
    anchors = []
    for index, (first, last) in sorted(paired.items()):
        group = originals[first - 1:last]
        if _normalize_lyric(cues[index - 1].japanese) == _normalize_lyric("".join(row.text for row in group)):
            anchors.append((cues[index - 1].start_ms, group[0].timestamp))
    relative_available = len(anchors) >= 2 and anchors[-1][1] > anchors[0][1]
    if relative_available:
        relative_official_first, relative_lyric_first = anchors[0]
        relative_official_span = anchors[-1][0] - relative_official_first
        relative_lyric_span = anchors[-1][1] - relative_lyric_first

    matches: list[ReferenceMatch] = []
    for index, cue in enumerate(cues, start=1):
        matched_index = paired.get(index)
        if matched_index is None:
            matches.append(
                ReferenceMatch(
                    index,
                    None,
                    None,
                    "",
                    "",
                    0.0,
                    1.0,
                    None,
                    None,
                    "unmatched",
                )
            )
            continue
        first, last = matched_index
        group = originals[first - 1:last]
        lyric = group[0]
        similarity, position_delta = pair_facts[(index, last, last - first + 1)]
        chinese_parts = [
            _translation_for_timestamp(candidate, item.timestamp) for item in group
        ]
        chinese = " ".join(chinese_parts) if all(chinese_parts) else ""
        expected = _normalize_lyric(cue.japanese)
        found = _normalize_lyric("".join(item.text for item in group))
        if expected != found and (expected in found or found in expected):
            chinese = ""  # A matched prefix is not a complete translation.
        projected = None
        timing_delta = None
        timing_hint = "relative-unavailable"
        if relative_available:
            projected = relative_official_first + round(
                (lyric.timestamp - relative_lyric_first) / relative_lyric_span * relative_official_span
            )
            timing_delta = cue.start_ms - projected
            if timing_delta <= -TIMING_REVIEW_THRESHOLD_MS:
                timing_hint = "relative-earlier"
            elif timing_delta >= TIMING_REVIEW_THRESHOLD_MS:
                timing_hint = "relative-later"
            else:
                timing_hint = "within-relative-range"
        matches.append(
            ReferenceMatch(
                official_line=index,
                netease_line=first,
                netease_timestamp_ms=lyric.timestamp,
                netease_japanese="".join(item.text for item in group),
                netease_chinese=chinese,
                similarity=similarity,
                position_delta=position_delta,
                projected_live_start_ms=projected,
                timing_delta_ms=timing_delta,
                timing_hint=timing_hint,
                netease_line_end=last,
            )
        )
    return matches


def _write_reference_rows(
    path: Path,
    matches: list[ReferenceMatch],
    current_chinese: dict[int, str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "official_line",
        "netease_line",
        "netease_line_end",
        "netease_timestamp_ms",
        "netease_japanese",
        "netease_chinese",
        "similarity",
        "position_delta",
        "projected_live_start_ms",
        "timing_delta_ms",
        "timing_hint",
        "accepted_chinese",
        "current_chinese",
        "chinese_same",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for match in matches:
            existing = (current_chinese or {}).get(match.official_line, "").strip()
            accepted = bool(
                match.netease_chinese
                and match.similarity >= REFERENCE_ACCEPT_SIMILARITY
            )
            writer.writerow(
                {
                    "official_line": match.official_line,
                    "netease_line": match.netease_line or "",
                    "netease_line_end": match.netease_line_end or match.netease_line or "",
                    "netease_timestamp_ms": match.netease_timestamp_ms
                    if match.netease_timestamp_ms is not None
                    else "",
                    "netease_japanese": match.netease_japanese,
                    "netease_chinese": match.netease_chinese,
                    "similarity": f"{match.similarity:.3f}",
                    "position_delta": f"{match.position_delta:.3f}",
                    "projected_live_start_ms": match.projected_live_start_ms
                    if match.projected_live_start_ms is not None
                    else "",
                    "timing_delta_ms": match.timing_delta_ms
                    if match.timing_delta_ms is not None
                    else "",
                    "timing_hint": match.timing_hint,
                    "accepted_chinese": "yes" if accepted else "no",
                    "current_chinese": existing,
                    "chinese_same": (
                        "yes"
                        if existing
                        and match.netease_chinese
                        and existing == match.netease_chinese
                        else "no"
                    ),
                }
            )


def prepare_netease_reference(mv_dir: Path, candidate: Candidate) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    review_dir = mv_dir / "review" / "official-subtitle"
    source_rows = _read_rows(review_dir / "source-cues.tsv")
    cues = [
        OfficialCue(
            start_ms=int(row["start_ms"]),
            end_ms=int(row["end_ms"]),
            japanese=row["japanese"],
        )
        for row in source_rows
    ]
    matches = match_netease_reference(cues, candidate)
    chinese_path = review_dir / "chinese.tsv"
    with chinese_path.open("r", encoding="utf-8-sig", newline="") as handle:
        chinese_rows = list(csv.DictReader(handle, delimiter="\t"))
    current_chinese = {
        int(row["line"]): row["chinese"].strip() for row in chinese_rows
    }
    reference = review_dir / "netease-match.tsv"
    _write_reference_rows(reference, matches, current_chinese)
    _write_chinese_rows(
        review_dir / "netease-chinese.tsv",
        [
            {
                "line": str(match.official_line),
                "chinese": (
                    match.netease_chinese
                    if match.similarity >= REFERENCE_ACCEPT_SIMILARITY
                    else ""
                ),
            }
            for match in matches
        ],
    )
    prefilled = 0
    for row, match in zip(chinese_rows, matches, strict=True):
        if (
            not row["chinese"].strip()
            and match.netease_chinese
            and match.similarity >= REFERENCE_ACCEPT_SIMILARITY
        ):
            row["chinese"] = match.netease_chinese
            prefilled += 1
    if prefilled:
        _write_chinese_rows(chinese_path, chinese_rows)

    accepted = sum(
        bool(match.netease_chinese)
        and match.similarity >= REFERENCE_ACCEPT_SIMILARITY
        for match in matches
    )
    timing_review = sum(
        match.timing_hint in {"relative-earlier", "relative-later"}
        for match in matches
    )
    existing_comparable = sum(
        bool(current_chinese.get(match.official_line)) and bool(match.netease_chinese)
        for match in matches
    )
    existing_same = sum(
        current_chinese.get(match.official_line, "").strip()
        == match.netease_chinese.strip()
        for match in matches
        if current_chinese.get(match.official_line) and match.netease_chinese
    )
    original_lines = lyric_lines(candidate.original)
    official_span = cues[-1].end_ms - cues[0].start_ms
    netease_span = (
        original_lines[-1].timestamp - original_lines[0].timestamp
        if len(original_lines) > 1
        else 0
    )
    report = review_dir / "netease-match.md"
    write_text(
        report,
        "\n".join(
            [
                "# 官方字幕与网易云歌词匹配",
                "",
                f"- 官方字幕事件　{len(cues)}",
                f"- 网易云歌词行　{len(original_lines)}",
                f"- 官方字幕覆盖时段　{official_span / 1000:.3f}秒",
                f"- 网易云歌词覆盖时段　{netease_span / 1000:.3f}秒",
                f"- 中文高置信匹配　{accepted}/{len(cues)}",
                f"- 本次新预填中文　{prefilled}",
                f"- 已有中文与网易云逐字相同　{existing_same}/{existing_comparable}",
                f"- 相对时段偏移提示　{timing_review}",
                "- 网易云中文独立表　netease-chinese.tsv",
                "",
                "中文按日文正文相似度、重复顺序与整段相对位置匹配，再继承官方字幕的初始时间轴",
                "网易云时间只用于相对时段比较，不直接覆盖现场版时间",
                "relative-earlier与relative-later只表示录音室版和现场版的相对位置有偏移，不等于实际早起或晚起",
                "真正的早晚入点必须通过实际演唱或独立音频对齐确认，不自动改时间",
                "",
            ]
        ),
    )
    write_review_sheet(mv_dir)
    return report


def write_review_sheet(mv_dir: Path) -> Path:
    directory = mv_dir / "review" / "official-subtitle"
    source = _read_rows(directory / "translation.tsv")
    with (directory / "chinese.tsv").open(encoding="utf-8-sig", newline="") as handle:
        chinese = {row["line"]: row["chinese"] for row in csv.DictReader(handle, delimiter="\t")}
    references = {}
    if (directory / "netease-match.tsv").is_file():
        with (directory / "netease-match.tsv").open(encoding="utf-8-sig", newline="") as handle:
            references = {row["official_line"]: row for row in csv.DictReader(handle, delimiter="\t")}
    repeats: dict[str, list[str]] = {}
    for row in source:
        repeats.setdefault(_normalize_lyric(row["japanese"]), []).append(row["line"])
    lines = [
        "# 整首字幕审阅", "",
        "从头到尾审阅一次，重点修错译、漏译、跨行语义和重复句不一致；正确的现成中文不为更换文风而重写",
        "匹配高置信只表示正文对应，不等于翻译正确；相对时间提示不是实际演唱早晚的判定",
        "只需把修改行写成line、japanese、chinese三列TSV，再用official-subtitle-review --edits一次合并校验；日文须原样填写以绑定行号", "",
        "|行|初始/审定时间|日文|当前中文|重点|", "|---:|---|---|---|---|",
    ]
    def cell(value: str) -> str:
        return value.replace("|", r"\|").replace("\n", "<br>")
    for row in source:
        ref = references.get(row["line"], {})
        text = chinese.get(row["line"], "")
        hints = []
        if not text.strip():
            hints.append("缺中文")
        if ref and ref.get("accepted_chinese") != "yes":
            hints.append("正文匹配需核对")
        if ref.get("netease_line_end") and ref.get("netease_line_end") != ref.get("netease_line"):
            hints.append("网易云相邻句合并")
        same = repeats[_normalize_lyric(row["japanese"])]
        if len(same) > 1:
            hints.append("重复行" + "、".join(same))
            if len({chinese.get(line, "").strip() for line in same}) > 1:
                hints.append("同句译文不同，结合上下文复核")
        if ref.get("timing_hint") in {"relative-earlier", "relative-later"}:
            hints.append("相对位置提示，仅供定位")
        time_text = format_review_timestamp(int(row["start_ms"]) / 1000) + "至" + format_review_timestamp(int(row["end_ms"]) / 1000)
        lines.append(f"|{row['line']}|{time_text}|{cell(row['japanese'])}|{cell(text)}|{'；'.join(hints)}|")
    result = directory / "review.md"
    write_text(result, "\n".join(lines) + "\n")
    return result


def write_netease_reference_failure(mv_dir: Path, error: Exception) -> Path:
    path = ensure_workspace(mv_dir) / "review" / "official-subtitle" / "netease-match.md"
    write_text(
        path,
        "\n".join(
            [
                "# 官方字幕与网易云歌词匹配",
                "",
                "- 结果　网易云歌词参考未取得",
                f"- 错误　{error}",
                "",
                "官方字幕仍可作为初始候选，但中文不得假称来自网易云音乐",
                "",
            ]
        ),
    )
    return path


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
    chinese = review_dir / "chinese.tsv"
    _write_rows(source_rows, rows)
    _write_rows(translation, rows)
    _write_chinese_rows(chinese, rows)
    write_text(
        review_dir / "audit.md",
        "\n".join(
            [
                "# 官方字幕基准",
                "",
                f"- 来源文件　{source.name}",
                f"- 官方事件　{len(cues)}",
                "- 正文基准　官方日文字幕",
                "- 初始时间轴　官方字幕逐事件起止时间",
                "- 中文参考　仍需查询网易云日中歌词并按正文、条数和相对时段匹配",
                "- 中文规则　优先采用高置信网易云中文，缺失或不匹配时再补模型翻译",
                "- 时间规则　明显早起或晚起必须在translation.tsv修正并保留差异审计",
                "- 冻结基准　source-cues.tsv中的行号、日文正文和原始时间不得手工修改",
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
        write_text(
            review_dir / "selection-required.md", f"# 官方字幕需要选择\n\n{error}\n"
        )
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


def apply_official_translations(
    mv_dir: Path,
    translation_model: str | None = None,
    translations: Path | None = None,
    edits: Path | None = None,
) -> Path:
    if translations is not None and edits is not None:
        raise ValueError("完整中文输入与修改行不能同时提供")
    mv_dir = ensure_workspace(mv_dir)
    model_name = (translation_model or "").strip()
    review_dir = mv_dir / "review" / "official-subtitle"
    source_rows = _read_rows(review_dir / "source-cues.tsv")
    current_rows = _read_rows(review_dir / "translation.tsv")
    if len(current_rows) != len(source_rows):
        raise ValueError("官方字幕工作表事件数与来源不一致")
    for index, (source, current) in enumerate(
        zip(source_rows, current_rows, strict=True), start=1
    ):
        if any(source[field] != current[field] for field in ("line", "japanese")):
            raise ValueError(f"第{index}条修改了冻结的行号或日文正文")
    input_path = translations.resolve() if translations else review_dir / "chinese.tsv"
    if not input_path.is_file():
        raise FileNotFoundError(f"缺少中文翻译输入：{input_path}")
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        chinese_rows = list(csv.DictReader(handle, delimiter="\t"))
    if not chinese_rows or set(chinese_rows[0]) != {"line", "chinese"}:
        raise ValueError("中文翻译输入字段必须是line和chinese")
    if len(chinese_rows) != len(source_rows):
        raise ValueError("中文翻译事件数与官方字幕不一致")
    if edits is not None:
        with edits.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if set(reader.fieldnames or []) != {"line", "japanese", "chinese"}:
                raise ValueError("修改行字段必须是line、japanese、chinese")
            changed = list(reader)
        seen: set[str] = set()
        by_line = {row["line"]: row for row in source_rows}
        input_by_line = {row["line"]: row for row in chinese_rows}
        for edit in changed:
            line = edit["line"]
            if line in seen or line not in by_line:
                raise ValueError(f"修改行号重复或不存在：{line}")
            if edit["japanese"] != by_line[line]["japanese"]:
                raise ValueError(f"第{line}条修改行与官方日文不一致")
            if not edit["chinese"].strip():
                raise ValueError(f"第{line}条修改行缺少中文")
            seen.add(line)
            input_by_line[line]["chinese"] = edit["chinese"].strip()
    translated_rows: list[dict[str, str]] = []
    for index, (source, current, chinese) in enumerate(
        zip(source_rows, current_rows, chinese_rows, strict=True),
        start=1,
    ):
        if chinese["line"] != source["line"]:
            raise ValueError(f"第{index}条行号与官方字幕不一致")
        value = chinese["chinese"].strip()
        if not value:
            raise ValueError(f"第{index}条缺少中文翻译")
        translated_rows.append({**current, "chinese": value})
    translation = review_dir / "translation.tsv"
    netease_by_line: dict[str, str] = {}
    reference = review_dir / "netease-match.tsv"
    if reference.is_file():
        with reference.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                if row.get("accepted_chinese") == "yes" and row.get(
                    "netease_chinese", ""
                ).strip():
                    netease_by_line[row["official_line"]] = row[
                        "netease_chinese"
                    ].strip()
    netease_used = sum(
        bool(netease_by_line.get(row["line"]))
        and _normalize_lyric(row["chinese"])
        == _normalize_lyric(netease_by_line[row["line"]])
        for row in translated_rows
    )
    if netease_used != len(translated_rows) and not model_name:
        raise ValueError("存在非网易云中文，必须填写该次实际使用的翻译模型名")
    _write_rows(translation, translated_rows)
    if netease_used == len(translated_rows):
        record_chinese_source(mv_dir, "netease")
    else:
        record_chinese_source(
            mv_dir,
            "hybrid" if netease_used else "translation-model",
            model_name,
        )
    _write_chinese_rows(review_dir / "chinese.tsv", translated_rows)
    write_review_sheet(mv_dir)
    return translation


def build_official_candidate(mv_dir: Path) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    review_dir = mv_dir / "review" / "official-subtitle"
    source_rows = _read_rows(review_dir / "source-cues.tsv")
    translated_rows = _read_rows(review_dir / "translation.tsv")
    if len(source_rows) != len(translated_rows):
        raise ValueError("中文翻译事件数与官方字幕不一致")
    cues: list[Cue] = []
    timing_changes = 0
    previous_end = -1
    for index, (source, translated) in enumerate(
        zip(source_rows, translated_rows), start=1
    ):
        protected = ("line", "japanese")
        if any(source[field] != translated[field] for field in protected):
            raise ValueError(f"第{index}条修改了冻结的行号或日文正文")
        try:
            start_ms = int(translated["start_ms"])
            end_ms = int(translated["end_ms"])
        except ValueError as error:
            raise ValueError(f"第{index}条修订时间不是整数毫秒") from error
        if start_ms < 0 or end_ms <= start_ms:
            raise ValueError(f"第{index}条修订时间非法")
        if start_ms < previous_end:
            raise ValueError(f"第{index}条修订时间与上一条重叠")
        previous_end = end_ms
        if start_ms != int(source["start_ms"]) or end_ms != int(source["end_ms"]):
            timing_changes += 1
        chinese = translated["chinese"].strip()
        if not chinese:
            raise ValueError(f"第{index}条缺少中文翻译")
        cues.append(
            Cue(
                start=start_ms / 1000,
                end=end_ms / 1000,
                chinese=chinese,
                japanese=translated["japanese"],
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
                f"- 相对官方字幕的时间修订　{timing_changes}条",
                "- 修订时间合法且零重叠　通过",
                "- 中文逐条齐全　通过",
                "- SOFA／Whisper机器时间轴　未使用",
                "- 网易云匹配、语义与早晚入点人工复核　必需",
                "",
            ]
        ),
    )
    if any(
        path.is_file() and path.suffix.casefold() in MEDIA_SUFFIXES
        for path in (mv_dir / "source").iterdir()
    ):
        from song_candidate import _prepare_candidate_preview

        _prepare_candidate_preview(mv_dir, find_source_video(mv_dir), candidate)
    return candidate


def validate_official_candidate(mv_dir: Path, candidate: Path) -> None:
    mv_dir = ensure_workspace(mv_dir)
    review_dir = mv_dir / "review" / "official-subtitle"
    source_rows = _read_rows(review_dir / "source-cues.tsv")
    translated_rows = _read_rows(review_dir / "translation.tsv")
    candidate_cues = load_cues(candidate)
    if len(source_rows) != len(translated_rows) or len(source_rows) != len(
        candidate_cues
    ):
        raise ValueError("官方候选事件数与冻结基准不一致")
    previous_end = -1
    for index, (source, translated, cue) in enumerate(
        zip(source_rows, translated_rows, candidate_cues),
        start=1,
    ):
        if any(source[field] != translated[field] for field in ("line", "japanese")):
            raise ValueError(f"第{index}条修改了冻结的行号或日文正文")
        start_ms = int(translated["start_ms"])
        end_ms = int(translated["end_ms"])
        if start_ms < 0 or end_ms <= start_ms or start_ms < previous_end:
            raise ValueError(f"第{index}条审定时间非法或与上一条重叠")
        previous_end = end_ms
        expected = (
            start_ms,
            end_ms,
            translated["japanese"],
            translated["chinese"].strip(),
        )
        actual = (
            round(cue.start * 1000),
            round(cue.end * 1000),
            cue.japanese,
            cue.chinese,
        )
        if actual != expected:
            raise ValueError(f"第{index}条官方候选与审定工作表不一致")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="官方日文字幕优先与中文逐事件对齐")
    subparsers = parser.add_subparsers(dest="action", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--mv-dir", required=True, type=Path)
    prepare.add_argument("--subtitle", type=Path)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--mv-dir", required=True, type=Path)
    apply.add_argument("--translations", type=Path)
    apply.add_argument("--translation-model")
    build = subparsers.add_parser("build")
    build.add_argument("--mv-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "prepare":
            result = prepare_official_subtitle(args.mv_dir, args.subtitle)
        elif args.action == "apply":
            result = apply_official_translations(
                args.mv_dir,
                args.translation_model,
                args.translations,
            )
        else:
            result = build_official_candidate(args.mv_dir)
    except Exception as error:
        print(f"error={error}")
        return 2
    print(f"result={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .evaluation import SubtitleEvent, align_events, compute_metrics
from .text_match import normalize_text


_SRT_TIME = re.compile(
    r"(?P<start>\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})"
)


def parse_timestamp(value: str) -> float:
    normalized = value.strip().replace(",", ".")
    hours, minutes, seconds = normalized.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def select_subtitle_line(text: str, selector: str) -> str:
    lines = [
        line.strip()
        for line in text.replace("\\N", "\n").replace("\\n", "\n").splitlines()
        if line.strip()
    ]
    if not lines:
        return ""
    if selector == "first":
        return lines[0]
    if selector == "last":
        return lines[-1]
    return "".join(lines)


def _json_events(path: Path) -> list[SubtitleEvent]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    source = data if isinstance(data, list) else data.get("events", [])
    if not isinstance(source, list):
        raise ValueError(f"JSON字幕缺少events数组：{path}")
    return [
        SubtitleEvent(
            index=index,
            start=float(event["start"]),
            end=float(event["end"]),
            text=str(event.get("source_text", event.get("text", "")) or ""),
        )
        for index, event in enumerate(source)
    ]


def _srt_events(path: Path, selector: str) -> list[SubtitleEvent]:
    events: list[SubtitleEvent] = []
    content = path.read_text(encoding="utf-8-sig").strip()
    for block in re.split(r"\r?\n\r?\n", content):
        lines = block.splitlines()
        time_index = next(
            (index for index, line in enumerate(lines) if _SRT_TIME.search(line)),
            None,
        )
        if time_index is None:
            continue
        match = _SRT_TIME.search(lines[time_index])
        if match is None:
            continue
        text = select_subtitle_line("\n".join(lines[time_index + 1 :]), selector)
        if text:
            events.append(
                SubtitleEvent(
                    index=len(events),
                    start=parse_timestamp(match.group("start")),
                    end=parse_timestamp(match.group("end")),
                    text=text,
                )
            )
    return events


def _ass_events(path: Path, selector: str) -> list[SubtitleEvent]:
    events: list[SubtitleEvent] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        if not raw_line.startswith("Dialogue:"):
            continue
        fields = raw_line.split(",", 9)
        if len(fields) != 10:
            continue
        text = select_subtitle_line(fields[9], selector)
        if text:
            events.append(
                SubtitleEvent(
                    index=len(events),
                    start=parse_timestamp(fields[1]),
                    end=parse_timestamp(fields[2]),
                    text=text,
                )
            )
    return events


def load_subtitle_events(
    path: Path,
    selector: str = "last",
) -> list[SubtitleEvent]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"字幕不存在：{resolved}")
    suffix = resolved.suffix.casefold()
    if suffix == ".json":
        return _json_events(resolved)
    if suffix == ".srt":
        return _srt_events(resolved, selector)
    if suffix in {".ass", ".ssa"}:
        return _ass_events(resolved, selector)
    raise ValueError(f"不支持的字幕格式：{resolved.suffix}")


def match_timing_only_reference(
    expected_events: list[dict[str, Any]],
    reference_events: list[SubtitleEvent],
    *,
    full_concert: bool,
    expected_absolute_start: float | None = None,
) -> dict[str, Any]:
    expected_text = [
        normalize_text(
            str(event.get("source_text", event.get("text", "")) or "")
        )
        for event in expected_events
    ]
    reference_text = [normalize_text(event.text) for event in reference_events]
    outliers: list[dict[str, Any]] = []
    if not expected_text:
        return {
            "accepted": False,
            "matched_events": [],
            "outliers": [{"code": "empty_representative_sample"}],
        }

    if not full_concert:
        if len(reference_events) != len(expected_events):
            outliers.append(
                {
                    "code": "event_count_changed",
                    "expected": len(expected_events),
                    "actual": len(reference_events),
                }
            )
            return {"accepted": False, "matched_events": [], "outliers": outliers}
        starts = [0]
    else:
        starts = [
            start
            for start in range(0, len(reference_events) - len(expected_events) + 1)
            if reference_text[start : start + len(expected_events)] == expected_text
        ]
        if not starts:
            outliers.append(
                {
                    "code": "text_order_or_event_count_changed",
                    "expected_event_count": len(expected_events),
                }
            )
            return {"accepted": False, "matched_events": [], "outliers": outliers}
        if expected_absolute_start is not None:
            starts.sort(
                key=lambda start: abs(
                    reference_events[start].start - expected_absolute_start
                )
            )
    start = starts[0]
    matched = reference_events[start : start + len(expected_events)]
    for index, (expected, actual) in enumerate(zip(expected_text, matched)):
        if expected != normalize_text(actual.text):
            outliers.append(
                {
                    "code": "subtitle_text_changed",
                    "event_index": index,
                    "expected": expected,
                    "actual": normalize_text(actual.text),
                }
            )
    for index, event in enumerate(matched):
        if event.start < 0 or event.end <= event.start:
            outliers.append(
                {
                    "code": "invalid_reference_duration",
                    "event_index": index,
                }
            )
    return {
        "accepted": not outliers,
        "matched_events": [asdict(event) for event in matched],
        "match_start_index": start,
        "candidate_match_count": len(starts),
        "outliers": outliers,
    }


def timing_metrics(
    hypothesis: list[SubtitleEvent],
    reference: list[SubtitleEvent],
) -> dict[str, Any]:
    alignment = align_events(hypothesis, reference)
    metrics = compute_metrics(
        alignment,
        hypothesis_events=len(hypothesis),
        reference_events=len(reference),
    )
    starts = [abs(pair.start_error) for pair in alignment.pairs]
    ends = [abs(pair.end_error) for pair in alignment.pairs]
    thresholds = (0.25, 0.5, 0.75)
    return {
        "metrics": asdict(metrics),
        "coverage_by_threshold": {
            f"{threshold:.2f}": {
                "start": round(
                    sum(value <= threshold for value in starts) / len(starts),
                    6,
                ) if starts else 0.0,
                "end": round(
                    sum(value <= threshold for value in ends) / len(ends),
                    6,
                ) if ends else 0.0,
            }
            for threshold in thresholds
        },
        "alignment": {
            "pairs": [asdict(pair) for pair in alignment.pairs],
            "unmatched_hypothesis": list(alignment.unmatched_hypothesis),
            "unmatched_reference": list(alignment.unmatched_reference),
        },
    }

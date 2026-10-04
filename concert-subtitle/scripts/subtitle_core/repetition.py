from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Any

from .text_match import normalize_text


@dataclass(frozen=True)
class RepetitionPolicy:
    min_block_events: int = 2
    max_group_duration_drift: float = 0.12
    structure_gap_seconds: float = 3.0
    padding_seconds: float = 3.0
    max_clip_seconds: float = 180.0


def canonical_event_text(event: dict[str, Any]) -> str:
    return str(
        event.get("canonical_source_text")
        or event.get("source_text")
        or event.get("text")
        or ""
    )


def event_is_lyric(event: dict[str, Any]) -> bool:
    return str(event.get("role", "lyric")) == "lyric"


def event_has_live_variant(event: dict[str, Any]) -> bool:
    canonical = event.get("canonical_source_text")
    if not canonical:
        return False
    source = str(event.get("source_text", event.get("text", "")) or "")
    return normalize_text(str(canonical)) != normalize_text(source)


def event_risk_codes(event: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    role = str(event.get("role", "lyric"))
    if role == "speech":
        codes.append("speech")
    elif role == "lyric_adlib":
        codes.append("lyric_adlib")
    elif role != "lyric":
        codes.append("non_lyric_role")
    if event_has_live_variant(event):
        codes.append("live_lyric_variant")
    evidence = {
        normalize_text(str(value))
        for value in event.get("evidence", [])
    }
    for needle, code in (
        ("audience", "audience_singing"),
        ("crowd", "audience_singing"),
        ("omitted", "omitted_lyric"),
        ("missing", "omitted_lyric"),
    ):
        if any(needle in value for value in evidence):
            codes.append(code)
    return sorted(set(codes))


def _event_token(event: dict[str, Any]) -> str | None:
    if not event_is_lyric(event):
        return None
    token = normalize_text(canonical_event_text(event))
    return token or None


def _non_overlapping_starts(starts: list[int], length: int) -> list[int]:
    selected: list[int] = []
    for start in starts:
        if not selected or start >= selected[-1] + length:
            selected.append(start)
    return selected


def _occurrence(
    events: list[dict[str, Any]],
    start_index: int,
    length: int,
) -> dict[str, Any]:
    selected = events[start_index : start_index + length]
    start = float(selected[0]["start"])
    end = float(selected[-1]["end"])
    risks = sorted(
        {
            code
            for event in selected
            for code in event_risk_codes(event)
        }
    )
    return {
        "start_event_index": start_index,
        "end_event_index": start_index + length,
        "event_ids": [str(event.get("id", "")) for event in selected],
        "start": start,
        "end": end,
        "duration": round(end - start, 6),
        "risk_codes": risks,
    }


def detect_repetition_group(
    events: list[dict[str, Any]],
    policy: RepetitionPolicy | None = None,
    known_lyrics: list[str] | None = None,
) -> dict[str, Any] | None:
    policy = policy or RepetitionPolicy()
    tokens = [_event_token(event) for event in events]
    if known_lyrics is not None:
        normalized_lyrics = [
            normalize_text(line) for line in known_lyrics if normalize_text(line)
        ]
        known_tokens = {
            "".join(normalized_lyrics[start:end])
            for start in range(len(normalized_lyrics))
            for end in range(start + 1, len(normalized_lyrics) + 1)
        }
        tokens = [token if token in known_tokens else None for token in tokens]
    best: tuple[tuple[int, int, int], tuple[str, ...], list[int]] | None = None
    maximum = len(events) // 2
    for length in range(maximum, policy.min_block_events - 1, -1):
        sequences: dict[tuple[str, ...], list[int]] = {}
        for start in range(0, len(events) - length + 1):
            sequence = tokens[start : start + length]
            if any(token is None for token in sequence):
                continue
            key = tuple(str(token) for token in sequence)
            sequences.setdefault(key, []).append(start)
        for sequence, starts in sequences.items():
            selected = _non_overlapping_starts(starts, length)
            if len(selected) < 2:
                continue
            score = (length, len(selected), sum(len(token) for token in sequence))
            if best is None or score > best[0]:
                best = (score, sequence, selected)
        if best is not None and best[0][0] == length:
            break
    if best is None:
        return None

    length = best[0][0]
    occurrences = [_occurrence(events, start, length) for start in best[2]]
    durations = [float(item["duration"]) for item in occurrences]
    typical_duration = median(durations)
    duration_drift = max(
        (
            abs(duration / typical_duration - 1.0)
            for duration in durations
        ),
        default=math.inf,
    ) if typical_duration > 0 else math.inf
    return {
        "anchor_event_count": length,
        "anchor_text": list(best[1]),
        "occurrences": occurrences,
        "duration_median": round(typical_duration, 6),
        "max_duration_drift_ratio": round(duration_drift, 6),
        "stable": duration_drift <= policy.max_group_duration_drift,
    }


def _structure_boundary(
    events: list[dict[str, Any]],
    anchor_start: int,
    occurrences: list[dict[str, Any]],
    policy: RepetitionPolicy,
) -> int:
    candidates = [0]
    for index in range(1, anchor_start + 1):
        previous = events[index - 1]
        current = events[index]
        gap = float(current["start"]) - float(previous["end"])
        if not event_is_lyric(previous) or gap >= policy.structure_gap_seconds:
            candidates.append(index)
    for occurrence in occurrences:
        occurrence_end = int(occurrence["end_event_index"])
        if occurrence_end <= anchor_start:
            candidates.append(occurrence_end)
    return max(candidates)


def representative_cycle(
    events: list[dict[str, Any]],
    group: dict[str, Any],
    occurrence_index: int,
    section_duration: float,
    policy: RepetitionPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or RepetitionPolicy()
    occurrence = group["occurrences"][occurrence_index]
    anchor_start = int(occurrence["start_event_index"])
    anchor_end = int(occurrence["end_event_index"])
    sample_start = _structure_boundary(
        events,
        anchor_start,
        group["occurrences"],
        policy,
    )
    maximum_core = policy.max_clip_seconds - 2 * policy.padding_seconds
    while sample_start < anchor_start:
        core_duration = (
            float(events[anchor_end - 1]["end"])
            - float(events[sample_start]["start"])
        )
        if core_duration <= maximum_core:
            break
        sample_start += 1
    core_start = float(events[sample_start]["start"])
    core_end = float(events[anchor_end - 1]["end"])
    clip_start = max(0.0, core_start - policy.padding_seconds)
    clip_end = min(section_duration, core_end + policy.padding_seconds)
    if clip_end - clip_start > policy.max_clip_seconds:
        clip_start = max(0.0, clip_end - policy.max_clip_seconds)
    complete_anchor = (
        clip_start <= float(events[anchor_start]["start"])
        and clip_end >= float(events[anchor_end - 1]["end"])
    )
    return {
        "sample_start_event_index": sample_start,
        "sample_end_event_index": anchor_end,
        "sample_event_ids": [
            str(event.get("id", ""))
            for event in events[sample_start:anchor_end]
        ],
        "anchor_start_event_index": anchor_start,
        "anchor_end_event_index": anchor_end,
        "clip_start": round(clip_start, 6),
        "clip_end": round(clip_end, 6),
        "clip_duration": round(clip_end - clip_start, 6),
        "padding_seconds": policy.padding_seconds,
        "complete_anchor": complete_anchor,
    }


def fallback_sample(
    events: list[dict[str, Any]],
    section_duration: float,
    policy: RepetitionPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or RepetitionPolicy()
    eligible = [index for index, event in enumerate(events) if event_is_lyric(event)]
    if not eligible:
        raise ValueError("歌曲没有可用于验证样片的歌词事件")
    best_start = eligible[0]
    best_end = best_start + 1
    for start in eligible:
        if start > 0 and start - 1 not in eligible:
            pass
        end = start
        while end < len(events) and event_is_lyric(events[end]):
            duration = float(events[end]["end"]) - float(events[start]["start"])
            if duration > policy.max_clip_seconds - 2 * policy.padding_seconds:
                break
            end += 1
        if end - start > best_end - best_start:
            best_start, best_end = start, end
    core_start = float(events[best_start]["start"])
    core_end = float(events[best_end - 1]["end"])
    clip_start = max(0.0, core_start - policy.padding_seconds)
    clip_end = min(section_duration, core_end + policy.padding_seconds)
    return {
        "sample_start_event_index": best_start,
        "sample_end_event_index": best_end,
        "sample_event_ids": [
            str(event.get("id", "")) for event in events[best_start:best_end]
        ],
        "anchor_start_event_index": None,
        "anchor_end_event_index": None,
        "clip_start": round(clip_start, 6),
        "clip_end": round(clip_end, 6),
        "clip_duration": round(clip_end - clip_start, 6),
        "padding_seconds": policy.padding_seconds,
        "complete_anchor": False,
    }

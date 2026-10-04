from __future__ import annotations

import math
from dataclasses import dataclass
from difflib import SequenceMatcher

from .text_match import normalize_text


@dataclass(frozen=True)
class SubtitleEvent:
    index: int
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class AlignedPair:
    hypothesis_indices: tuple[int, ...]
    reference_indices: tuple[int, ...]
    start_error: float
    end_error: float
    relation: str


@dataclass(frozen=True)
class AlignmentResult:
    pairs: tuple[AlignedPair, ...]
    unmatched_hypothesis: tuple[int, ...]
    unmatched_reference: tuple[int, ...]


@dataclass(frozen=True)
class ErrorStats:
    count: int
    median: float
    p90: float
    maximum: float
    signed_median: float


@dataclass(frozen=True)
class EvalMetrics:
    start: ErrorStats
    end: ErrorStats
    coverage: float
    hypothesis_events: int
    reference_events: int
    matched_pairs: int
    split_pairs: int
    merge_pairs: int
    buckets: dict[str, int]


def _char_stream(events: list[SubtitleEvent]) -> tuple[str, list[int]]:
    chars: list[str] = []
    owners: list[int] = []
    for item in events:
        for char in normalize_text(item.text):
            chars.append(char)
            owners.append(item.index)
    return "".join(chars), owners


def align_events(
    hypothesis: list[SubtitleEvent],
    reference: list[SubtitleEvent],
    *,
    min_link_ratio: float = 0.30,
) -> AlignmentResult:
    hyp_chars, hyp_owner = _char_stream(hypothesis)
    ref_chars, ref_owner = _char_stream(reference)
    if not hyp_chars or not ref_chars:
        return AlignmentResult(
            (),
            tuple(item.index for item in hypothesis),
            tuple(item.index for item in reference),
        )
    shared: dict[tuple[int, int], int] = {}
    matcher = SequenceMatcher(None, hyp_chars, ref_chars, autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            key = (hyp_owner[block.a + offset], ref_owner[block.b + offset])
            shared[key] = shared.get(key, 0) + 1
    hyp_length = {item.index: len(normalize_text(item.text)) for item in hypothesis}
    ref_length = {item.index: len(normalize_text(item.text)) for item in reference}
    links = [
        key
        for key, count in shared.items()
        if count >= min_link_ratio * min(hyp_length[key[0]], ref_length[key[1]])
    ]
    parent: dict[tuple[str, int], tuple[str, int]] = {}

    def find(node: tuple[str, int]) -> tuple[str, int]:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for hyp_index, ref_index in links:
        parent[find(("h", hyp_index))] = find(("r", ref_index))
    groups: dict[tuple[str, int], list[tuple[str, int]]] = {}
    for node in list(parent):
        groups.setdefault(find(node), []).append(node)
    hyp_by_index = {item.index: item for item in hypothesis}
    ref_by_index = {item.index: item for item in reference}
    pairs: list[AlignedPair] = []
    matched_hyp: set[int] = set()
    matched_ref: set[int] = set()
    for members in groups.values():
        hyp_indices = sorted(index for kind, index in members if kind == "h")
        ref_indices = sorted(index for kind, index in members if kind == "r")
        if not hyp_indices or not ref_indices:
            continue
        matched_hyp.update(hyp_indices)
        matched_ref.update(ref_indices)
        pairs.append(
            AlignedPair(
                hypothesis_indices=tuple(hyp_indices),
                reference_indices=tuple(ref_indices),
                start_error=round(
                    hyp_by_index[hyp_indices[0]].start
                    - ref_by_index[ref_indices[0]].start,
                    3,
                ),
                end_error=round(
                    hyp_by_index[hyp_indices[-1]].end
                    - ref_by_index[ref_indices[-1]].end,
                    3,
                ),
                relation=f"{'1' if len(hyp_indices) == 1 else 'N'}:"
                f"{'1' if len(ref_indices) == 1 else 'N'}",
            )
        )
    pairs.sort(key=lambda item: item.hypothesis_indices[0])
    return AlignmentResult(
        pairs=tuple(pairs),
        unmatched_hypothesis=tuple(
            item.index for item in hypothesis if item.index not in matched_hyp
        ),
        unmatched_reference=tuple(
            item.index for item in reference if item.index not in matched_ref
        ),
    )


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(ratio * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _error_stats(signed: list[float]) -> ErrorStats:
    absolute = [abs(value) for value in signed]
    return ErrorStats(
        count=len(signed),
        median=round(percentile(absolute, 0.5), 3),
        p90=round(percentile(absolute, 0.9), 3),
        maximum=round(max(absolute), 3) if absolute else 0.0,
        signed_median=round(percentile(signed, 0.5), 3),
    )


def is_sub_frame(error: float, *, frame_rate: float = 30.0) -> bool:
    return abs(error) < 1.0 / frame_rate


def compute_metrics(
    result: AlignmentResult,
    *,
    hypothesis_events: int,
    reference_events: int,
    sub_frame_rate: float | None = None,
) -> EvalMetrics:
    matched_hypothesis = sum(
        len(pair.hypothesis_indices) for pair in result.pairs
    )
    working_pairs = result.pairs
    if sub_frame_rate is not None:
        working_pairs = tuple(
            pair
            for pair in result.pairs
            if not is_sub_frame(pair.start_error, frame_rate=sub_frame_rate)
        )
    starts = [pair.start_error for pair in working_pairs]
    ends = [pair.end_error for pair in working_pairs]
    absolute_starts = [abs(value) for value in starts]
    return EvalMetrics(
        start=_error_stats(starts),
        end=_error_stats(ends),
        coverage=(
            round(matched_hypothesis / hypothesis_events, 4)
            if hypothesis_events
            else 0.0
        ),
        hypothesis_events=hypothesis_events,
        reference_events=reference_events,
        matched_pairs=len(working_pairs),
        split_pairs=sum(
            1
            for pair in working_pairs
            if len(pair.hypothesis_indices) == 1
            and len(pair.reference_indices) > 1
        ),
        merge_pairs=sum(
            1
            for pair in working_pairs
            if len(pair.hypothesis_indices) > 1
            and len(pair.reference_indices) == 1
        ),
        buckets={
            "<=0.20": sum(value <= 0.20 for value in absolute_starts),
            "0.20-0.75": sum(0.20 < value <= 0.75 for value in absolute_starts),
            ">0.75": sum(value > 0.75 for value in absolute_starts),
        },
    )

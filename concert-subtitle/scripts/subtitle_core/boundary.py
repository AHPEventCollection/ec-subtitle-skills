from __future__ import annotations

from dataclasses import dataclass, replace
from difflib import SequenceMatcher

from .text_match import normalize_text, phonetic_key, similarity
from .transcript import TranscriptSegment


@dataclass(frozen=True)
class TimedWord:
    index: int
    start: float
    end: float
    text: str
    probability: float | None = None


@dataclass(frozen=True)
class TimedEvent:
    event_id: str
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class BoundaryPolicy:
    search_margin: float = 2.0
    min_score: float = 0.55
    rank_distance_weight: float = 0.05
    rank_length_weight: float = 0.01
    start_offset: float = 0.0
    end_offset: float = 0.0
    min_adjustment: float = 0.05
    max_boundary_adjustment: float = 0.75
    minimum_edge_chars: int = 2
    strong_edge_chars: int = 4
    strong_edge_probability: float = 0.75
    handoff_gap: float = 0.02


@dataclass(frozen=True)
class WordMatch:
    start_index: int
    end_index: int
    start: float
    end: float
    score: float
    text: str
    rank_value: float


@dataclass(frozen=True)
class BoundaryProposal:
    event_id: str
    original_start: float
    original_end: float
    proposed_start: float
    proposed_end: float
    start_delta: float
    end_delta: float
    score: float | None
    matched_text: str | None
    status: str
    candidate_start: float | None = None
    candidate_end: float | None = None
    start_anchored: bool = False
    end_anchored: bool = False
    phonetic_coverage: float | None = None
    start_edge_chars: int = 0
    end_edge_chars: int = 0
    start_probability: float | None = None
    end_probability: float | None = None
    rejected_reason: str | None = None
    boundary_resolution: str | None = None
    would_overlap: bool = False


@dataclass(frozen=True)
class EdgeRefinement:
    start: float
    end: float
    start_anchored: bool
    end_anchored: bool
    phonetic_coverage: float
    start_edge_chars: int
    end_edge_chars: int
    start_probability: float | None
    end_probability: float | None
    consumed_word_end: int


def flatten_words(segments: list[TranscriptSegment]) -> list[TimedWord]:
    raw = [
        (word.start, word.end, word.text, word.probability)
        for segment in segments
        for word in segment.words
    ]
    raw.sort(key=lambda item: (item[0], item[1], item[2]))
    return [
        TimedWord(index, start, end, text, probability)
        for index, (start, end, text, probability) in enumerate(raw)
    ]


def find_best_word_match(
    event: TimedEvent,
    *,
    words: list[TimedWord],
    minimum_word_index: int,
    policy: BoundaryPolicy,
) -> WordMatch | None:
    target = normalize_text(event.text)
    if not target:
        return None
    candidates = [
        word
        for word in words
        if word.index >= minimum_word_index
        and word.end >= event.start - policy.search_margin
        and word.start <= event.end + policy.search_margin
    ]
    if not candidates:
        return None
    min_chars = max(1, int(len(target) * 0.45))
    max_chars = max(len(target) + 4, int(len(target) * 1.75))
    max_words = max(8, len(target) * 2 + 4)
    event_center = (event.start + event.end) / 2
    best: WordMatch | None = None
    best_rank: tuple[float, float] | None = None
    for start_offset, first in enumerate(candidates):
        parts: list[str] = []
        stop = min(len(candidates), start_offset + max_words)
        for end_offset in range(start_offset, stop):
            current = candidates[end_offset]
            if (
                end_offset > start_offset
                and current.index != candidates[end_offset - 1].index + 1
            ):
                break
            parts.append(current.text)
            candidate_text = "".join(parts)
            candidate_chars = len(normalize_text(candidate_text))
            if candidate_chars < min_chars:
                continue
            if candidate_chars > max_chars:
                break
            score = similarity(event.text, candidate_text)
            center = (first.start + current.end) / 2
            rank_value = (
                score
                - policy.rank_distance_weight * abs(center - event_center)
                - policy.rank_length_weight * abs(candidate_chars - len(target))
            )
            rank = (rank_value, -first.start)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best = WordMatch(
                    start_index=first.index,
                    end_index=current.index + 1,
                    start=first.start,
                    end=current.end,
                    score=score,
                    text=candidate_text,
                    rank_value=rank_value,
                )
    if best is None or best.score < policy.min_score:
        return None
    return best


def _phonetic_timeline(
    words: list[TimedWord],
) -> tuple[str, list[tuple[float, float]], list[TimedWord]]:
    characters: list[str] = []
    timings: list[tuple[float, float]] = []
    owners: list[TimedWord] = []
    for word in words:
        key = phonetic_key(word.text)
        if not key:
            continue
        duration = word.end - word.start
        for offset, character in enumerate(key):
            characters.append(character)
            timings.append(
                (
                    word.start + duration * offset / len(key),
                    word.start + duration * (offset + 1) / len(key),
                )
            )
            owners.append(word)
    return "".join(characters), timings, owners


def _block_probability(
    block: object | None,
    owners: list[TimedWord],
) -> float | None:
    if block is None:
        return None
    start = int(getattr(block, "b"))
    size = int(getattr(block, "size"))
    word_probabilities = {
        owner.index: owner.probability
        for owner in owners[start : start + size]
        if owner.probability is not None
    }
    if not word_probabilities:
        return None
    return sum(word_probabilities.values()) / len(word_probabilities)


def refine_match_edges(
    event: TimedEvent,
    match: WordMatch,
    *,
    words: list[TimedWord],
    policy: BoundaryPolicy,
) -> EdgeRefinement:
    target = phonetic_key(event.text)
    candidate, timings, owners = _phonetic_timeline(
        words[match.start_index : match.end_index]
    )
    if not target or not candidate or not timings:
        return EdgeRefinement(
            event.start,
            event.end,
            False,
            False,
            0.0,
            0,
            0,
            None,
            None,
            match.end_index,
        )
    blocks = [
        block
        for block in SequenceMatcher(None, target, candidate, autojunk=False).get_matching_blocks()
        if block.size
    ]
    coverage = sum(block.size for block in blocks) / len(target)
    required_edge_chars = min(policy.minimum_edge_chars, len(target))
    start_block = next(
        (
            block
            for block in blocks
            if block.a == 0 and block.size >= required_edge_chars
        ),
        None,
    )
    end_block = next(
        (
            block
            for block in reversed(blocks)
            if block.a + block.size == len(target)
            and block.size >= required_edge_chars
        ),
        None,
    )
    consumed_word_end = max(
        (
            owners[position].index + 1
            for block in blocks
            for position in range(block.b, block.b + block.size)
        ),
        default=match.end_index,
    )
    return EdgeRefinement(
        start=timings[start_block.b][0] if start_block else event.start,
        end=(
            timings[end_block.b + end_block.size - 1][1]
            if end_block
            else event.end
        ),
        start_anchored=start_block is not None,
        end_anchored=end_block is not None,
        phonetic_coverage=coverage,
        start_edge_chars=start_block.size if start_block else 0,
        end_edge_chars=end_block.size if end_block else 0,
        start_probability=_block_probability(start_block, owners),
        end_probability=_block_probability(end_block, owners),
        consumed_word_end=consumed_word_end,
    )


def _strong_edge(
    edge_chars: int,
    probability: float | None,
    policy: BoundaryPolicy,
) -> bool:
    return (
        edge_chars >= policy.strong_edge_chars
        and probability is not None
        and probability >= policy.strong_edge_probability
    )


def _merge_reason(existing: str | None, addition: str) -> str:
    reasons = {
        reason
        for value in (existing, addition)
        for reason in (value or "").split(",")
        if reason
    }
    return ",".join(sorted(reasons))


def _resolve_neighbor_conflicts(
    proposals: list[BoundaryProposal],
    policy: BoundaryPolicy,
) -> list[BoundaryProposal]:
    output = list(proposals)
    conflict_reasons: dict[int, set[str]] = {}
    resolutions: dict[int, set[str]] = {}
    accepted_statuses = {"suggested", "suggested_partial"}
    for index in range(len(output) - 1):
        left = output[index]
        right = output[index + 1]
        if left.proposed_end <= right.proposed_start:
            continue
        if (
            left.status in accepted_statuses
            and right.status in accepted_statuses
            and not left.end_anchored
            and right.start_anchored
        ):
            handoff_end = round(right.proposed_start - policy.handoff_gap, 3)
            if handoff_end - left.proposed_start >= 0.2:
                output[index] = replace(left, proposed_end=handoff_end)
                resolutions.setdefault(index, set()).add(
                    "end_from_next_sentence_start"
                )
                continue
        if (
            left.status in accepted_statuses
            and left.proposed_end != left.original_end
        ):
            output[index] = replace(left, proposed_end=left.original_end)
            conflict_reasons.setdefault(index, set()).add(
                "end_candidate_overlaps_next"
            )
        if (
            right.status in accepted_statuses
            and right.proposed_start != right.original_start
        ):
            output[index + 1] = replace(
                right,
                proposed_start=right.original_start,
            )
            conflict_reasons.setdefault(index + 1, set()).add(
                "start_candidate_overlaps_previous"
            )
    for index, notes in resolutions.items():
        proposal = output[index]
        output[index] = replace(
            proposal,
            start_delta=round(
                proposal.proposed_start - proposal.original_start,
                3,
            ),
            end_delta=round(
                proposal.proposed_end - proposal.original_end,
                3,
            ),
            status="suggested_partial",
            boundary_resolution=",".join(sorted(notes)),
            would_overlap=True,
        )
    for index, reasons in conflict_reasons.items():
        proposal = output[index]
        start_delta = round(proposal.proposed_start - proposal.original_start, 3)
        end_delta = round(proposal.proposed_end - proposal.original_end, 3)
        status = (
            "neighbor_conflict_keep_original"
            if start_delta == 0.0 and end_delta == 0.0
            else "suggested_partial"
        )
        output[index] = replace(
            proposal,
            start_delta=start_delta,
            end_delta=end_delta,
            status=status,
            rejected_reason=_merge_reason(
                proposal.rejected_reason,
                ",".join(sorted(reasons)),
            ),
            would_overlap=True,
        )
    return output


def propose_boundaries(
    events: list[TimedEvent],
    segments: list[TranscriptSegment],
    *,
    policy: BoundaryPolicy | None = None,
) -> list[BoundaryProposal]:
    policy = policy or BoundaryPolicy()
    words = flatten_words(segments)
    minimum_word_index = 0
    proposals: list[BoundaryProposal] = []
    for event in events:
        match = find_best_word_match(
            event,
            words=words,
            minimum_word_index=minimum_word_index,
            policy=policy,
        )
        if match is None:
            proposals.append(
                BoundaryProposal(
                    event_id=event.event_id,
                    original_start=event.start,
                    original_end=event.end,
                    proposed_start=event.start,
                    proposed_end=event.end,
                    start_delta=0.0,
                    end_delta=0.0,
                    score=None,
                    matched_text=None,
                    status="unmatched_keep_original",
                )
            )
            continue
        refinement = refine_match_edges(
            event,
            match,
            words=words,
            policy=policy,
        )
        minimum_word_index = max(
            minimum_word_index,
            refinement.consumed_word_end,
        )
        candidate_start = (
            max(0.0, refinement.start + policy.start_offset)
            if refinement.start_anchored
            else event.start
        )
        candidate_end = (
            refinement.end + policy.end_offset
            if refinement.end_anchored
            else event.end
        )
        raw_start_delta = candidate_start - event.start
        raw_end_delta = candidate_end - event.end
        target_start = candidate_start
        target_end = candidate_end
        rejected_reasons: list[str] = []
        if not refinement.start_anchored and not refinement.end_anchored:
            status = "edge_unanchored_keep_original"
            rejected_reasons.append("missing_sentence_edge_phonetic_anchor")
            target_start = event.start
            target_end = event.end
        else:
            if (
                abs(raw_start_delta) > policy.max_boundary_adjustment
                and not _strong_edge(
                    refinement.start_edge_chars,
                    refinement.start_probability,
                    policy,
                )
            ):
                target_start = event.start
                rejected_reasons.append(
                    "start_adjustment_exceeds_policy_without_strong_edge"
                )
            if (
                abs(raw_end_delta) > policy.max_boundary_adjustment
                and not _strong_edge(
                    refinement.end_edge_chars,
                    refinement.end_probability,
                    policy,
                )
            ):
                target_end = event.end
                rejected_reasons.append(
                    "end_adjustment_exceeds_policy_without_strong_edge"
                )
            start_delta = target_start - event.start
            end_delta = target_end - event.end
            if (
                abs(start_delta) < policy.min_adjustment
                and abs(end_delta) < policy.min_adjustment
            ):
                status = (
                    "large_adjustment_keep_original"
                    if rejected_reasons
                    else "matched_keep_original"
                )
                target_start = event.start
                target_end = event.end
            elif target_end - target_start < 0.2:
                status = "unsafe_duration_keep_original"
                rejected_reasons.append("candidate_duration_below_minimum")
                target_start = event.start
                target_end = event.end
            elif (
                rejected_reasons
                or not refinement.start_anchored
                or not refinement.end_anchored
            ):
                status = "suggested_partial"
            else:
                status = "suggested"
        start_delta = target_start - event.start
        end_delta = target_end - event.end
        proposals.append(
            BoundaryProposal(
                event_id=event.event_id,
                original_start=event.start,
                original_end=event.end,
                proposed_start=round(target_start, 3),
                proposed_end=round(target_end, 3),
                start_delta=round(start_delta, 3),
                end_delta=round(end_delta, 3),
                score=round(match.score, 4),
                matched_text=match.text,
                status=status,
                candidate_start=round(candidate_start, 3),
                candidate_end=round(candidate_end, 3),
                start_anchored=refinement.start_anchored,
                end_anchored=refinement.end_anchored,
                phonetic_coverage=round(refinement.phonetic_coverage, 4),
                start_edge_chars=refinement.start_edge_chars,
                end_edge_chars=refinement.end_edge_chars,
                start_probability=(
                    round(refinement.start_probability, 4)
                    if refinement.start_probability is not None
                    else None
                ),
                end_probability=(
                    round(refinement.end_probability, 4)
                    if refinement.end_probability is not None
                    else None
                ),
                rejected_reason=(
                    ",".join(sorted(rejected_reasons))
                    if rejected_reasons
                    else None
                ),
            )
        )
    return _resolve_neighbor_conflicts(proposals, policy)

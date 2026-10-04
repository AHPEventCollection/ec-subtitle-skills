from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Any

from assess_mc_complexity import load_assessment, validate_assessment
from common import (
    SOURCE_MAX_DISPLAY_UNITS,
    TRANSLATION_MAX_DISPLAY_UNITS,
    VERSION,
    console_version,
    display_units,
    event_source_text,
    load_manifest,
    read_json,
    section_directory,
    utc_now,
    write_json,
)
from subtitle_core import load_transcript


STRONG_BREAKS = "。！？?!；;"
SOFT_BREAKS = "，、,:："
TRAILING_CLOSERS = "”’」』）》】\"'"
UNFINISHED_JAPANESE_ENDINGS = (
    "けれども",
    "けれど",
    "ところで",
    "という",
    "ことを",
    "ために",
    "ように",
    "ながら",
    "なので",
    "だから",
    "わけで",
    "けど",
    "から",
    "ので",
    "のに",
    "なら",
    "たら",
    "れば",
    "つつ",
    "のを",
    "って",
    "とか",
    "し",
    "て",
    "で",
    "が",
    "ば",
)
SHORT_REPLY_TEXTS = {
    "はい",
    "うん",
    "ええ",
    "いいえ",
    "いや",
    "そう",
    "そうです",
    "そうだね",
    "そうですね",
    "なるほど",
    "ありがとう",
    "ありがとうございます",
    "えっ",
    "あっ",
    "おお",
    "へえ",
}
SPEAKER_KEYS = ("speaker", "speaker_id", "speaker_label")


def _position_for_ratio(text: str, ratio: float) -> int:
    total = max(1, display_units(text))
    target = total * ratio
    used = 0
    for index, character in enumerate(text, start=1):
        used += display_units(character)
        if used >= target:
            return index
    return max(1, len(text) - 1)


def _split_text(
    text: str,
    part_count: int,
    max_units: int,
    target_ratios: list[float] | None = None,
    preferred_ratios: list[float] | None = None,
) -> tuple[list[str], list[float]]:
    text = text.strip()
    if part_count <= 1:
        return [text], []
    total_units = max(1, display_units(text))
    strong_positions = _internal_strong_positions(text)
    if len(strong_positions) == part_count - 1:
        pieces = []
        previous = 0
        for position in [*strong_positions, len(text)]:
            pieces.append(text[previous:position].strip())
            previous = position
        ratios = [
            display_units(text[:position]) / total_units
            for position in strong_positions
        ]
        return pieces, ratios
    preferred_positions = {
        _position_for_ratio(text, ratio)
        for ratio in (preferred_ratios or [])
        if 0.0 < ratio < 1.0
    }
    cuts = []
    previous = 0
    for cut_number in range(1, part_count):
        remaining_parts = part_count - cut_number
        target_ratio = (
            target_ratios[cut_number - 1]
            if target_ratios and cut_number - 1 < len(target_ratios)
            else cut_number / part_count
        )
        candidates = []
        for position in range(previous + 1, len(text)):
            left = text[previous:position].strip()
            right = text[position:].strip()
            if not left or not right:
                continue
            left_units = display_units(left)
            right_units = display_units(right)
            actual_ratio = display_units(text[:position]) / total_units
            previous_character = text[position - 1]
            next_character = text[position]
            if previous_character in STRONG_BREAKS:
                penalty = 0.0
            elif position in preferred_positions:
                penalty = 0.03
            elif previous_character.isspace() or next_character.isspace():
                penalty = 0.06
            elif previous_character in SOFT_BREAKS:
                penalty = 0.1
            else:
                penalty = 0.55
            penalty += max(0, left_units - max_units) / max_units * 0.1
            penalty += (
                max(0, right_units - max_units * remaining_parts)
                / max_units
                * 0.1
            )
            if (
                previous_character.isascii()
                and previous_character.isalnum()
                and next_character.isascii()
                and next_character.isalnum()
            ):
                penalty += 0.8
            candidates.append(
                (
                    abs(actual_ratio - target_ratio) + penalty,
                    position,
                )
            )
        if not candidates:
            position = _position_for_ratio(text, target_ratio)
            position = max(previous + 1, min(len(text) - 1, position))
        else:
            _, position = min(candidates)
        cuts.append(position)
        previous = position
    pieces = []
    previous = 0
    for position in [*cuts, len(text)]:
        piece = text[previous:position].strip()
        if piece:
            pieces.append(piece)
        previous = position
    if len(pieces) != part_count:
        return [text], []
    ratios = [
        display_units(text[:position]) / total_units
        for position in cuts
    ]
    return pieces, ratios


def _matching_segments(event: dict[str, Any], segments: list[dict]) -> list[dict]:
    start = float(event["start"])
    end = float(event["end"])
    return [
        segment
        for segment in segments
        if float(segment["start"]) >= start - 0.05
        and float(segment["end"]) <= end + 0.05
    ]


def _word_timed_segments(segments: list[dict]) -> list[dict]:
    return [
        segment
        for segment in segments
        if any(
            word.get("start") is not None and word.get("end") is not None
            for word in (segment.get("words") or [])
            if isinstance(word, dict)
        )
    ]


def _without_terminal_marks(text: str) -> str:
    return text.strip().rstrip(TRAILING_CLOSERS + STRONG_BREAKS + SOFT_BREAKS).strip()


def _has_strong_termination(text: str) -> bool:
    stripped = text.strip().rstrip(TRAILING_CLOSERS).rstrip()
    return bool(stripped) and stripped[-1] in STRONG_BREAKS


def _has_unfinished_japanese_tail(text: str) -> bool:
    stripped = _without_terminal_marks(text)
    return bool(stripped) and stripped.endswith(UNFINISHED_JAPANESE_ENDINGS)


def _has_continuation_evidence(event: dict[str, Any]) -> bool:
    if any(
        event.get(key) is True
        for key in ("continuation", "continues_next", "is_continuation")
    ):
        return True
    for item in event.get("evidence") or []:
        token = str(item).casefold()
        if "continuation" in token or "continues_next" in token or "unfinished" in token:
            return True
    return False


def _is_short_reply(text: str) -> bool:
    normalized = _without_terminal_marks(text).replace(" ", "")
    return normalized in SHORT_REPLY_TEXTS


def _speaker_values(event: dict[str, Any], segments: list[dict]) -> set[str]:
    values = {
        str(item.get(key)).strip()
        for item in [event, *segments]
        for key in SPEAKER_KEYS
        if item.get(key) not in (None, "")
    }
    return values


def _join_continuation_text(first: str, second: str) -> str:
    left = first.rstrip()
    right = second.lstrip()
    if (
        left
        and right
        and left[-1].isascii()
        and left[-1].isalnum()
        and right[0].isascii()
        and right[0].isalnum()
    ):
        return f"{left} {right}"
    return left + right


def _can_merge_short_prefix(
    first: dict[str, Any],
    second: dict[str, Any],
    transcript_segments: list[dict],
) -> bool:
    source = event_source_text(first).strip()
    translation = str(first.get("translation", "") or "").strip()
    duration = float(first["end"]) - float(first["start"])
    gap = float(second["start"]) - float(first["end"])
    if (
        not source
        or not translation
        or display_units(source) > 20
        or display_units(translation) > 16
        or duration <= 0.0
        or duration > 1.8
        or gap < -0.001
        or gap > 0.8
        or _has_strong_termination(source)
        or _has_strong_termination(translation)
        or _is_short_reply(source)
    ):
        return False
    if not (
        _has_unfinished_japanese_tail(source)
        or _has_continuation_evidence(first)
    ):
        return False

    first_segments = _matching_segments(first, transcript_segments)
    second_segments = _matching_segments(second, transcript_segments)
    first_speakers = _speaker_values(first, first_segments)
    second_speakers = _speaker_values(second, second_segments)
    if (
        len(first_speakers) > 1
        or len(second_speakers) > 1
        or bool(first_speakers) != bool(second_speakers)
        or (
            first_speakers
            and second_speakers
            and first_speakers != second_speakers
        )
    ):
        return False
    return True


def _merge_short_prefixes(
    events: list[dict[str, Any]],
    transcript_segments: list[dict],
) -> tuple[list[dict[str, Any]], int]:
    merged: list[dict[str, Any]] = []
    merged_event_count = 0
    index = 0
    while index < len(events):
        current = {**events[index]}
        if (
            index + 1 < len(events)
            and _can_merge_short_prefix(
                current,
                events[index + 1],
                transcript_segments,
            )
        ):
            following = events[index + 1]
            evidence = list(current.get("evidence") or [])
            for item in following.get("evidence") or []:
                if item not in evidence:
                    evidence.append(item)
            if "timed_prefix_merge" not in evidence:
                evidence.append("timed_prefix_merge")
            current = {
                **current,
                "end": float(following["end"]),
                "source_text": _join_continuation_text(
                    event_source_text(current),
                    event_source_text(following),
                ),
                "translation": _join_continuation_text(
                    str(current.get("translation", "") or ""),
                    str(following.get("translation", "") or ""),
                ),
                "evidence": evidence,
            }
            merged_event_count += 1
            index += 2
        else:
            index += 1
        merged.append(current)
    return merged, merged_event_count


def _preferred_segment_ratios(segments: list[dict]) -> list[float]:
    widths = [max(1, display_units(str(segment.get("text", "")))) for segment in segments]
    total = sum(widths)
    cumulative = 0
    ratios = []
    for width in widths[:-1]:
        cumulative += width
        ratios.append(cumulative / total)
    return ratios


def _internal_strong_positions(text: str) -> list[int]:
    stripped = text.strip()
    positions = []
    for index, character in enumerate(stripped[:-1]):
        if character not in STRONG_BREAKS:
            continue
        position = index + 1
        while (
            position < len(stripped)
            and stripped[position] in TRAILING_CLOSERS
        ):
            position += 1
        remainder = stripped[position:].strip()
        if not remainder:
            continue
        positions.append(position)
    return positions


def _internal_sentence_count(text: str) -> int:
    return len(_internal_strong_positions(text))


def _part_count(event: dict[str, Any], segments: list[dict]) -> int:
    if "timed_prefix_merge" in (event.get("evidence") or []):
        return 1
    source = event_source_text(event)
    translation = str(event.get("translation", ""))
    source_sentences = _internal_sentence_count(source) + 1
    translation_sentences = _internal_sentence_count(translation) + 1
    timed_segments = _word_timed_segments(segments)
    has_word_segment_evidence = len(timed_segments) >= 2
    count = source_sentences if source_sentences == translation_sentences else 1
    if (
        count == 1
        and has_word_segment_evidence
        and {source_sentences, translation_sentences} == {1, 2}
    ):
        count = 2
    if (
        count == 1
        and source_sentences == 1
        and translation_sentences == 1
        and has_word_segment_evidence
        and any(
            _has_unfinished_japanese_tail(str(segment.get("text", "")))
            for segment in timed_segments[:-1]
        )
    ):
        count = 2
    duration = float(event["end"]) - float(event["start"])
    return min(count, max(1, int(duration / 0.45)), 6)


def _timing_boundaries(
    event: dict[str, Any],
    segments: list[dict],
    ratios: list[float],
) -> list[float]:
    if not ratios:
        return []
    candidates = []
    word_widths = []
    words = []
    for segment in segments:
        segment_words = segment.get("words") or []
        if segment_words:
            words.extend(segment_words)
        else:
            words.append(
                {
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": segment.get("text", ""),
                }
            )
    for word in words:
        word_widths.append(max(1, display_units(str(word.get("text", "")))))
    total_width = sum(word_widths)
    cumulative_width = 0
    for word, width in zip(words[:-1], word_widths[:-1]):
        cumulative_width += width
        candidates.append(
            (
                cumulative_width / max(1, total_width),
                float(word["end"]),
            )
        )
    start = float(event["start"])
    end = float(event["end"])
    boundaries = []
    previous = start
    for index, ratio in enumerate(ratios):
        remaining = len(ratios) - index
        valid = [
            candidate
            for candidate in candidates
            if candidate[1] >= previous + 0.35
            and candidate[1] <= end - 0.35 * remaining
        ]
        if valid:
            _, boundary = min(
                valid,
                key=lambda candidate: abs(candidate[0] - ratio),
            )
        else:
            boundary = start + (end - start) * ratio
            boundary = max(previous + 0.35, boundary)
            boundary = min(end - 0.35 * remaining, boundary)
        boundaries.append(round(boundary, 3))
        previous = boundary
    return boundaries


def reflow_events(
    events: list[dict[str, Any]],
    transcript_segments: list[dict],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    output = []
    split_event_count = 0
    merged_events, merged_event_count = _merge_short_prefixes(
        events,
        transcript_segments,
    )
    for event in merged_events:
        segments = _matching_segments(event, transcript_segments)
        part_count = _part_count(event, segments)
        if part_count <= 1:
            output.append({**event})
            continue
        preferred_ratios = _preferred_segment_ratios(segments)
        source_parts, source_ratios = _split_text(
            event_source_text(event),
            part_count,
            SOURCE_MAX_DISPLAY_UNITS,
            preferred_ratios=preferred_ratios,
        )
        if len(source_parts) != part_count:
            output.append({**event})
            continue
        translation_parts, _ = _split_text(
            str(event.get("translation", "")),
            part_count,
            TRANSLATION_MAX_DISPLAY_UNITS,
            target_ratios=source_ratios,
        )
        if len(translation_parts) != part_count:
            output.append({**event})
            continue
        boundaries = _timing_boundaries(event, segments, source_ratios)
        if len(boundaries) != part_count - 1:
            output.append({**event})
            continue
        times = [
            float(event["start"]),
            *boundaries,
            float(event["end"]),
        ]
        for index, (source, translation) in enumerate(
            zip(source_parts, translation_parts)
        ):
            evidence = list(event.get("evidence") or [])
            if "timed_sentence_reflow" not in evidence:
                evidence.append("timed_sentence_reflow")
            output.append(
                {
                    **event,
                    "start": round(times[index], 3),
                    "end": round(times[index + 1] - (0.02 if index < part_count - 1 else 0.0), 3),
                    "source_text": source,
                    "translation": translation,
                    "evidence": evidence,
                }
            )
        split_event_count += 1
    for index, event in enumerate(output, start=1):
        section_id = str(event["id"]).split("_", 2)[:2]
        event["id"] = f"{'_'.join(section_id)}_{index:04d}"
    metrics = {
        "original_event_count": len(events),
        "new_event_count": len(output),
        "split_event_count": split_event_count,
        "merged_event_count": merged_event_count,
        "max_source_units": max(
            (display_units(event_source_text(event)) for event in output),
            default=0,
        ),
        "max_translation_units": max(
            (
                display_units(str(event.get("translation", "")))
                for event in output
            ),
            default=0,
        ),
    }
    return output, metrics


def reflow_section(
    concert_dir: Path,
    section_id: str,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    section_dir = section_directory(concert_dir, section_id)
    package = read_json(section_dir / "input.json")
    assessment = load_assessment(section_dir)
    if assessment is None:
        raise ValueError(
            f"{section_id}缺少MC复杂度评估，请先运行assess_mc_complexity.py"
        )
    assessment_failures = validate_assessment(assessment, package)
    if assessment_failures:
        codes = ", ".join(failure["code"] for failure in assessment_failures)
        raise ValueError(f"{section_id}的MC复杂度评估无效：{codes}")
    manual_conform_required = bool(
        assessment.get("manual_conform_required")
    )
    if manual_conform_required and not dry_run:
        return {
            "section_id": section_id,
            "status": "skipped_crowded_mc",
            "risk_level": assessment["risk_level"],
            "classification": assessment["classification"],
            "automatic_reflow_policy": assessment["automatic_reflow_policy"],
            "manual_conform_required": True,
        }
    events_path = section_dir / "events.json"
    report_path = section_dir / "report.json"
    events_data = read_json(events_path)
    report = read_json(report_path) if report_path.is_file() else {}
    if report.get("layout_reflow", {}).get("tool_version") == VERSION and not force:
        return {
            "section_id": section_id,
            "status": "already_reflowed",
            **report["layout_reflow"],
        }
    transcript_path = (
        concert_dir
        / "run"
        / "evidence"
        / "transcripts"
        / f"{section_id}.formal.transcript.json"
    )
    transcript_segments = [
        segment.to_mapping() for segment in load_transcript(transcript_path)
    ]
    output, metrics = reflow_events(
        events_data["events"],
        transcript_segments,
    )
    if manual_conform_required:
        return {
            "section_id": section_id,
            "status": "crowded_mc_draft_preview",
            "risk_level": assessment["risk_level"],
            "classification": assessment["classification"],
            "automatic_reflow_policy": assessment["automatic_reflow_policy"],
            "manual_conform_required": True,
            **metrics,
        }
    if not dry_run:
        backup_dir = (
            concert_dir
            / "run"
            / "evidence"
            / f"reflow-v{VERSION}"
            / "before"
            / section_id
        )
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_events = backup_dir / "events.json"
        backup_report = backup_dir / "report.json"
        if not backup_events.exists():
            shutil.copy2(events_path, backup_events)
        if report_path.is_file() and not backup_report.exists():
            shutil.copy2(report_path, backup_report)
        events_data["events"] = output
        write_json(events_path, events_data)
        report["layout_reflow"] = {
            "tool_version": VERSION,
            "applied_at": utc_now(),
            **metrics,
        }
        flags = list(report.get("review_flags") or [])
        flags.append(
            {
                "code": "timed_sentence_reflow_review_required",
                "detail": (
                    "MC events were reflowed using source word timing; "
                    f"split={metrics['split_event_count']}; "
                    f"merge={metrics['merged_event_count']}; "
                    "review sentence, continuation, and speaker boundaries"
                ),
            }
        )
        report["review_flags"] = flags
        write_json(report_path, report)
    return {
        "section_id": section_id,
        "status": "dry_run" if dry_run else "reflowed",
        **metrics,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Split dense MC events using transcript word timing"
    )
    parser.add_argument("--concert-dir", required=True, type=Path)
    parser.add_argument("--section", action="append")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    console_version("reflow_mc_events")
    concert_dir = args.concert_dir.resolve()
    manifest = load_manifest(concert_dir)
    selected = set(args.section or [])
    summaries = []
    for section in manifest["sections"]:
        if section["type"] != "mc":
            continue
        if selected and section["id"] not in selected:
            continue
        summaries.append(
            reflow_section(
                concert_dir,
                section["id"],
                dry_run=args.dry_run,
                force=args.force,
            )
        )
    for summary in summaries:
        print(
            " ".join(f"{key}={value}" for key, value in summary.items())
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

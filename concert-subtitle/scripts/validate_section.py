from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

from assess_mc_complexity import (
    assessment_hash,
    load_assessment,
    validate_assessment,
)
from common import (
    SCHEMA_VERSION,
    SOURCE_MAX_DISPLAY_UNITS,
    TRANSLATION_MAX_DISPLAY_UNITS,
    VERSION,
    canonical_hash,
    console_version,
    display_units,
    event_source_text,
    file_sha256,
    load_events,
    load_manifest,
    load_lyrics,
    manifest_path,
    manifest_section_contract,
    mc_has_disallowed_punctuation,
    normalize_text,
    package_input_hash,
    package_manifest_mismatches,
    read_json,
    update_status,
    utc_now,
    write_json,
)

EPSILON = 0.001
MIN_EVENT_DURATION_SECONDS = 0.2
SHORT_EVENT_REVIEW_SECONDS = 0.35


def _number(report: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = report.get(key, report.get("alignment", {}).get(key, default))
    return float(value if value is not None else default)


def _boolean(report: dict[str, Any], key: str) -> bool:
    return bool(report.get(key, report.get("alignment", {}).get(key, False)))


def _validate_lyric_event_sequence(
    lyric_lines: list[str],
    lyric_events: list[dict[str, Any]],
    omitted_spans: list[tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    cursor = 1
    mismatches = []
    invalid_spans = []
    omissions_by_start = {
        start: end for start, end in (omitted_spans or [])
    }

    def skip_omissions(position: int) -> int:
        while position in omissions_by_start:
            position = omissions_by_start[position] + 1
        return position

    cursor = skip_omissions(cursor)
    for event_index, event in enumerate(lyric_events):
        raw_span = event.get("lyric_line_span")
        if raw_span is None:
            start_line = cursor
            end_line = cursor
        elif (
            isinstance(raw_span, list)
            and len(raw_span) == 2
            and all(isinstance(value, int) for value in raw_span)
        ):
            start_line, end_line = raw_span
        else:
            invalid_spans.append(event_index)
            continue
        if (
            start_line != cursor
            or end_line < start_line
            or end_line > len(lyric_lines)
        ):
            invalid_spans.append(event_index)
            continue
        expected = "".join(lyric_lines[start_line - 1 : end_line])
        actual = str(
            event.get("canonical_source_text") or event_source_text(event)
        )
        if normalize_text(expected) != normalize_text(actual):
            mismatches.append(event_index)
        cursor = skip_omissions(end_line + 1)

    if invalid_spans:
        failures.append(
            {
                "code": "invalid_lyric_line_span",
                "event_indexes": invalid_spans[:20],
                "invalid_count": len(invalid_spans),
            }
        )
    if cursor != len(lyric_lines) + 1:
        failures.append(
            {
                "code": "lyrics_event_count_mismatch",
                "lyrics_count": len(lyric_lines),
                "covered_lyrics_count": cursor - 1,
                "lyric_event_count": len(lyric_events),
            }
        )
    if mismatches:
        failures.append(
            {
                "code": "lyrics_text_or_order_changed",
                "event_indexes": mismatches[:20],
                "mismatch_count": len(mismatches),
            }
        )
    return failures


def _manual_lyric_deviations(
    report: dict[str, Any],
    lyric_line_count: int,
) -> tuple[list[tuple[int, int]], list[dict[str, Any]], list[dict[str, Any]]]:
    omitted_spans: list[tuple[int, int]] = []
    hard_failures: list[dict[str, Any]] = []
    review_flags: list[dict[str, Any]] = []
    entries = report.get("manual_final_conform", {}).get(
        "omitted_lyrics",
        [],
    )
    if not isinstance(entries, list):
        return [], [{"code": "invalid_omitted_lyric_span"}], []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            hard_failures.append(
                {"code": "invalid_omitted_lyric_span", "entry_index": index}
            )
            continue
        span = entry.get("lyric_line_span")
        evidence = set(entry.get("evidence") or [])
        valid_span = (
            isinstance(span, list)
            and len(span) == 2
            and all(isinstance(value, int) for value in span)
            and 1 <= span[0] <= span[1] <= lyric_line_count
        )
        if not valid_span:
            hard_failures.append(
                {"code": "invalid_omitted_lyric_span", "entry_index": index}
            )
            continue
        if not evidence & {
            "user_manual_full_av_review",
            "continuous_av_review",
        }:
            hard_failures.append(
                {
                    "code": "omitted_lyric_missing_manual_av_evidence",
                    "entry_index": index,
                }
            )
            continue
        omitted_spans.append((span[0], span[1]))

    ordered = sorted(omitted_spans)
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] <= previous[1]:
            hard_failures.append(
                {
                    "code": "overlapping_omitted_lyric_spans",
                    "previous": list(previous),
                    "current": list(current),
                }
            )
    if ordered:
        review_flags.append(
            {
                "code": "omitted_live_lyrics_review_required",
                "spans": [list(span) for span in ordered],
            }
        )
    return ordered, hard_failures, review_flags


def validate_section(section_dir: Path, update: bool = True) -> dict[str, Any]:
    section_dir = section_dir.resolve()
    input_path = section_dir / "input.json"
    events_path = section_dir / "events.json"
    if not input_path.is_file():
        raise FileNotFoundError(f"缺少input.json：{input_path}")
    if not events_path.is_file():
        raise FileNotFoundError(f"缺少events.json：{events_path}")

    package = read_json(input_path)
    events = load_events(events_path)
    report_path = section_dir / "report.json"
    report = read_json(report_path) if report_path.is_file() else {}
    hard_failures: list[dict[str, Any]] = []
    review_flags: list[dict[str, Any]] = []
    for flag in report.get("review_flags", []):
        if isinstance(flag, dict) and flag.get("code"):
            review_flags.append({**flag, "source": "section_report"})
        elif isinstance(flag, str) and flag.strip():
            review_flags.append(
                {"code": flag.strip(), "source": "section_report"}
            )
    duration = float(package["section_end"]) - float(package["section_start"])
    mc_complexity = None
    mc_complexity_hash = None
    if not report_path.is_file():
        review_flags.append({"code": "missing_section_report"})
    if not events:
        hard_failures.append({"code": "empty_section_events"})
    computed_input_hash = package_input_hash(package)
    if package.get("input_hash") != computed_input_hash:
        hard_failures.append(
            {
                "code": "input_hash_mismatch",
                "stored": package.get("input_hash"),
                "computed": computed_input_hash,
            }
        )

    if package.get("section_type") == "mc":
        mc_complexity = load_assessment(section_dir)
        if mc_complexity is None:
            hard_failures.append({"code": "missing_mc_complexity_assessment"})
        else:
            mc_complexity_hash = assessment_hash(mc_complexity)
            hard_failures.extend(validate_assessment(mc_complexity, package))
            if mc_complexity.get("manual_conform_required") is True:
                review_flags.append(
                    {
                        "code": "crowded_mc_manual_conform_required",
                        "risk_level": mc_complexity.get("risk_level"),
                        "classification": mc_complexity.get("classification"),
                        "signals": mc_complexity.get("signals", []),
                    }
                )
            elif mc_complexity.get("risk_level") == "medium":
                review_flags.append(
                    {
                        "code": "managed_dialogue_review_required",
                        "classification": mc_complexity.get("classification"),
                    }
                )

    concert_dir = section_dir.parents[2]
    if manifest_path(concert_dir).is_file():
        manifest = load_manifest(concert_dir)
        section_indexes = [
            index
            for index, section in enumerate(manifest["sections"])
            if section["id"] == package.get("section_id")
        ]
        if not section_indexes:
            hard_failures.append({"code": "section_missing_from_manifest"})
        else:
            section_index = section_indexes[0]
            expected_manifest_hash = canonical_hash(
                manifest_section_contract(manifest, section_index)
            )
            if package.get("manifest_section_hash") != expected_manifest_hash:
                hard_failures.append({"code": "manifest_changed_after_package"})
            mismatched_fields = package_manifest_mismatches(
                package,
                manifest,
                section_index,
            )
            if mismatched_fields:
                hard_failures.append(
                    {
                        "code": "package_manifest_field_mismatch",
                        "fields": mismatched_fields,
                    }
                )

    previous_start = -1.0
    previous_end = -1.0
    for index, event in enumerate(events):
        try:
            start = float(event["start"])
            end = float(event["end"])
        except (KeyError, TypeError, ValueError):
            hard_failures.append({"code": "invalid_time", "event_index": index})
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            hard_failures.append({"code": "non_finite_time", "event_index": index})
            continue
        if start < previous_start - EPSILON:
            hard_failures.append({"code": "event_order_changed", "event_index": index})
        if start >= end:
            hard_failures.append({"code": "invalid_time", "event_index": index})
        elif end - start < MIN_EVENT_DURATION_SECONDS - EPSILON:
            hard_failures.append(
                {
                    "code": "unreadable_event_duration",
                    "event_index": index,
                    "duration_seconds": end - start,
                    "minimum_seconds": MIN_EVENT_DURATION_SECONDS,
                }
            )
        elif end - start < SHORT_EVENT_REVIEW_SECONDS - EPSILON:
            review_flags.append(
                {
                    "code": "very_short_event_duration",
                    "event_index": index,
                    "duration_seconds": end - start,
                }
            )
        if start < -EPSILON or end > duration + EPSILON:
            hard_failures.append(
                {
                    "code": "outside_section",
                    "event_index": index,
                    "start": start,
                    "end": end,
                    "duration": duration,
                }
            )
        if previous_end >= 0 and start < previous_end - EPSILON:
            hard_failures.append(
                {
                    "code": "overlap",
                    "event_index": index,
                    "overlap_seconds": previous_end - start,
                }
            )
        if not normalize_text(event_source_text(event)):
            hard_failures.append({"code": "empty_source_text", "event_index": index})
        if not normalize_text(str(event.get("translation", ""))):
            hard_failures.append(
                {"code": "missing_translation", "event_index": index}
            )
        if package["section_type"] == "mc":
            for field_name, field_text in (
                ("source_text", event_source_text(event)),
                ("translation", str(event.get("translation", "") or "")),
            ):
                if mc_has_disallowed_punctuation(field_text):
                    hard_failures.append(
                        {
                            "code": "mc_disallowed_punctuation",
                            "event_index": index,
                            "field": field_name,
                        }
                    )
        role = str(
            event.get(
                "role",
                "speech" if package["section_type"] == "mc" else "lyric",
            )
        )
        if role not in {"lyric", "speech", "lyric_adlib"}:
            hard_failures.append(
                {
                    "code": "invalid_event_role",
                    "event_index": index,
                    "role": role,
                }
            )
        if role == "lyric_adlib":
            if package["section_type"] != "song":
                hard_failures.append(
                    {
                        "code": "lyric_adlib_outside_song",
                        "event_index": index,
                    }
                )
            evidence = set(event.get("evidence") or [])
            has_timing_evidence = any(
                item.startswith("asr_") or item.startswith("word_timing")
                for item in evidence
            )
            if not evidence & {
                "user_manual_full_av_review",
                "continuous_av_review",
            } and not has_timing_evidence:
                hard_failures.append(
                    {
                        "code": "lyric_adlib_missing_evidence",
                        "event_index": index,
                    }
                )
            review_flags.append(
                {
                    "code": "lyric_adlib_review_required",
                    "event_index": index,
                }
            )
        elif role == "speech":
            source_units = display_units(event_source_text(event))
            translation_units = display_units(
                str(event.get("translation", ""))
            )
            if (
                package["section_type"] == "mc"
                and (
                    source_units > SOURCE_MAX_DISPLAY_UNITS
                    or translation_units > TRANSLATION_MAX_DISPLAY_UNITS
                )
            ):
                hard_failures.append(
                    {
                        "code": "overlong_mc_event",
                        "event_index": index,
                        "source_units": source_units,
                        "translation_units": translation_units,
                    }
                )
            if package["section_type"] == "song":
                evidence = set(event.get("evidence") or [])
                if not any(
                    item.startswith("asr_") or item.startswith("word_timing")
                    for item in evidence
                ):
                    hard_failures.append(
                        {
                            "code": "embedded_speech_missing_asr_evidence",
                            "event_index": index,
                        }
                    )
                review_flags.append(
                    {
                        "code": "embedded_speech_review_required",
                        "event_index": index,
                    }
                )
        previous_start = start
        previous_end = end

    if package["section_type"] == "song":
        lyric_events = [
            event for event in events if event.get("role", "lyric") == "lyric"
        ]
        for index, event in enumerate(lyric_events):
            canonical = event.get("canonical_source_text")
            if canonical is not None and normalize_text(str(canonical)) != normalize_text(
                event_source_text(event)
            ):
                evidence = set(event.get("evidence") or [])
                if not evidence & {
                    "user_manual_full_av_review",
                    "continuous_av_review",
                }:
                    hard_failures.append(
                        {
                            "code": (
                                "live_lyric_variant_missing_manual_av_evidence"
                            ),
                            "event_index": index,
                        }
                    )
                review_flags.append(
                    {
                        "code": "live_lyric_variant_review_required",
                        "event_index": index,
                    }
                )
            span = event.get("lyric_line_span")
            if (
                isinstance(span, list)
                and len(span) == 2
                and all(isinstance(value, int) for value in span)
                and span[1] > span[0]
            ):
                review_flags.append(
                    {
                        "code": "lyric_semantic_merge_review_required",
                        "event_index": index,
                        "lyric_line_span": span,
                    }
                )
        translation_name = package.get("translation_file")
        translation_path = (
            section_dir / translation_name if translation_name else None
        )
        if translation_path and translation_path.is_file():
            expected_translation_hash = package.get("translation_hash")
            if (
                expected_translation_hash
                and file_sha256(translation_path) != expected_translation_hash
            ):
                hard_failures.append(
                    {"code": "translation_file_changed_after_package"}
                )
        lyrics_name = package.get("lyrics_file")
        lyrics_path = section_dir / lyrics_name if lyrics_name else None
        if lyrics_path and lyrics_path.is_file():
            expected_lyrics_hash = package.get("lyrics_hash")
            if (
                expected_lyrics_hash
                and file_sha256(lyrics_path) != expected_lyrics_hash
            ):
                hard_failures.append(
                    {"code": "lyrics_file_changed_after_package"}
                )
            lyric_lines = load_lyrics(
                lyrics_path,
                initial_metadata=[
                    package.get("title", ""),
                    package.get("artist", ""),
                ],
            )
            omitted_spans, omission_failures, omission_flags = (
                _manual_lyric_deviations(report, len(lyric_lines))
            )
            hard_failures.extend(omission_failures)
            review_flags.extend(omission_flags)
            hard_failures.extend(
                _validate_lyric_event_sequence(
                    lyric_lines,
                    lyric_events,
                    omitted_spans,
                )
            )
        else:
            hard_failures.append({"code": "missing_known_lyrics"})

        first_vocal_start = report.get("first_vocal_start")
        if lyric_events and first_vocal_start is not None:
            first_vocal = float(first_vocal_start)
            if not math.isfinite(first_vocal):
                hard_failures.append({"code": "non_finite_first_vocal_start"})
            else:
                lead = first_vocal - float(lyric_events[0]["start"])
            if math.isfinite(first_vocal) and lead > 0.35:
                hard_failures.append(
                    {
                        "code": "lyrics_before_vocal",
                        "lead_seconds": lead,
                        "first_event_start": float(lyric_events[0]["start"]),
                        "first_vocal_start": first_vocal,
                    }
                )
        elif lyric_events:
            review_flags.append({"code": "missing_first_vocal_start"})

        for gap in report.get("instrumental_gaps", []):
            if float(gap.get("confidence", 1.0)) < 0.75:
                continue
            gap_start = float(gap["start"])
            gap_end = float(gap["end"])
            if (
                not math.isfinite(gap_start)
                or not math.isfinite(gap_end)
                or gap_end <= gap_start
            ):
                hard_failures.append({"code": "invalid_instrumental_gap"})
                continue
            for index, event in enumerate(lyric_events):
                overlap = min(float(event["end"]), gap_end) - max(
                    float(event["start"]), gap_start
                )
                if overlap > 0.25:
                    hard_failures.append(
                        {
                            "code": "lyrics_in_instrumental_gap",
                            "event_index": index,
                            "overlap_seconds": overlap,
                            "gap_start": gap_start,
                            "gap_end": gap_end,
                        }
                    )

        correction = _number(report, "max_correction_seconds")
        extrapolation = _number(report, "max_extrapolation_seconds")
        slope = _number(report, "alignment_slope", 1.0)
        anchor_ratio = _number(report, "first_reliable_anchor_ratio")
        metrics = {
            "max_correction_seconds": correction,
            "max_extrapolation_seconds": extrapolation,
            "alignment_slope": slope,
            "first_reliable_anchor_ratio": anchor_ratio,
        }
        non_finite_metrics = [
            key for key, value in metrics.items() if not math.isfinite(value)
        ]
        if non_finite_metrics:
            hard_failures.append(
                {
                    "code": "non_finite_alignment_metric",
                    "fields": non_finite_metrics,
                }
            )
        if correction > 5.0:
            hard_failures.append(
                {"code": "excessive_correction", "seconds": correction}
            )
        elif correction > 2.0:
            review_flags.append(
                {"code": "large_correction", "seconds": correction}
            )
        if extrapolation > 5.0:
            hard_failures.append(
                {"code": "excessive_extrapolation", "seconds": extrapolation}
            )
        elif extrapolation > 2.0:
            review_flags.append(
                {"code": "large_extrapolation", "seconds": extrapolation}
            )
        if slope < 0.75 or slope > 1.35:
            hard_failures.append({"code": "unsafe_alignment_slope", "slope": slope})
        elif slope < 0.85 or slope > 1.15:
            review_flags.append({"code": "unusual_alignment_slope", "slope": slope})
        if anchor_ratio > 0.2:
            review_flags.append(
                {"code": "late_first_reliable_anchor", "ratio": anchor_ratio}
            )
        if _boolean(report, "unsafe_reference_extrapolation"):
            hard_failures.append({"code": "unsafe_reference_extrapolation"})

    if float(package.get("boundary_confidence", 0.5)) < 0.6:
        review_flags.append(
            {
                "code": "low_boundary_confidence",
                "confidence": float(package.get("boundary_confidence", 0.5)),
            }
        )

    if "structure_timing" in report and package.get("section_type") == "song":
        from structure_review import applied_structure_is_current
        if not applied_structure_is_current(section_dir, package, events, report):
            review_flags.append({"code": "complete_cycle_transfer_review_required"})

    automatic_result = "failed" if hard_failures else "passed"
    result_hash = canonical_hash({"events": events, "report": report})
    gate_report = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": VERSION,
        "section_id": package["section_id"],
        "input_hash": package["input_hash"],
        "result_hash": result_hash,
        "mc_complexity_hash": mc_complexity_hash,
        "checked_at": utc_now(),
        "automatic_result": automatic_result,
        "event_count": len(events),
        "hard_failures": hard_failures,
        "review_flags": review_flags,
    }
    write_json(section_dir / "gate_report.json", gate_report)
    if update:
        if hard_failures:
            update_status(
                section_dir,
                "failed",
                "automatic_gate_failed",
                section_id=package["section_id"],
                input_hash=package["input_hash"],
            )
        else:
            decision_path = section_dir / "review_decision.json"
            decision = read_json(decision_path) if decision_path.is_file() else {}
            keep_approved = (
                decision.get("decision") == "approved"
                and decision.get("input_hash") == package["input_hash"]
                and decision.get("result_hash") == result_hash
                and (
                    package.get("section_type") != "mc"
                    or decision.get("mc_complexity_hash") == mc_complexity_hash
                )
            )
            if not keep_approved:
                update_status(
                    section_dir,
                    "needs_review",
                    "automatic_gates_passed",
                    section_id=package["section_id"],
                    input_hash=package["input_hash"],
                    review_flag_count=len(review_flags),
                )
    return gate_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="执行单个歌曲或MC质量gate")
    parser.add_argument("--section-dir", required=True, type=Path)
    parser.add_argument("--no-status-update", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    console_version("validate_section")
    report = validate_section(args.section_dir, not args.no_status_update)
    print(f"section={report['section_id']}")
    print(f"automatic_result={report['automatic_result']}")
    print(f"hard_failures={len(report['hard_failures'])}")
    print(f"review_flags={len(report['review_flags'])}")
    return 2 if report["automatic_result"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
from pathlib import Path

from assess_mc_complexity import assessment_hash, load_assessment
from common import (
    SCHEMA_VERSION,
    VERSION,
    canonical_hash,
    console_version,
    load_events,
    package_input_hash,
    read_json,
    update_status,
    utc_now,
    write_json,
)

REQUIRED_POINTS = {
    "song": {
        "full_section",
    },
    "mc": {
        "full_section",
        "continuous_av_playback",
    },
}
FULL_REVIEW_CODES = {
    "missing_known_lyrics",
    "missing_section_report",
    "missing_first_vocal_start",
    "large_correction",
    "large_extrapolation",
    "unusual_alignment_slope",
    "late_first_reliable_anchor",
    "low_boundary_confidence",
    "low_confidence_transcription",
    "very_short_event_duration",
    "timed_sentence_reflow_review_required",
    "embedded_speech_review_required",
    "lyric_semantic_merge_review_required",
    "lyric_adlib_review_required",
    "live_lyric_variant_review_required",
    "omitted_live_lyrics_review_required",
    "crowded_mc_manual_conform_required",
}
CROWDED_MC_REVIEW_POINTS = {
    "continuous_av_playback",
    "speaker_turns",
    "utterance_boundaries",
    "source_transcription_rechecked",
    "translation_after_segmentation",
}


def record_review(
    section_dir: Path,
    decision: str,
    points: set[str],
    notes: str,
    reviewer: str,
) -> dict:
    section_dir = section_dir.resolve()
    package = read_json(section_dir / "input.json")
    gate = read_json(section_dir / "gate_report.json")
    if package.get("input_hash") != package_input_hash(package):
        raise ValueError("input.json内容与input_hash不一致，请重建工作包")
    if gate.get("input_hash") != package.get("input_hash"):
        raise ValueError("gate_report与当前输入哈希不一致，请重新运行validate_section")
    report_path = section_dir / "report.json"
    report = read_json(report_path) if report_path.is_file() else {}
    result_hash = canonical_hash(
        {
            "events": load_events(section_dir / "events.json"),
            "report": report,
        }
    )
    if gate.get("result_hash") != result_hash:
        raise ValueError("分段结果在自动gate后发生变化，请重新运行validate_section")
    mc_complexity_hash = None
    if package.get("section_type") == "mc":
        assessment = load_assessment(section_dir)
        mc_complexity_hash = assessment_hash(assessment)
        if assessment is None:
            raise ValueError("缺少MC复杂度评估，不能记录MC批准")
        if gate.get("mc_complexity_hash") != mc_complexity_hash:
            raise ValueError("MC复杂度评估在自动gate后发生变化，请重新运行validate_section")

    if decision == "approve":
        if "structure_timing" in report and package.get("section_type") == "song":
            from structure_review import applied_structure_is_current
            if not applied_structure_is_current(section_dir, package, load_events(section_dir / "events.json"), report):
                raise ValueError("已应用的单曲结构复用记录已失效，请复核该曲结果")
        if gate.get("automatic_result") != "passed":
            raise ValueError("自动gate未通过，不能批准")
        missing = REQUIRED_POINTS[package["section_type"]] - points
        flag_codes = {
            flag.get("code")
            for flag in gate.get("review_flags", [])
        }
        if (
            package["section_type"] == "mc"
            and (
                package.get("review_priority") == "high"
                or flag_codes & FULL_REVIEW_CODES
            )
            and "full_section" not in points
        ):
            missing.add("full_section")
        if "crowded_mc_manual_conform_required" in flag_codes:
            missing.update(CROWDED_MC_REVIEW_POINTS - points)
        if missing:
            raise ValueError(f"缺少必查点：{', '.join(sorted(missing))}")
        stored_decision = "approved"
        status = "approved"
        reason = "main_review_approved"
    else:
        stored_decision = "rejected"
        status = "failed"
        reason = "main_review_rejected"

    result = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": VERSION,
        "section_id": package["section_id"],
        "input_hash": package["input_hash"],
        "result_hash": result_hash,
        "decision": stored_decision,
        "reviewed_points": sorted(points),
        "notes": notes,
        "reviewed_at": utc_now(),
        "reviewer": reviewer,
    }
    if mc_complexity_hash is not None:
        result["mc_complexity_hash"] = mc_complexity_hash
    write_json(section_dir / "review_decision.json", result)
    update_status(
        section_dir,
        status,
        reason,
        section_id=package["section_id"],
        input_hash=package["input_hash"],
        reviewer=reviewer,
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="记录主线程分段审查决定")
    parser.add_argument("--section-dir", required=True, type=Path)
    parser.add_argument("--decision", required=True, choices=["approve", "reject"])
    parser.add_argument("--point", action="append", default=[])
    parser.add_argument("--notes", default="")
    parser.add_argument("--reviewer", default="main")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    console_version("review_section")
    result = record_review(
        args.section_dir,
        args.decision,
        set(args.point),
        args.notes,
        args.reviewer,
    )
    print(f"section={result['section_id']}")
    print(f"decision={result['decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

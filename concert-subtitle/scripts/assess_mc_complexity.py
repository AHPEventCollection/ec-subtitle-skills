from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import (
    SCHEMA_VERSION,
    VERSION,
    canonical_hash,
    console_version,
    package_input_hash,
    read_json,
    utc_now,
    write_json,
)


CLASSIFICATIONS = {
    "single_speaker",
    "managed_dialogue",
    "crowded_multi_speaker",
}
RISK_SIGNALS = {
    "overlapping_speech",
    "rapid_turn_taking",
    "speaker_identity_uncertain",
    "off_camera_speech",
    "crowd_interjections",
}
EVIDENCE_BASES = {
    "continuous_av_review",
    "audio_review",
    "video_review",
    "static_video_frames",
    "asr_word_timing",
}
AUDIO_EVIDENCE_BASES = {
    "continuous_av_review",
    "audio_review",
}
HIGH_RISK_SIGNALS = {
    "overlapping_speech",
    "rapid_turn_taking",
    "speaker_identity_uncertain",
    "off_camera_speech",
    "crowd_interjections",
}


def assessment_path(section_dir: Path) -> Path:
    return section_dir / "evidence" / "mc_complexity_assessment.json"


def derive_policy(
    classification: str,
    speaker_count_estimate: int,
    signals: set[str],
) -> tuple[str, bool, str]:
    if (
        classification == "crowded_multi_speaker"
        or speaker_count_estimate >= 3
        or signals & HIGH_RISK_SIGNALS
    ):
        return "high", True, "draft_only"
    if classification == "managed_dialogue":
        return "medium", False, "review_required"
    return "low", False, "review_required"


def validate_assessment(
    assessment: dict[str, Any],
    package: dict[str, Any],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    classification = str(assessment.get("classification", ""))
    signals = {
        str(signal)
        for signal in assessment.get("signals", [])
    }
    evidence_basis = {
        str(item)
        for item in assessment.get("evidence_basis", [])
    }
    try:
        speaker_count = int(assessment.get("speaker_count_estimate", 0))
    except (TypeError, ValueError):
        speaker_count = 0

    if assessment.get("schema_version") != SCHEMA_VERSION:
        failures.append({"code": "mc_complexity_schema_mismatch"})
    if assessment.get("section_id") != package.get("section_id"):
        failures.append({"code": "mc_complexity_section_mismatch"})
    if assessment.get("input_hash") != package.get("input_hash"):
        failures.append({"code": "stale_mc_complexity_assessment"})
    if classification not in CLASSIFICATIONS:
        failures.append({"code": "invalid_mc_complexity_classification"})
    if speaker_count < 1:
        failures.append({"code": "invalid_mc_speaker_count"})
    if signals - RISK_SIGNALS:
        failures.append(
            {
                "code": "invalid_mc_complexity_signal",
                "signals": sorted(signals - RISK_SIGNALS),
            }
        )
    if not evidence_basis or evidence_basis - EVIDENCE_BASES:
        failures.append({"code": "invalid_mc_complexity_evidence_basis"})
    if not evidence_basis & AUDIO_EVIDENCE_BASES:
        failures.append({"code": "mc_complexity_missing_audio_review"})

    if (
        classification in CLASSIFICATIONS
        and speaker_count >= 1
        and not signals - RISK_SIGNALS
    ):
        risk_level, manual_required, reflow_policy = derive_policy(
            classification,
            speaker_count,
            signals,
        )
        if assessment.get("risk_level") != risk_level:
            failures.append({"code": "mc_complexity_risk_mismatch"})
        if assessment.get("manual_conform_required") is not manual_required:
            failures.append({"code": "mc_manual_conform_policy_mismatch"})
        if assessment.get("automatic_reflow_policy") != reflow_policy:
            failures.append({"code": "mc_reflow_policy_mismatch"})
    return failures


def load_assessment(section_dir: Path) -> dict[str, Any] | None:
    path = assessment_path(section_dir)
    return read_json(path) if path.is_file() else None


def assessment_hash(assessment: dict[str, Any] | None) -> str | None:
    return canonical_hash(assessment) if assessment is not None else None


def assess_section(
    section_dir: Path,
    classification: str,
    speaker_count_estimate: int,
    signals: set[str],
    evidence_basis: set[str],
    notes: str,
    reviewer: str,
) -> dict[str, Any]:
    section_dir = section_dir.resolve()
    package = read_json(section_dir / "input.json")
    if package.get("section_type") != "mc":
        raise ValueError("MC复杂度评估只能用于MC分段")
    if package.get("input_hash") != package_input_hash(package):
        raise ValueError("input.json内容与input_hash不一致，请重建工作包")
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"未知MC复杂度分类：{classification}")
    if speaker_count_estimate < 1:
        raise ValueError("speaker_count_estimate必须至少为1")
    if signals - RISK_SIGNALS:
        raise ValueError(f"未知MC风险信号：{', '.join(sorted(signals - RISK_SIGNALS))}")
    if not evidence_basis or evidence_basis - EVIDENCE_BASES:
        raise ValueError("MC复杂度评估必须使用受支持的证据类型")
    if not evidence_basis & AUDIO_EVIDENCE_BASES:
        raise ValueError("MC复杂度评估必须包含连续音画或音频回听，静态画面和ASR不足以分级")

    risk_level, manual_required, reflow_policy = derive_policy(
        classification,
        speaker_count_estimate,
        signals,
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": VERSION,
        "section_id": package["section_id"],
        "input_hash": package["input_hash"],
        "classification": classification,
        "speaker_count_estimate": speaker_count_estimate,
        "signals": sorted(signals),
        "evidence_basis": sorted(evidence_basis),
        "risk_level": risk_level,
        "manual_conform_required": manual_required,
        "automatic_reflow_policy": reflow_policy,
        "notes": notes,
        "assessed_by": reviewer,
        "assessed_at": utc_now(),
    }
    write_json(assessment_path(section_dir), result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="记录MC说话场景复杂度并决定自动重排是否只限草稿"
    )
    parser.add_argument("--section-dir", required=True, type=Path)
    parser.add_argument(
        "--classification",
        required=True,
        choices=sorted(CLASSIFICATIONS),
    )
    parser.add_argument("--speaker-count-estimate", required=True, type=int)
    parser.add_argument(
        "--signal",
        action="append",
        default=[],
        choices=sorted(RISK_SIGNALS),
    )
    parser.add_argument(
        "--evidence-basis",
        action="append",
        required=True,
        choices=sorted(EVIDENCE_BASES),
    )
    parser.add_argument("--notes", default="")
    parser.add_argument("--reviewer", default="main")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    console_version("assess_mc_complexity")
    result = assess_section(
        args.section_dir,
        args.classification,
        args.speaker_count_estimate,
        set(args.signal),
        set(args.evidence_basis),
        args.notes,
        args.reviewer,
    )
    print(f"section={result['section_id']}")
    print(f"risk_level={result['risk_level']}")
    print(f"automatic_reflow_policy={result['automatic_reflow_policy']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

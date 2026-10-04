from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import (
    SCHEMA_VERSION,
    VERSION,
    canonical_hash,
    load_manifest,
    read_json,
    section_directory,
    utc_now,
    write_json,
)

RESOLVED_CANDIDATE_STATUSES = {
    "approved",
    "known_lyric",
    "crowd_nonlexical",
    "rejected_hallucination",
}
RESOLVED_GAP_DISPOSITIONS = {
    "approved_speech",
    "crowd_nonlexical",
    "credits",
    "music_only",
    "no_speech",
}
GAP_TOLERANCE_SECONDS = 0.5


def timeline_gaps(
    duration: float,
    sections: list[dict[str, Any]],
) -> list[dict[str, float]]:
    intervals = sorted(
        (
            max(0.0, float(section["start"])),
            min(duration, float(section["end"])),
        )
        for section in sections
    )
    gaps = []
    cursor = 0.0
    for start, end in intervals:
        if start > cursor + GAP_TOLERANCE_SECONDS:
            gaps.append(
                {
                    "start": round(cursor, 3),
                    "end": round(start, 3),
                    "duration": round(start - cursor, 3),
                }
            )
        cursor = max(cursor, end)
    if duration > cursor + GAP_TOLERANCE_SECONDS:
        gaps.append(
            {
                "start": round(cursor, 3),
                "end": round(duration, 3),
                "duration": round(duration - cursor, 3),
            }
        )
    return gaps


def _gap_is_resolved(
    gap: dict[str, float],
    decisions: list[dict[str, Any]],
) -> bool:
    for decision in decisions:
        try:
            start = float(decision["start"])
            end = float(decision["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            start <= gap["start"] + GAP_TOLERANCE_SECONDS
            and end >= gap["end"] - GAP_TOLERANCE_SECONDS
            and decision.get("disposition") in RESOLVED_GAP_DISPOSITIONS
        ):
            return True
    return False


def audit_coverage(concert_dir: Path) -> dict[str, Any]:
    concert_dir = concert_dir.resolve()
    manifest = load_manifest(concert_dir)
    gaps = timeline_gaps(
        float(manifest["duration_seconds"]),
        manifest["sections"],
    )
    decisions_path = concert_dir / "run" / "coverage_gap_decisions.json"
    decisions_data = (
        read_json(decisions_path) if decisions_path.is_file() else {}
    )
    decisions = decisions_data.get("decisions") or []
    unresolved_gaps = [
        gap for gap in gaps if not _gap_is_resolved(gap, decisions)
    ]

    missing_song_scans = []
    unresolved_candidates = []
    candidate_count = 0
    for section in manifest["sections"]:
        if section["type"] != "song":
            continue
        candidate_path = (
            section_directory(concert_dir, section["id"])
            / "speech_candidates.json"
        )
        if not candidate_path.is_file():
            missing_song_scans.append(section["id"])
            continue
        candidate_data = read_json(candidate_path)
        candidates = candidate_data.get("candidates") or []
        candidate_count += len(candidates)
        for candidate in candidates:
            if candidate.get("status") not in RESOLVED_CANDIDATE_STATUSES:
                unresolved_candidates.append(
                    {
                        "section_id": section["id"],
                        "candidate_id": candidate.get("id"),
                        "start": candidate.get("start"),
                        "end": candidate.get("end"),
                        "source_text": candidate.get("source_text"),
                        "status": candidate.get("status", "missing"),
                    }
                )

    ready = not (
        missing_song_scans
        or unresolved_candidates
        or unresolved_gaps
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": VERSION,
        "concert_id": manifest["concert_id"],
        "created_at": utc_now(),
        "coverage_policy": manifest.get(
            "coverage_policy",
            "legacy_section_only",
        ),
        "manifest_hash": canonical_hash(manifest),
        "ready_for_formal_output": ready,
        "song_scan_count": sum(
            1 for section in manifest["sections"] if section["type"] == "song"
        )
        - len(missing_song_scans),
        "missing_song_scans": missing_song_scans,
        "candidate_count": candidate_count,
        "unresolved_candidate_count": len(unresolved_candidates),
        "unresolved_candidates": unresolved_candidates,
        "timeline_gap_count": len(gaps),
        "timeline_gap_seconds": round(
            sum(gap["duration"] for gap in gaps),
            3,
        ),
        "unresolved_timeline_gaps": unresolved_gaps,
    }
    write_json(
        concert_dir / "run" / "coverage_audit_report.json",
        report,
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="审计全场时间线与歌曲段歌词外讲话候选是否全部得到裁决"
    )
    parser.add_argument("--concert-dir", required=True, type=Path)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = audit_coverage(args.concert_dir)
    print(f"ready_for_formal_output={report['ready_for_formal_output']}")
    print(f"candidate_count={report['candidate_count']}")
    print(
        "unresolved_candidate_count="
        f"{report['unresolved_candidate_count']}"
    )
    print(f"timeline_gap_seconds={report['timeline_gap_seconds']}")
    print(
        "unresolved_timeline_gap_count="
        f"{len(report['unresolved_timeline_gaps'])}"
    )
    return 0 if report["ready_for_formal_output"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

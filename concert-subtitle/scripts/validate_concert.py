from __future__ import annotations

import argparse
from pathlib import Path

from common import (
    SCHEMA_VERSION,
    VERSION,
    canonical_hash,
    console_version,
    read_json,
    utc_now,
    write_json,
)
from merge_concert_subtitles import collect_absolute_events, render_ass
from song_progress import validate_song_progress_ass


def validate_concert(concert_dir: Path) -> dict:
    concert_dir = concert_dir.resolve()
    manifest, events, failures = collect_absolute_events(concert_dir, True)
    if manifest.get("coverage_policy") == "full_timeline_asr":
        coverage_path = concert_dir / "run" / "coverage_audit_report.json"
        if not coverage_path.is_file():
            failures.append({"code": "missing_coverage_audit_report"})
        else:
            coverage = read_json(coverage_path)
            if coverage.get("manifest_hash") != canonical_hash(manifest):
                failures.append({"code": "stale_coverage_audit_report"})
            if not coverage.get("ready_for_formal_output"):
                failures.append(
                    {
                        "code": "concert_coverage_not_ready",
                        "unresolved_candidate_count": coverage.get(
                            "unresolved_candidate_count"
                        ),
                        "unresolved_timeline_gap_count": len(
                            coverage.get("unresolved_timeline_gaps") or []
                        ),
                    }
                )
    output_dir = concert_dir / "output"
    base_name = manifest["concert_id"]
    required_outputs = {
        "ass": output_dir / f"{base_name}.ass",
    }
    required_evidence = {
        "events": (
            concert_dir
            / "run"
            / "evidence"
            / f"{base_name}.merged.events.json"
        ),
        "report": (
            concert_dir
            / "run"
            / "evidence"
            / f"{base_name}.merge.report.json"
        ),
    }
    for output_type, path in required_outputs.items():
        if not path.is_file():
            failures.append(
                {
                    "code": "missing_formal_output",
                    "output_type": output_type,
                    "path": str(path),
                }
            )
    for evidence_type, path in required_evidence.items():
        if not path.is_file():
            failures.append(
                {
                    "code": "missing_merge_evidence",
                    "evidence_type": evidence_type,
                    "path": str(path),
                }
            )

    output_report_path = required_evidence["report"]
    if output_report_path.is_file():
        output_report = read_json(output_report_path)
        if int(output_report.get("event_count", -1)) != len(events):
            failures.append(
                {
                    "code": "output_event_count_mismatch",
                    "expected": len(events),
                    "actual": output_report.get("event_count"),
                }
            )
        if output_report.get("result") != "passed":
            failures.append({"code": "output_report_not_passed"})

    output_events_path = required_evidence["events"]
    if output_events_path.is_file():
        output_events_data = read_json(output_events_path)
        output_events = output_events_data.get("events", [])
        if canonical_hash(output_events) != canonical_hash(events):
            failures.append({"code": "output_events_content_mismatch"})

    ass_path = required_outputs["ass"]
    if ass_path.is_file():
        ass_bytes = ass_path.read_bytes()
        try:
            ass_text = ass_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            ass_text = ""
            failures.append({"code": "ass_not_utf8"})
        ass_count = sum(
            1
            for line in ass_text.splitlines()
            if line.startswith("Dialogue:")
        )
        if ass_count != len(events):
            failures.append(
                {
                    "code": "ass_event_count_mismatch",
                    "expected": len(events),
                    "actual": ass_count,
                }
            )
        empty_dialogue_count = sum(
            1
            for line in ass_text.splitlines()
            if line.startswith("Dialogue:")
            and (
                len(line.split(",", 9)) < 10
                or not line.split(",", 9)[9].strip()
            )
        )
        if empty_dialogue_count:
            failures.append(
                {
                    "code": "ass_empty_text",
                    "actual": empty_dialogue_count,
                }
            )
        if ass_text.replace("\r\n", "\n") != render_ass(
            events,
            manifest["concert_id"],
        ):
            failures.append({"code": "ass_content_mismatch"})

    failures.extend(
        {"code": code} for code in validate_song_progress_ass(concert_dir)
    )

    result = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": VERSION,
        "concert_id": manifest["concert_id"],
        "checked_at": utc_now(),
        "result": "failed" if failures else "passed",
        "section_count": len(manifest["sections"]),
        "event_count": len(events),
        "failures": failures,
    }
    write_json(concert_dir / "run" / "concert_gate_report.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="独立验证全场字幕")
    parser.add_argument("--concert-dir", required=True, type=Path)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    console_version("validate_concert")
    result = validate_concert(args.concert_dir)
    print(f"result={result['result']}")
    print(f"sections={result['section_count']}")
    print(f"events={result['event_count']}")
    print(f"failures={len(result['failures'])}")
    return 2 if result["result"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
from typing import Any

from common import SCHEMA_VERSION, VERSION, event_source_text, load_events, write_json
from subtitle_core import BoundaryPolicy, TimedEvent, load_transcript, propose_boundaries


def _default_transcript_path(section_dir: Path) -> Path:
    concert_dir = section_dir.resolve().parents[2]
    return (
        concert_dir
        / "run"
        / "evidence"
        / "transcripts"
        / f"{section_dir.name}.formal.transcript.json"
    )


def build_report(
    *,
    section_dir: Path,
    transcript_path: Path,
    policy: BoundaryPolicy,
) -> dict[str, Any]:
    source_events = load_events(section_dir / "events.json")
    lyric_events = [
        TimedEvent(
            event_id=str(event["id"]),
            start=float(event["start"]),
            end=float(event["end"]),
            text=event_source_text(event),
        )
        for event in source_events
        if event.get("role", "lyric") != "speech"
    ]
    transcript = load_transcript(transcript_path)
    proposals = propose_boundaries(lyric_events, transcript, policy=policy)
    suggested = [
        proposal
        for proposal in proposals
        if proposal.status in {"suggested", "suggested_partial"}
    ]
    conflicts = [
        proposal
        for proposal in proposals
        if proposal.would_overlap
    ]
    rejected = [
        proposal
        for proposal in proposals
        if proposal.rejected_reason is not None
    ]
    matched = [proposal for proposal in proposals if proposal.score is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": VERSION,
        "section_id": section_dir.name,
        "transcript_path": str(transcript_path.resolve()),
        "event_count": len(lyric_events),
        "matched_count": len(matched),
        "suggested_count": len(suggested),
        "conflict_count": len(conflicts),
        "rejected_count": len(rejected),
        "unchanged_count": len(proposals) - len(suggested),
        "text_preserved": True,
        "event_count_preserved": True,
        "applied": False,
        "requires_review": bool(suggested),
        "policy": asdict(policy),
        "proposals": [asdict(proposal) for proposal in proposals],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate review-only lyric boundary proposals from word-timed ASR"
    )
    parser.add_argument("--section-dir", required=True, type=Path)
    parser.add_argument("--transcript-json", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--search-margin", type=float, default=2.0)
    parser.add_argument("--min-score", type=float, default=0.55)
    parser.add_argument("--start-offset", type=float, default=0.0)
    parser.add_argument("--end-offset", type=float, default=0.0)
    parser.add_argument("--min-adjustment", type=float, default=0.05)
    parser.add_argument("--max-boundary-adjustment", type=float, default=0.75)
    parser.add_argument("--minimum-edge-chars", type=int, default=2)
    parser.add_argument("--strong-edge-chars", type=int, default=4)
    parser.add_argument("--strong-edge-probability", type=float, default=0.75)
    parser.add_argument("--handoff-gap", type=float, default=0.02)
    parser.add_argument("--version", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.version:
        print(f"suggest_lyric_boundaries.py v{VERSION}")
        return 0
    section_dir = args.section_dir.resolve()
    transcript_path = (
        args.transcript_json.resolve()
        if args.transcript_json
        else _default_transcript_path(section_dir)
    )
    if not transcript_path.is_file():
        raise FileNotFoundError(f"找不到词级ASR：{transcript_path}")
    output_path = (
        args.output_json.resolve()
        if args.output_json
        else section_dir / "evidence" / "lyric_boundary_proposals.json"
    )
    report = build_report(
        section_dir=section_dir,
        transcript_path=transcript_path,
        policy=BoundaryPolicy(
            search_margin=args.search_margin,
            min_score=args.min_score,
            start_offset=args.start_offset,
            end_offset=args.end_offset,
            min_adjustment=args.min_adjustment,
            max_boundary_adjustment=args.max_boundary_adjustment,
            minimum_edge_chars=args.minimum_edge_chars,
            strong_edge_chars=args.strong_edge_chars,
            strong_edge_probability=args.strong_edge_probability,
            handoff_gap=args.handoff_gap,
        ),
    )
    write_json(output_path, report)
    print(
        f"{report['section_id']}: events={report['event_count']} "
        f"matched={report['matched_count']} "
        f"suggested={report['suggested_count']} "
        f"conflicts={report['conflict_count']} applied=false"
        f" rejected={report['rejected_count']}"
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

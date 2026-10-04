from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
from typing import Any

from common import VERSION, write_json, write_text
from subtitle_core import align_events, compute_metrics
from subtitle_core.reference import load_subtitle_events


def build_report(
    *,
    hypothesis_path: Path,
    reference_path: Path,
    hypothesis_line: str,
    reference_line: str,
    sub_frame_rate: float | None,
) -> dict[str, Any]:
    hypothesis = load_subtitle_events(hypothesis_path, hypothesis_line)
    reference = load_subtitle_events(reference_path, reference_line)
    alignment = align_events(hypothesis, reference)
    metrics = compute_metrics(
        alignment,
        hypothesis_events=len(hypothesis),
        reference_events=len(reference),
        sub_frame_rate=sub_frame_rate,
    )
    return {
        "schema": "concert.subtitle-timing-eval.v1",
        "tool_version": VERSION,
        "hypothesis_path": str(hypothesis_path.resolve()),
        "reference_path": str(reference_path.resolve()),
        "hypothesis_line": hypothesis_line,
        "reference_line": reference_line,
        "sub_frame_rate": sub_frame_rate,
        "metrics": asdict(metrics),
        "alignment": {
            "pairs": [asdict(pair) for pair in alignment.pairs],
            "unmatched_hypothesis": list(alignment.unmatched_hypothesis),
            "unmatched_reference": list(alignment.unmatched_reference),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    start = metrics["start"]
    end = metrics["end"]
    return (
        "# Subtitle timing evaluation\n\n"
        f"- Tool version: `{report['tool_version']}`\n"
        f"- Coverage: `{metrics['coverage']:.1%}`\n"
        f"- Matched pairs: `{metrics['matched_pairs']}`\n"
        f"- Split pairs: `{metrics['split_pairs']}`\n"
        f"- Merge pairs: `{metrics['merge_pairs']}`\n"
        f"- Start error median / p90 / max: "
        f"`{start['median']:.3f}s / {start['p90']:.3f}s / "
        f"{start['maximum']:.3f}s`\n"
        f"- End error median / p90 / max: "
        f"`{end['median']:.3f}s / {end['p90']:.3f}s / "
        f"{end['maximum']:.3f}s`\n"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare machine subtitle timing against a reviewed reference"
    )
    parser.add_argument("--hypothesis", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--report-json", required=True, type=Path)
    parser.add_argument("--report-md", type=Path)
    parser.add_argument(
        "--hypothesis-line",
        choices=("all", "first", "last"),
        default="last",
    )
    parser.add_argument(
        "--reference-line",
        choices=("all", "first", "last"),
        default="last",
    )
    parser.add_argument("--sub-frame-rate", type=float)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = build_report(
        hypothesis_path=args.hypothesis.resolve(),
        reference_path=args.reference.resolve(),
        hypothesis_line=args.hypothesis_line,
        reference_line=args.reference_line,
        sub_frame_rate=args.sub_frame_rate,
    )
    write_json(args.report_json.resolve(), report)
    if args.report_md:
        write_text(args.report_md.resolve(), render_markdown(report))
    metrics = report["metrics"]
    print(
        f"coverage={metrics['coverage']:.1%} "
        f"start_p90={metrics['start']['p90']:.3f}s "
        f"end_p90={metrics['end']['p90']:.3f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

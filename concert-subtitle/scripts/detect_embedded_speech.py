from __future__ import annotations

import argparse
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from common import (
    SCHEMA_VERSION,
    VERSION,
    event_source_text,
    load_events,
    load_manifest,
    normalize_text,
    read_json,
    section_directory,
    utc_now,
    write_json,
)
from subtitle_core import load_transcript

MIN_SEGMENT_SECONDS = 0.45
MIN_TEXT_LENGTH = 4
MAX_LYRIC_SIMILARITY = 0.48
MAX_LYRIC_OVERLAP_RATIO = 0.2
EDGE_MARGIN_SECONDS = 0.25
GAP_MARGIN_SECONDS = 0.15


def _overlap_seconds(
    start: float,
    end: float,
    other_start: float,
    other_end: float,
) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def _mean_word_probability(segment: dict[str, Any]) -> float | None:
    probabilities = []
    for word in segment.get("words") or []:
        value = word.get("probability")
        if value is None:
            continue
        try:
            probabilities.append(float(value))
        except (TypeError, ValueError):
            continue
    if not probabilities:
        return None
    return sum(probabilities) / len(probabilities)


def _similarity(left: str, right: str) -> float:
    normalized_left = normalize_text(left)
    normalized_right = normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left in normalized_right or normalized_right in normalized_left:
        shorter = min(len(normalized_left), len(normalized_right))
        longer = max(len(normalized_left), len(normalized_right))
        return shorter / max(1, longer)
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def _instrumental_gap(
    start: float,
    end: float,
    gaps: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for gap in gaps:
        gap_start = float(gap["start"])
        gap_end = float(gap["end"])
        if (
            start >= gap_start + GAP_MARGIN_SECONDS
            and end <= gap_end - GAP_MARGIN_SECONDS
        ):
            return gap
    return None


def detect_candidates(
    transcript_segments: list[dict[str, Any]],
    lyric_events: list[dict[str, Any]],
    instrumental_gaps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not lyric_events:
        return []
    first_lyric_start = min(float(event["start"]) for event in lyric_events)
    last_lyric_end = max(float(event["end"]) for event in lyric_events)
    candidates = []
    for segment in transcript_segments:
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(segment.get("text", "") or "").strip()
        normalized = normalize_text(text)
        if (
            end - start < MIN_SEGMENT_SECONDS
            or len(normalized) < MIN_TEXT_LENGTH
        ):
            continue

        duration = end - start
        lyric_overlap = sum(
            _overlap_seconds(
                start,
                end,
                float(event["start"]),
                float(event["end"]),
            )
            for event in lyric_events
        )
        overlap_ratio = min(1.0, lyric_overlap / duration)
        nearby_lyrics = [
            event
            for event in lyric_events
            if float(event["end"]) >= start - 8.0
            and float(event["start"]) <= end + 8.0
        ]
        similarity = max(
            (
                _similarity(text, event_source_text(event))
                for event in nearby_lyrics
            ),
            default=0.0,
        )
        gap = _instrumental_gap(start, end, instrumental_gaps)
        location = None
        if end <= first_lyric_start - EDGE_MARGIN_SECONDS:
            location = "prelude"
        elif start >= last_lyric_end + EDGE_MARGIN_SECONDS:
            location = "coda"
        elif gap is not None:
            location = "instrumental_gap"
        elif overlap_ratio <= MAX_LYRIC_OVERLAP_RATIO:
            location = "uncovered_gap"
        if location is None or similarity >= MAX_LYRIC_SIMILARITY:
            continue

        probability = _mean_word_probability(segment)
        confidence = 0.72
        if location in {"prelude", "coda", "instrumental_gap"}:
            confidence += 0.12
        if probability is not None:
            confidence += max(-0.12, min(0.1, (probability - 0.65) * 0.4))
        confidence += max(0.0, MAX_LYRIC_SIMILARITY - similarity) * 0.08
        confidence = max(0.0, min(0.99, confidence))
        candidates.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "source_text": text,
                "translation": "",
                "role": "speech",
                "location": location,
                "confidence": round(confidence, 3),
                "lyric_overlap_ratio": round(overlap_ratio, 3),
                "max_nearby_lyric_similarity": round(similarity, 3),
                "mean_word_probability": (
                    round(probability, 3) if probability is not None else None
                ),
                "evidence": ["asr_embedded_speech_candidate"],
                "status": "needs_transcription_review",
            }
        )
    return candidates


def scan_section(concert_dir: Path, section_id: str) -> dict[str, Any]:
    section_dir = section_directory(concert_dir, section_id)
    events = load_events(section_dir / "events.json")
    lyric_events = [
        event for event in events if event.get("role", "lyric") != "speech"
    ]
    report_path = section_dir / "report.json"
    report = read_json(report_path) if report_path.is_file() else {}
    transcript_path = (
        concert_dir
        / "run"
        / "evidence"
        / "transcripts"
        / f"{section_id}.formal.transcript.json"
    )
    if not transcript_path.is_file():
        raise FileNotFoundError(f"缺少正式ASR转录：{transcript_path}")
    transcript_segments = [
        segment.to_mapping() for segment in load_transcript(transcript_path)
    ]
    candidates = detect_candidates(
        transcript_segments,
        lyric_events,
        report.get("instrumental_gaps") or [],
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": VERSION,
        "section_id": section_id,
        "created_at": utc_now(),
        "transcript_path": str(transcript_path),
        "candidate_count": len(candidates),
        "candidates": [
            {"id": f"{section_id}_speech_candidate_{index:04d}", **candidate}
            for index, candidate in enumerate(candidates, start=1)
        ],
    }
    write_json(section_dir / "speech_candidates.json", payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="用正式ASR证据检测歌曲分段中的歌词外讲话候选"
    )
    parser.add_argument("--concert-dir", required=True, type=Path)
    parser.add_argument("--section", action="append")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    concert_dir = args.concert_dir.resolve()
    manifest = load_manifest(concert_dir)
    selected = set(args.section or [])
    for section in manifest["sections"]:
        if section["type"] != "song":
            continue
        if selected and section["id"] not in selected:
            continue
        result = scan_section(concert_dir, section["id"])
        print(
            f"{section['id']}\tcandidates={result['candidate_count']}\t"
            f"output={section_directory(concert_dir, section['id']) / 'speech_candidates.json'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

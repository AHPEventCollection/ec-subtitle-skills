from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TRANSCRIPT_SCHEMA_V1 = "subsup.transcript.v1"
TRANSCRIPT_SCHEMA_V2 = "subsup.transcript.v2"


@dataclass(frozen=True)
class TranscriptWord:
    start: float
    end: float
    text: str
    probability: float | None = None

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }
        if self.probability is not None:
            payload["probability"] = self.probability
        return payload


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: tuple[TranscriptWord, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }
        if self.words:
            payload["words"] = [word.to_mapping() for word in self.words]
        return payload


def _coerce_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _segment_from_mapping(item: dict[str, Any]) -> TranscriptSegment | None:
    start = _coerce_float(item.get("start"))
    end = _coerce_float(item.get("end"))
    text = str(item.get("text", "")).strip()
    if start is None or end is None or end <= start or not text:
        return None
    words: list[TranscriptWord] = []
    for raw_word in item.get("words", []):
        if not isinstance(raw_word, dict):
            continue
        word_start = _coerce_float(raw_word.get("start"))
        word_end = _coerce_float(raw_word.get("end"))
        word_text = str(raw_word.get("text", "")).strip()
        if (
            word_start is None
            or word_end is None
            or word_end <= word_start
            or not word_text
        ):
            continue
        words.append(
            TranscriptWord(
                start=word_start,
                end=word_end,
                text=word_text,
                probability=_coerce_float(raw_word.get("probability")),
            )
        )
    return TranscriptSegment(start=start, end=end, text=text, words=tuple(words))


def normalize_transcript_payload(payload: Any) -> list[TranscriptSegment]:
    if isinstance(payload, dict):
        for key in ("segments", "utterances"):
            if isinstance(payload.get(key), list):
                return normalize_transcript_payload(payload[key])
        if {"start", "end", "text"}.issubset(payload):
            segment = _segment_from_mapping(payload)
            return [segment] if segment else []
        return []
    if not isinstance(payload, list):
        return []
    segments = [
        segment
        for item in payload
        if isinstance(item, dict)
        if (segment := _segment_from_mapping(item)) is not None
    ]
    return sorted(segments, key=lambda item: (item.start, item.end, item.text))


def load_transcript(path: Path) -> list[TranscriptSegment]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return normalize_transcript_payload(payload)


def write_transcript(segments: list[TranscriptSegment], path: Path) -> None:
    has_words = any(segment.words for segment in segments)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": TRANSCRIPT_SCHEMA_V2 if has_words else TRANSCRIPT_SCHEMA_V1,
                "segments": [segment.to_mapping() for segment in segments],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path
from typing import Any

from common import (
    SCHEMA_VERSION,
    VERSION,
    console_version,
    ensure_audio_path,
    file_fingerprint,
    manifest_path,
    portable_path,
    read_json,
    utc_now,
    validate_filename_component,
    write_json,
)
from lyrics_source import fetch_one

_SECTION_ID = re.compile(r"^[a-z0-9_-]+$")


def _duration_from_preflight(concert_dir: Path) -> float | None:
    path = concert_dir / "run" / "evidence" / "preflight.json"
    if not path.is_file():
        return None
    return float(read_json(path)["duration_seconds"])


def _resolve_input_path(value: str | None, sections_file: Path) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = sections_file.parent / path
    return path.resolve()


def _resolve_missing_song_lyrics(
    sections: list[dict[str, Any]],
    concert_dir: Path,
    cookie_file: Path | None,
) -> None:
    output_dir = concert_dir / "input" / "known-lyrics"
    for section in sections:
        if section["type"] != "song":
            continue
        if section.get("lyrics_path"):
            section["lyrics_status"] = "provided"
            section["translation_status"] = (
                "provided" if section.get("translation_path") else "not_found"
            )
            continue

        result = fetch_one(
            section.get("title", section["id"]),
            section.get("artist", ""),
            output_dir,
            cookie_file=cookie_file,
        )
        lyrics_file = result.get("file")
        translation_file = result.get("translation_file")
        section.update(
            {
                "lyrics_path": (
                    portable_path(Path(lyrics_file), concert_dir)
                    if lyrics_file
                    else None
                ),
                "lyrics_source": result.get("source"),
                "lyrics_status": result.get("status", "not_found"),
                "translation_path": (
                    section.get("translation_path")
                    or (
                        portable_path(Path(translation_file), concert_dir)
                        if translation_file
                        else None
                    )
                ),
                "translation_source": (
                    section.get("translation_source")
                    or result.get("translation_source")
                ),
                "translation_status": (
                    "provided"
                    if section.get("translation_path")
                    else result.get("translation_status", "not_found")
                ),
                "lyrics_lookup": {
                    "reason": "missing_lyrics_path",
                    "status": result.get("status", "not_found"),
                    "source": result.get("source"),
                    "translation_status": result.get(
                        "translation_status",
                        "not_found",
                    ),
                    "translation_source": result.get("translation_source"),
                    "metadata": result.get("metadata", {}),
                },
            }
        )


def normalize_sections(
    raw_sections: list[dict[str, Any]],
    duration: float,
    context_seconds: float,
    sections_file: Path,
    concert_dir: Path,
) -> list[dict[str, Any]]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("演唱会总时长必须是有限正数")
    normalized: list[dict[str, Any]] = []
    counters = {"song": 0, "mc": 0}
    for index, raw in enumerate(raw_sections, start=1):
        section_type = str(raw.get("type", "")).lower()
        if section_type not in counters:
            raise ValueError(f"第{index}段type必须是song或mc")
        counters[section_type] += 1
        section_id = raw.get("id") or f"{section_type}_{counters[section_type]:02d}"
        if not _SECTION_ID.fullmatch(section_id):
            raise ValueError(f"非法分段ID：{section_id}")

        start = float(raw["start"])
        end = float(raw["end"])
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
            or end > duration + 0.1
        ):
            raise ValueError(f"{section_id}区间非法：{start}-{end}，总时长{duration}")

        lyrics_path = _resolve_input_path(raw.get("lyrics_path"), sections_file)
        translation_path = _resolve_input_path(
            raw.get("translation_path"),
            sections_file,
        )
        if lyrics_path and not lyrics_path.is_file():
            raise FileNotFoundError(f"{section_id}歌词文件不存在：{lyrics_path}")
        if translation_path and not translation_path.is_file():
            raise FileNotFoundError(
                f"{section_id}中文歌词文件不存在：{translation_path}"
            )
        context_start = max(
            0.0,
            float(raw.get("context_start", start - context_seconds)),
        )
        context_end = min(
            duration,
            float(raw.get("context_end", end + context_seconds)),
        )
        if (
            not math.isfinite(context_start)
            or not math.isfinite(context_end)
            or context_start > start
            or context_end < end
        ):
            raise ValueError(f"{section_id}上下文区间必须完整包含正式区间")
        confidence = float(raw.get("boundary_confidence", 0.5))
        if not math.isfinite(confidence) or confidence < 0 or confidence > 1:
            raise ValueError(f"{section_id}的boundary_confidence必须在0至1之间")
        review_priority = str(raw.get("review_priority", "normal"))
        if review_priority not in {"normal", "high"}:
            raise ValueError(f"{section_id}的review_priority必须是normal或high")

        normalized.append(
            {
                "id": section_id,
                "order": index,
                "type": section_type,
                "title": str(raw.get("title", section_id)),
                "artist": str(raw.get("artist", "")),
                "start": start,
                "end": end,
                "context_start": context_start,
                "context_end": context_end,
                "boundary_source": str(raw.get("boundary_source", "provided")),
                "boundary_confidence": confidence,
                "lyrics_path": (
                    portable_path(lyrics_path, concert_dir) if lyrics_path else None
                ),
                "lyrics_source": raw.get("lyrics_source"),
                "lyrics_status": "provided" if lyrics_path else None,
                "translation_path": (
                    portable_path(translation_path, concert_dir)
                    if translation_path
                    else None
                ),
                "translation_source": raw.get("translation_source"),
                "translation_status": "provided" if translation_path else None,
                "gpu_required": bool(raw.get("gpu_required", True)),
                "review_priority": review_priority,
                "status": "planned",
            }
        )

        if "source_song_title" in raw:
            if type(raw["source_song_title"]) is not bool:
                raise ValueError("source_song_title必须为布尔值")
            normalized[-1]["source_song_title"] = raw["source_song_title"]

    ids = [section["id"] for section in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("分段ID重复")
    for previous, current in zip(normalized, normalized[1:]):
        if current["start"] < previous["end"] - 0.001:
            raise ValueError(
                f"正式区间重叠：{previous['id']}与{current['id']}"
            )
    return normalized


def create_manifest(
    concert_dir: Path,
    source: Path,
    audio: Path,
    sections_file: Path,
    concert_id: str | None = None,
    duration: float | None = None,
    context_seconds: float = 20.0,
    overwrite: bool = False,
    setlist: Path | None = None,
    netease_cookie_file: Path | None = None,
) -> dict[str, Any]:
    concert_dir = concert_dir.resolve()
    source = source.resolve()
    audio = ensure_audio_path(audio)
    sections_file = sections_file.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"源媒体不存在：{source}")
    if not sections_file.is_file():
        raise FileNotFoundError(f"区间JSON不存在：{sections_file}")
    if setlist and not setlist.resolve().is_file():
        raise FileNotFoundError(f"歌单文件不存在：{setlist.resolve()}")
    if netease_cookie_file and not netease_cookie_file.resolve().is_file():
        raise FileNotFoundError(
            f"网易Cookie文件不存在：{netease_cookie_file.resolve()}"
        )

    output_path = manifest_path(concert_dir)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Manifest已存在，使用--overwrite覆盖：{output_path}")

    actual_duration = duration or _duration_from_preflight(concert_dir)
    if not actual_duration:
        raise ValueError("缺少总时长，请先运行预检或传入--duration")

    raw = read_json(sections_file)
    raw_sections = raw.get("sections") if isinstance(raw, dict) else raw
    if not isinstance(raw_sections, list) or not raw_sections:
        raise ValueError("区间JSON必须是非空数组或包含sections数组")
    sections = normalize_sections(
        raw_sections,
        float(actual_duration),
        context_seconds,
        sections_file,
        concert_dir,
    )
    _resolve_missing_song_lyrics(
        sections,
        concert_dir,
        netease_cookie_file.resolve() if netease_cookie_file else None,
    )

    preflight_path = concert_dir / "run" / "evidence" / "preflight.json"
    preflight = read_json(preflight_path) if preflight_path.is_file() else {}
    safe_concert_id = validate_filename_component(
        concert_id or concert_dir.name,
        "concert_id",
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": VERSION,
        "concert_id": safe_concert_id,
        "created_at": utc_now(),
        "source_media": str(source),
        "source_fingerprint": preflight.get("source_fingerprint", file_fingerprint(source)),
        "review_media": portable_path(
            Path(preflight.get("review_media", source)),
            concert_dir,
        ),
        "review_media_kind": preflight.get("review_media_kind", "source"),
        "review_media_fingerprint": preflight.get(
            "review_media_fingerprint",
            file_fingerprint(source),
        ),
        "audio_path": portable_path(audio, concert_dir),
        "setlist_path": (
            portable_path(setlist.resolve(), concert_dir)
            if setlist
            else None
        ),
        "duration_seconds": float(actual_duration),
        "coverage_policy": "full_timeline_asr",
        "sections": sections,
    }
    write_json(output_path, manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="建立演唱会分段Manifest")
    parser.add_argument("--concert-dir", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--sections", required=True, type=Path)
    parser.add_argument("--concert-id")
    parser.add_argument("--setlist", type=Path)
    parser.add_argument("--netease-cookie-file", type=Path)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--context-seconds", type=float, default=20.0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    console_version("index_concert")
    manifest = create_manifest(
        args.concert_dir,
        args.source,
        args.audio,
        args.sections,
        args.concert_id,
        args.duration,
        args.context_seconds,
        args.overwrite,
        args.setlist,
        args.netease_cookie_file,
    )
    print(f"manifest={manifest_path(args.concert_dir)}")
    print(f"sections={len(manifest['sections'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

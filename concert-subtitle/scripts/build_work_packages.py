from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path
from typing import Any

from common import (
    SCHEMA_VERSION,
    VERSION,
    archive_section_results,
    canonical_hash,
    console_version,
    ensure_audio_path,
    file_fingerprint,
    file_sha256,
    load_manifest,
    manifest_section_contract,
    read_json,
    resolve_binary,
    resolve_stored_path,
    section_directory,
    update_status,
    write_json,
)


def _copy_lyrics(
    section: dict[str, Any],
    section_dir: Path,
    concert_dir: Path,
) -> tuple[str | None, str | None]:
    source = resolve_stored_path(section.get("lyrics_path"), concert_dir)
    if not source:
        return None, None
    if not source.is_file():
        return None, None
    extension = source.suffix.lower() if source.suffix else ".txt"
    destination = section_dir / f"lyrics{extension}"
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
    return destination.name, file_sha256(destination)


def _copy_translation(
    section: dict[str, Any],
    section_dir: Path,
    concert_dir: Path,
) -> tuple[str | None, str | None]:
    source = resolve_stored_path(section.get("translation_path"), concert_dir)
    if not source or not source.is_file():
        return None, None
    extension = source.suffix.lower() if source.suffix else ".txt"
    destination = section_dir / f"translation{extension}"
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
    return destination.name, file_sha256(destination)


def _extract_audio(
    ffmpeg: Path,
    source_audio: Path,
    destination: Path,
    start: float,
    end: float,
) -> None:
    command = [
        str(ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{end - start:.3f}",
        "-i",
        str(source_audio),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-af",
        "asetpts=PTS-STARTPTS",
        "-c:a",
        "flac",
        str(destination),
    ]
    subprocess.run(command, check=True)


def build_packages(
    concert_dir: Path,
    section_ids: set[str] | None = None,
    ffmpeg_value: str | None = None,
    no_audio: bool = False,
    force: bool = False,
) -> list[dict[str, Any]]:
    concert_dir = concert_dir.resolve()
    manifest = load_manifest(concert_dir)
    source_audio = ensure_audio_path(
        resolve_stored_path(manifest["audio_path"], concert_dir)
    )
    ffmpeg = None if no_audio else resolve_binary("ffmpeg", ffmpeg_value)
    summaries = []

    all_section_ids = {section["id"] for section in manifest["sections"]}
    if section_ids:
        missing = section_ids - all_section_ids
        if missing:
            raise ValueError(f"Manifest中不存在分段：{', '.join(sorted(missing))}")
        selected_indexes = {
            index
            for index, section in enumerate(manifest["sections"])
            if section["id"] in section_ids
        }
        expanded_indexes = set(selected_indexes)
        for index in selected_indexes:
            if index > 0:
                expanded_indexes.add(index - 1)
            if index + 1 < len(manifest["sections"]):
                expanded_indexes.add(index + 1)
    else:
        expanded_indexes = set(range(len(manifest["sections"])))

    for section_index, section in enumerate(manifest["sections"]):
        if section_index not in expanded_indexes:
            continue
        section_dir = section_directory(concert_dir, section["id"])
        section_dir.mkdir(parents=True, exist_ok=True)
        (section_dir / "evidence").mkdir(exist_ok=True)
        lyrics_file, lyrics_hash = _copy_lyrics(section, section_dir, concert_dir)
        translation_file, translation_hash = _copy_translation(
            section,
            section_dir,
            concert_dir,
        )
        audio_destination = section_dir / "audio.flac"

        package_without_hash = {
            "schema_version": SCHEMA_VERSION,
            "tool_version": VERSION,
            "concert_id": manifest["concert_id"],
            "section_id": section["id"],
            "section_type": section["type"],
            "order": section["order"],
            "title": section.get("title", ""),
            "artist": section.get("artist", ""),
            "section_start": float(section["start"]),
            "section_end": float(section["end"]),
            "context_start": float(section["context_start"]),
            "context_end": float(section["context_end"]),
            "timeline_origin": float(section["start"]),
            "audio_context_origin": float(section["context_start"]),
            "audio_path": "audio.flac",
            "lyrics_file": lyrics_file,
            "lyrics_source": section.get("lyrics_source"),
            "lyrics_status": section.get("lyrics_status"),
            "lyrics_lookup": section.get("lyrics_lookup"),
            "lyrics_hash": lyrics_hash,
            "translation_file": translation_file,
            "translation_source": section.get("translation_source"),
            "translation_status": section.get("translation_status"),
            "translation_hash": translation_hash,
            "boundary_source": section.get("boundary_source", ""),
            "boundary_confidence": float(section.get("boundary_confidence", 0.5)),
            "review_priority": section.get("review_priority", "normal"),
            "gpu_required": bool(section.get("gpu_required", True)),
            "manifest_section_hash": canonical_hash(
                manifest_section_contract(manifest, section_index)
            ),
            "source_audio_fingerprint": file_fingerprint(source_audio),
        }
        input_hash = canonical_hash(package_without_hash)
        package = {**package_without_hash, "input_hash": input_hash}
        input_path = section_dir / "input.json"
        old_package = read_json(input_path) if input_path.is_file() else None
        old_hash = old_package.get("input_hash") if old_package else None
        status_path = section_dir / "status.json"
        current_status = read_json(status_path).get("status") if status_path.is_file() else None

        if (
            old_hash == input_hash
            and current_status == "approved"
            and audio_destination.is_file()
            and not force
        ):
            summaries.append({"section_id": section["id"], "action": "kept_approved"})
            continue
        if (old_hash and old_hash != input_hash) or force:
            archive_section_results(
                section_dir,
                "input-changed" if old_hash != input_hash else "forced-rebuild",
                [
                    concert_dir / "run" / "review" / f"{section['id']}.ass",
                    concert_dir / "run" / "review" / f"{section['id']}.mp4",
                    concert_dir / "run" / "review" / section["id"],
                ],
            )
            update_status(
                section_dir,
                "stale",
                "input_hash_changed" if old_hash != input_hash else "forced_rebuild",
                section_id=section["id"],
                input_hash=input_hash,
            )

        if not no_audio:
            _extract_audio(
                ffmpeg,
                source_audio,
                audio_destination,
                float(section["context_start"]),
                float(section["context_end"]),
            )
        write_json(input_path, package)
        update_status(
            section_dir,
            "prepared",
            "work_package_ready",
            section_id=section["id"],
            input_hash=input_hash,
        )
        summaries.append({"section_id": section["id"], "action": "prepared"})
    return summaries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成歌曲和MC独立工作包")
    parser.add_argument("--concert-dir", required=True, type=Path)
    parser.add_argument("--section", action="append", dest="sections")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--no-audio", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    console_version("build_work_packages")
    summaries = build_packages(
        args.concert_dir,
        set(args.sections or []),
        args.ffmpeg,
        args.no_audio,
        args.force,
    )
    for summary in summaries:
        print(f"{summary['section_id']}: {summary['action']}")
    print(f"processed={len(summaries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

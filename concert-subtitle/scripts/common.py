from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

VERSION = "0.15.3"
SCHEMA_VERSION = "0.1.0"
SOURCE_MAX_DISPLAY_UNITS = 56
TRANSLATION_MAX_DISPLAY_UNITS = 44
SOURCE_RENDER_MAX_DISPLAY_UNITS = 56
TRANSLATION_RENDER_MAX_DISPLAY_UNITS = 38
STATUSES = {
    "planned",
    "prepared",
    "queued",
    "running",
    "needs_review",
    "approved",
    "failed",
    "stale",
}
AUDIO_EXTENSIONS = {".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".aiff", ".aif"}

_LRC_TIMESTAMP = re.compile(r"^(?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\])+")
_LRC_METADATA = re.compile(r"^\[(?:ar|al|ti|by|offset|re|ve|length):", re.IGNORECASE)
_LYRIC_METADATA_TEXT = re.compile(
    r"^\s*(?:(?:作词|作詞|作曲|编曲|編曲|翻译|翻譯|歌词|歌詞)"
    r"|(?:lyricist|composer|arranger|lyrics|music))\s*[:：]",
    re.IGNORECASE,
)
_ASS_TAG = re.compile(r"\{[^}]*\}")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def write_text(
    path: Path,
    text: str,
    encoding: str = "utf-8",
    newline: str = "\n",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding=encoding, newline=newline) as handle:
        handle.write(text)
    os.replace(temporary, path)


def canonical_hash(data: Any) -> str:
    payload = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def find_project_root(start: Path | None = None) -> Path:
    configured = os.environ.get("CONCERT_SUBTITLE_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()

    current = (start or Path.cwd()).absolute()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "tool").exists() and (candidate / "workspace").exists():
            return candidate
        if candidate.name == ".agents":
            return candidate.parent
    return Path.cwd().absolute()


def resolve_binary(
    name: str,
    explicit: str | None = None,
    project_root: Path | None = None,
) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        raise FileNotFoundError(f"{name}不存在：{candidate}")

    environment_name = f"CONCERT_SUBTITLE_{name.upper()}"
    environment_value = os.environ.get(environment_name)
    if environment_value:
        candidate = Path(environment_value).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        raise FileNotFoundError(f"{environment_name}指向不存在的文件：{candidate}")

    root = project_root or find_project_root()
    executable = f"{name}.exe" if os.name == "nt" else name
    candidates = [
        root / "tool" / "ffmpeg" / "bin" / executable,
        root / "tool" / "bin" / executable,
        root / "tool" / executable,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    from_path = shutil.which(name)
    if from_path:
        return Path(from_path).resolve()
    raise FileNotFoundError(
        f"找不到{name}，请放入项目tool目录、系统PATH，"
        f"或设置{environment_name}"
    )


def ensure_audio_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"音频不存在：{resolved}")
    if resolved.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(
            f"生产输入必须是独立音频，不能使用{resolved.suffix or '无扩展名'}：{resolved}"
        )
    return resolved


def portable_path(path: Path, base: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(base.resolve()))
    except ValueError:
        return str(resolved)


def resolve_stored_path(value: str | None, base: Path) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (base / path).resolve()


def resolve_review_media(manifest: dict[str, Any], concert_dir: Path) -> Path:
    value = manifest.get("review_media") or manifest.get("source_media")
    path = resolve_stored_path(str(value) if value else None, concert_dir)
    if path is None or not path.is_file():
        raise FileNotFoundError(f"审查媒体不存在：{path}")
    expected = manifest.get("review_media_fingerprint")
    if isinstance(expected, dict):
        actual = file_fingerprint(path)
        for key in ("size", "mtime_ns"):
            if expected.get(key) != actual.get(key):
                raise ValueError(f"审查媒体指纹已变化：{path}")
    return path


def manifest_path(concert_dir: Path) -> Path:
    return concert_dir.resolve() / "run" / "concert_manifest.json"


def validate_filename_component(value: str, field_name: str = "name") -> str:
    candidate = value.strip()
    if not candidate or candidate in {".", ".."}:
        raise ValueError(f"{field_name}不能为空或点路径")
    if len(candidate) > 128:
        raise ValueError(f"{field_name}过长")
    if candidate.endswith((" ", ".")):
        raise ValueError(f"{field_name}不能以空格或点结尾")
    if any(character in '<>:"/\\|?*' or ord(character) < 32 for character in candidate):
        raise ValueError(f"{field_name}包含非法文件名字符")
    if candidate.split(".", 1)[0].lower() in _WINDOWS_RESERVED:
        raise ValueError(f"{field_name}使用了Windows保留名")
    return candidate


def load_manifest(concert_dir: Path) -> dict[str, Any]:
    path = manifest_path(concert_dir)
    if not path.is_file():
        raise FileNotFoundError(f"找不到Manifest：{path}")
    manifest = read_json(path)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Manifest版本不兼容：{manifest.get('schema_version')}，当前{SCHEMA_VERSION}"
        )
    return manifest


def section_directory(concert_dir: Path, section_id: str) -> Path:
    return concert_dir.resolve() / "run" / "sections" / section_id


def manifest_section_contract(
    manifest: dict[str, Any],
    section_index: int,
) -> dict[str, Any]:
    sections = manifest["sections"]
    section = sections[section_index]

    def boundary(item: dict[str, Any] | None) -> dict[str, Any] | None:
        if item is None:
            return None
        return {
            "id": item["id"],
            "order": item["order"],
            "type": item["type"],
            "start": float(item["start"]),
            "end": float(item["end"]),
        }

    contract = {
        "concert_id": manifest["concert_id"],
        "source_media": manifest["source_media"],
        "source_fingerprint": manifest.get("source_fingerprint"),
        "audio_path": manifest["audio_path"],
        "duration_seconds": float(manifest["duration_seconds"]),
        "section": {
            key: section.get(key)
            for key in (
                "id",
                "order",
                "type",
                "title",
                "artist",
                "start",
                "end",
                "context_start",
                "context_end",
                "boundary_source",
                "boundary_confidence",
                "lyrics_path",
                "lyrics_source",
                "lyrics_status",
                "lyrics_lookup",
                "translation_path",
                "translation_source",
                "translation_status",
                "gpu_required",
                "review_priority",
            )
        },
        "previous_boundary": boundary(sections[section_index - 1])
        if section_index > 0
        else None,
        "next_boundary": boundary(sections[section_index + 1])
        if section_index + 1 < len(sections)
        else None,
    }
    if "review_media" in manifest:
        contract.update(
            {
                "review_media": manifest["review_media"],
                "review_media_kind": manifest.get("review_media_kind"),
                "review_media_fingerprint": manifest.get(
                    "review_media_fingerprint"
                ),
            }
        )
    return contract


def package_input_hash(package: dict[str, Any]) -> str:
    return canonical_hash(
        {
            key: value
            for key, value in package.items()
            if key != "input_hash"
        }
    )


def package_manifest_mismatches(
    package: dict[str, Any],
    manifest: dict[str, Any],
    section_index: int,
) -> list[str]:
    section = manifest["sections"][section_index]
    expected = {
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
        "boundary_source": section.get("boundary_source", ""),
        "boundary_confidence": float(section.get("boundary_confidence", 0.5)),
        "review_priority": section.get("review_priority", "normal"),
        "gpu_required": bool(section.get("gpu_required", True)),
    }
    return [
        key
        for key, expected_value in expected.items()
        if package.get(key) != expected_value
    ]


def archive_section_results(
    section_dir: Path,
    reason: str,
    extra_paths: Iterable[Path] = (),
) -> Path | None:
    result_names = (
        "events.json",
        "report.json",
        "gate_report.json",
        "review_decision.json",
        "section.ass",
        "section.srt",
        "review.mp4",
    )
    existing: list[Path] = [
        section_dir / name
        for name in result_names
        if (section_dir / name).exists()
    ]
    existing.extend(path for path in extra_paths if path.exists())
    if not existing:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = (
        section_dir
        / "evidence"
        / "stale-results"
        / f"{stamp}-{reason}"
    )
    counter = 1
    while destination.exists():
        destination = destination.with_name(f"{stamp}-{reason}-{counter}")
        counter += 1
    destination.mkdir(parents=True)
    for source in existing:
        target = destination / source.name
        if target.exists():
            target = destination / f"external-{source.name}"
        shutil.move(str(source), str(target))
    return destination


def update_status(
    section_dir: Path,
    status: str,
    reason: str,
    **extra: Any,
) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"非法状态：{status}")
    path = section_dir / "status.json"
    existing = read_json(path) if path.is_file() else {}
    history = list(existing.get("history", []))
    entry = {
        "status": status,
        "reason": reason,
        "at": utc_now(),
    }
    if extra:
        entry.update(extra)
    history.append(entry)
    updated = {
        **existing,
        "schema_version": SCHEMA_VERSION,
        "section_id": extra.get("section_id", existing.get("section_id", section_dir.name)),
        "status": status,
        "reason": reason,
        "updated_at": entry["at"],
        "history": history,
    }
    for key, value in extra.items():
        if key != "section_id":
            updated[key] = value
    write_json(path, updated)
    return updated


def load_events(path: Path) -> list[dict[str, Any]]:
    data = read_json(path)
    if isinstance(data, list):
        return data
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        raise ValueError(f"events.json必须是数组或包含events数组：{path}")
    return events


def event_source_text(event: dict[str, Any]) -> str:
    value = event.get("source_text", event.get("text", ""))
    return str(value or "")


def normalize_text(value: str) -> str:
    text = _ASS_TAG.sub("", value)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\\N", " ").replace("\u3000", " ")
    return "".join(text.split())


_MC_ELLIPSIS_RUN = re.compile(r"[.．｡]{2,}")
_MC_DISALLOWED_PUNCTUATION = frozenset({",", ".", "，", "。", "、", "．", "｡", "､"})


def normalize_mc_punctuation(value: str) -> str:
    """Remove comma/period punctuation from MC while preserving tone marks."""
    text = _MC_ELLIPSIS_RUN.sub("…", str(value))
    output: list[str] = []
    for index, character in enumerate(text):
        if character not in _MC_DISALLOWED_PUNCTUATION:
            output.append(character)
            continue
        previous_character = text[index - 1] if index else ""
        next_character = text[index + 1] if index + 1 < len(text) else ""
        if (
            character in {".", "．"}
            and previous_character.isdigit()
            and next_character.isdigit()
        ):
            output.append(character)
    return "".join(output)


def mc_has_disallowed_punctuation(value: str) -> bool:
    return normalize_mc_punctuation(value) != str(value)


def load_lyrics(
    path: Path,
    initial_metadata: Iterable[str] | None = None,
) -> list[str]:
    lines: list[str] = []
    metadata = {
        normalize_text(str(value)).casefold()
        for value in (initial_metadata or [])
        if str(value).strip()
    }
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or _LRC_METADATA.match(line):
            continue
        line = _LRC_TIMESTAMP.sub("", line).strip()
        if (
            line
            and not line.startswith("[")
            and not _LYRIC_METADATA_TEXT.match(line)
        ):
            if not lines and normalize_text(line).casefold() in metadata:
                continue
            lines.append(line)
    return lines


def event_display_text(event: dict[str, Any]) -> str:
    source = event_source_text(event).strip()
    translation = str(event.get("translation", "") or "").strip()
    return f"{translation}\n{source}" if translation else source


def display_units(text: str) -> int:
    return sum(
        2
        if unicodedata.east_asian_width(character) in {"W", "F", "A"}
        else 1
        for character in text
        if not character.isspace()
    )


def _wrap_display_line(text: str, max_units: int) -> list[str]:
    remaining = text.strip()
    lines = []
    strong_breaks = "。！？?!；;"
    soft_breaks = "，、,:："
    while display_units(remaining) > max_units:
        used = 0
        hard_index = 0
        preferred_index = 0
        for index, character in enumerate(remaining, start=1):
            character_units = display_units(character)
            if used + character_units > max_units:
                break
            used += character_units
            hard_index = index
            if (
                used >= max_units * 0.55
                and (
                    character.isspace()
                    or character in strong_breaks
                    or character in soft_breaks
                )
            ):
                preferred_index = index
        break_index = preferred_index or hard_index
        if break_index <= 0:
            break_index = 1
        while (
            break_index > 1
            and break_index < len(remaining)
            and remaining[break_index - 1].isascii()
            and remaining[break_index - 1].isalnum()
            and remaining[break_index].isascii()
            and remaining[break_index].isalnum()
        ):
            break_index -= 1
        if break_index <= 0:
            break_index = hard_index
        lines.append(remaining[:break_index].strip())
        remaining = remaining[break_index:].strip()
    if remaining:
        lines.append(remaining)
    return lines


def wrap_display_text(text: str, max_units: int) -> str:
    lines = []
    for source_line in re.split(r"\r?\n", text.strip()):
        if source_line.strip():
            lines.extend(_wrap_display_line(source_line, max_units))
    return "\n".join(lines)


def event_render_text(event: dict[str, Any]) -> str:
    source = wrap_display_text(
        event_source_text(event).strip(),
        SOURCE_RENDER_MAX_DISPLAY_UNITS,
    )
    translation = wrap_display_text(
        str(event.get("translation", "") or "").strip(),
        TRANSLATION_RENDER_MAX_DISPLAY_UNITS,
    )
    return f"{translation}\n{source}" if translation else source


def srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, cents = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cents:02d}"


_REVIEW_PLACEHOLDER = r"(?:(?:前|后|後)半(?:句)?(?:待确认|待確認|要確認)|待确认|待確認|待核对|待核對|要確認|听不清|聽不清|聞き取れない|TBD|TODO)"
_REVIEW_MARKER = re.compile(
    rf"[\[［【（(]\s*{_REVIEW_PLACEHOLDER}\s*[\]］】）)]|^\s*{_REVIEW_PLACEHOLDER}\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def reject_review_placeholder(text: str) -> None:
    """Reject explicit production labels, not ordinary lyric phrases such as 確認中."""
    if _REVIEW_MARKER.search(text):
        raise ValueError("字幕正文含核对占位提示；请核实正文，将制作备注留在工作记录中")


def render_srt(events: Iterable[dict[str, Any]]) -> str:
    blocks = []
    for index, event in enumerate(events, start=1):
        text = event_render_text(event)
        reject_review_placeholder(text)
        blocks.append(
            f"{index}\n"
            f"{srt_timestamp(float(event['start']))} --> "
            f"{srt_timestamp(float(event['end']))}\n"
            f"{text}\n"
        )
    return "\n".join(blocks)


def console_version(script_name: str) -> None:
    print(f"{script_name} v{VERSION}")

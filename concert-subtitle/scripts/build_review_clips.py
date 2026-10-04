from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

from common import (
    VERSION,
    console_version,
    file_sha256,
    load_events,
    load_manifest,
    read_json,
    resolve_binary,
    resolve_review_media,
    section_directory,
    write_json,
)
from merge_concert_subtitles import render_ass
from preview_encoding import PREVIEW_PROFILE, PREVIEW_SCALE, preview_encoding_args


def render_review_video(
    *,
    source: Path,
    output_path: Path,
    start: float,
    end: float,
    ffmpeg: Path,
) -> None:
    if start < 0 or end <= start:
        raise ValueError("审查片段必须使用非负起点和正时长")
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-sn",
        "-dn",
        "-vf",
        f"{PREVIEW_SCALE},setpts=PTS-STARTPTS",
        "-af",
        "asetpts=PTS-STARTPTS",
        *preview_encoding_args(),
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    subprocess.run(command, check=True)


def _window_events(
    events: list[dict[str, Any]],
    section_start: float,
    window_start: float,
    window_end: float,
) -> list[dict[str, Any]]:
    shifted = []
    for event in events:
        absolute_start = section_start + float(event["start"])
        absolute_end = section_start + float(event["end"])
        if absolute_end <= window_start or absolute_start >= window_end:
            continue
        shifted.append(
            {
                **event,
                "start": max(0.0, absolute_start - window_start),
                "end": min(window_end - window_start, absolute_end - window_start),
            }
        )
    return shifted


def _section_review_window(
    section: dict[str, Any],
    events: list[dict[str, Any]],
) -> tuple[str, float, float]:
    return "full_section", float(section["start"]), float(section["end"])

def build_review_clips(
    concert_dir: Path,
    section_ids: set[str] | None = None,
    ffmpeg_value: str | None = None,
    output_dir: Path | None = None,
) -> list[dict[str, Any]]:
    concert_dir = concert_dir.resolve()
    manifest = load_manifest(concert_dir)
    source = resolve_review_media(manifest, concert_dir)
    ffmpeg = resolve_binary("ffmpeg", ffmpeg_value)
    review_root = (output_dir or concert_dir / "待检查").resolve()
    review_root.mkdir(parents=True, exist_ok=True)
    summaries = []

    for section in manifest["sections"]:
        if section_ids and section["id"] not in section_ids:
            continue
        section_dir = section_directory(concert_dir, section["id"])
        events = load_events(section_dir / "events.json")
        label, start, end = _section_review_window(section, events)
        shifted_events = _window_events(
            events,
            float(section["start"]),
            start,
            end,
        )
        subtitle_path = review_root / f"{section['id']}.ass"
        output_path = review_root / f"{section['id']}.mp4"
        receipt_path = concert_dir / "run" / "evidence" / f"review-{section['id']}.json"
        receipt = read_json(receipt_path) if receipt_path.is_file() else {}
        media_contract = {
            "preview_profile": PREVIEW_PROFILE,
            "source": str(source), "size": source.stat().st_size,
            "mtime_ns": source.stat().st_mtime_ns, "start": start, "end": end,
        }
        reuse_video = output_path.is_file() and (
            receipt.get("video") == str(output_path)
            and receipt.get("media_contract") == media_contract
            and receipt.get("video_sha256") == file_sha256(output_path)
        )
        if output_path.exists() and not reuse_video:
            raise FileExistsError(f"已有视频与本轮记录不匹配，保留文件：{output_path}；请指定新的--output-dir")
        if subtitle_path.exists() and (
            receipt.get("subtitle") != str(subtitle_path)
            or receipt.get("subtitle_sha256") != file_sha256(subtitle_path)
        ):
            raise FileExistsError(f"保留人工修改或来源未知的字幕：{subtitle_path}；请先回灌修订或指定新的--output-dir")
        subtitle_path.write_text(
            render_ass(shifted_events, f"{section['id']}-{label}"),
            encoding="utf-8-sig",
            newline="\n",
        )
        if not reuse_video:
            render_review_video(
                source=source,
                output_path=output_path,
                start=start,
                end=end,
                ffmpeg=ffmpeg,
            )
        summaries.append(
            {
                "section_id": section["id"],
                "clip_count": 1,
                "review_point": label,
                "start": start,
                "end": end,
                "video": str(output_path),
                "subtitle": str(subtitle_path),
            }
        )
        if output_path.is_file():
            write_json(receipt_path, {
                **summaries[-1], "source": str(source),
                "media_contract": media_contract,
                "video_sha256": file_sha256(output_path),
                "subtitle_sha256": file_sha256(subtitle_path),
                "events_sha256": file_sha256(section_dir / "events.json"),
            })
    return summaries


def build_source_preview(
    concert_dir: Path, output_dir: Path | None = None, *,
    lightweight_video: bool = False, ffmpeg_value: str | None = None,
) -> Path:
    """Export available events in source time without approval or formal merge writes."""
    concert_dir = concert_dir.resolve()
    manifest = load_manifest(concert_dir)
    source = resolve_review_media(manifest, concert_dir)
    root = (output_dir or concert_dir / "待检查" / ("整场预览" if lightweight_video else "整场候选")).resolve()
    subtitle = root / ("整场预览.ass" if lightweight_video else f"{source.stem}.candidate.ass")
    note = root / "打开方式.txt"
    receipt_path = concert_dir / "run" / "evidence" / ("full-preview.json" if lightweight_video else "source-preview.json")
    receipt = read_json(receipt_path) if receipt_path.is_file() else {}
    if not lightweight_video and (subtitle.exists() or note.exists()):
        raise FileExistsError(f"保留已有整场候选：{root}；新一轮请指定新的--output-dir")
    if lightweight_video and subtitle.exists() and (
        receipt.get("subtitle") != str(subtitle)
        or receipt.get("subtitle_sha256") != file_sha256(subtitle)
    ):
        raise FileExistsError(f"保留人工修改或来源未知的字幕：{subtitle}")
    events = []
    included, missing = [], []
    for section in manifest["sections"]:
        path = section_directory(concert_dir, section["id"]) / "events.json"
        if not path.is_file():
            missing.append(section["id"])
            continue
        local = load_events(path)
        if not local:
            missing.append(section["id"])
            continue
        duration = float(section["end"]) - float(section["start"])
        for event in local:
            if not (0 <= float(event["start"]) < float(event["end"]) <= duration):
                raise ValueError(f"{section['id']}存在非法时间或越界事件")
            events.append({**event,
                           "start": float(section["start"]) + float(event["start"]),
                           "end": float(section["start"]) + float(event["end"])})
        included.append({"section_id": section["id"], "events_sha256": file_sha256(path)})
    if not events:
        raise ValueError("尚无可导出的字幕事件")
    events.sort(key=lambda event: (event["start"], event["end"]))
    caption_text = render_ass(events, "整场候选-未批准")
    if lightweight_video and any(section.get("type") == "song" for section in manifest["sections"]):
        from song_progress import merge_review_ass, render_song_progress_ass
        caption_text = merge_review_ass(caption_text, render_song_progress_ass(manifest))
    root.mkdir(parents=True, exist_ok=True)
    video = source
    video_record = {}
    if lightweight_video:
        video = root / "整场预览.mp4"
        duration = float(manifest["duration_seconds"])
        contract = {"source": str(source), "size": source.stat().st_size,
                    "mtime_ns": source.stat().st_mtime_ns, "duration": duration,
                    "preview_profile": PREVIEW_PROFILE}
        reuse = video.is_file() and receipt.get("video") == str(video) and (
            receipt.get("media_contract") == contract
            and receipt.get("video_sha256") == file_sha256(video)
        )
        if video.exists() and not reuse:
            raise FileExistsError(f"保留与本轮记录不匹配的视频：{video}；请指定新的--output-dir")
        if not reuse:
            render_review_video(source=source, output_path=video, start=0, end=duration,
                                ffmpeg=resolve_binary("ffmpeg", ffmpeg_value))
        video_record = {"video": str(video), "video_sha256": file_sha256(video),
                        "media_contract": contract}
    subtitle.write_text(caption_text, encoding="utf-8-sig")
    playback = "同名ASS自动加载；仅字幕变化时复用视频\n" if lightweight_video else "视频不复制、不转码；手动加载候选ASS\n"
    note.write_text(f"打开视频：{video}\n字幕：{subtitle.name}\n"
                    f"这是未批准的候选，不是正式合并结果\n缺少字幕的分段：{', '.join(missing) or '无'}\n"
                    + playback, encoding="utf-8-sig")
    write_json(receipt_path, {
        "candidate_only": True, "source": str(source), "subtitle": str(subtitle),
        "subtitle_sha256": file_sha256(subtitle), "included": included, "missing": missing,
        **video_record,
    })
    return subtitle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成逐段审查片段")
    parser.add_argument("--concert-dir", required=True, type=Path)
    parser.add_argument("--section", action="append", dest="sections")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--mode", choices=("sections", "source", "preview"), default="sections")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    console_version("build_review_clips")
    if args.mode in {"source", "preview"}:
        if args.sections:
            raise ValueError("整场候选模式不接受--section")
        print(build_source_preview(args.concert_dir, args.output_dir,
                                   lightweight_video=args.mode == "preview", ffmpeg_value=args.ffmpeg))
        return 0
    summaries = build_review_clips(
        args.concert_dir,
        set(args.sections or []),
        args.ffmpeg,
        args.output_dir,
    )
    for summary in summaries:
        print(f"{summary['section_id']}: clips={summary['clip_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

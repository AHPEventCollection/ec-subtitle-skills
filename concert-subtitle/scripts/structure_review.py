"""Concert adapter: complete sample, structural candidate, explicitly reviewed apply."""
from __future__ import annotations

import argparse
from pathlib import Path

from common import (
    archive_section_results, canonical_hash, load_events, load_manifest, package_input_hash,
    read_json, resolve_binary, resolve_review_media, resolve_stored_path,
    section_directory, update_status, write_json,
)
from structure_timing import (
    audio_onsets, current_pack, load_structure, normalized_events, prepare_review, choose_cycle, cycle_window,
    proposed_review, read_srt_against, read_tsv, write_rhythm_hints,
)


def applied_structure_is_current(directory: Path, package: dict, events: list[dict], report: dict) -> bool:
    structure = report.get("structure_timing", {})
    plan = directory / "structure.tsv"
    return (plan.is_file() and structure.get("input_hash") == package.get("input_hash")
        and structure.get("events_hash") == canonical_hash(events)
        and structure.get("coverage") == len(events)
        and structure.get("local_review_completed") is True
        and structure.get("structure_hash") == canonical_hash(read_tsv(plan)))


def context(concert_dir: Path, section_id: str) -> dict:
    manifest = load_manifest(concert_dir)
    section = next(s for s in manifest["sections"] if s["id"] == section_id)
    if section["type"] != "song":
        raise ValueError("结构推广只适用于歌曲")
    directory = section_directory(concert_dir, section_id)
    package = read_json(directory / "input.json")
    if package["input_hash"] != package_input_hash(package):
        raise ValueError("分段输入已变化，请重建工作包")
    events = load_events(directory / "events.json")
    return dict(manifest=manifest, section=section, directory=directory, package=package, events=events,
                duration=section["end"]-section["start"], plan=directory / "structure.tsv",
                output=concert_dir / "run" / "review" / section_id / "structure")


def prepare_section(concert_dir: Path, section_id: str, *, cycle: str | None = None,
                    ffmpeg_value: str | None = None, render_video: bool = True) -> dict:
    ctx = context(concert_dir, section_id)
    report_path = ctx["directory"] / "report.json"
    report = read_json(report_path) if report_path.exists() else {}
    applied = applied_structure_is_current(ctx["directory"], ctx["package"], ctx["events"], report)
    rows = load_structure(ctx["plan"], ctx["events"], ctx["duration"])
    if applied:
        values = {r["key"]: r["value"] for r in read_tsv(ctx["output"] / "selection.tsv")}
        selected = choose_cycle(rows, values["cycle"])
        start, end = cycle_window(selected, ctx["duration"])
        result = dict(start=start, end=end, origin=start, reference=ctx["output"] / "reference.srt")
        visible = [{**e, "start": e["start"]-start, "end": e["end"]-start}
                   for e in ctx["events"][selected[0]["first"]-1:selected[-1]["last"]]]
    else:
        result = prepare_review(ctx["events"], ctx["plan"], ctx["duration"], ctx["output"],
                                cycle=cycle, zero_origin=True)
        values = current_pack(ctx["events"], rows, ctx["output"])
        selected = choose_cycle(rows, values["cycle"])
        expected = normalized_events(ctx["events"])[selected[0]["first"]-1:selected[-1]["last"]]
        visible = read_srt_against(result["reference"], expected)
    from build_review_clips import render_review_video
    from merge_concert_subtitles import render_ass
    ass = ctx["output"] / "reference.ass"
    ass.write_text(render_ass(visible, section_id), encoding="utf-8-sig")
    video = ctx["output"] / "reference.mp4"
    if render_video:
        render_review_video(source=resolve_review_media(ctx["manifest"], concert_dir), output_path=video,
                            start=ctx["section"]["start"] + result["start"],
                            end=ctx["section"]["start"] + result["end"],
                            ffmpeg=resolve_binary("ffmpeg", ffmpeg_value))
    return dict(section_id=section_id, clip_count=1, review_point="representative_verse_to_chorus",
                start=ctx["section"]["start"]+result["start"], end=ctx["section"]["start"]+result["end"],
                video=str(video), subtitle=str(ass), reference=str(result["reference"]))


def propose_section(concert_dir: Path, section_id: str, *, ffmpeg_value: str | None = None,
                    write: bool = True) -> tuple[list[dict], list[dict]]:
    ctx = context(concert_dir, section_id)
    audio = resolve_stored_path(ctx["package"]["audio_path"], ctx["directory"])
    offset = ctx["section"]["start"] - ctx["package"].get("audio_context_origin", ctx["section"]["start"])
    onsets = audio_onsets(audio, resolve_binary("ffmpeg", ffmpeg_value), offset)
    return proposed_review(ctx["events"], ctx["plan"], ctx["duration"], ctx["output"], onsets, write=write)


def rhythm_section(concert_dir: Path, section_id: str, ffmpeg_value: str | None = None) -> Path:
    ctx = context(concert_dir, section_id)
    audio = resolve_stored_path(ctx["package"]["audio_path"], ctx["directory"])
    offset = ctx["section"]["start"] - ctx["package"].get("audio_context_origin", ctx["section"]["start"])
    onsets = audio_onsets(audio, resolve_binary("ffmpeg", ffmpeg_value), offset)
    return write_rhythm_hints(ctx["plan"], onsets, ctx["output"])


def apply_section(concert_dir: Path, section_id: str, *, reviewed: bool,
                  ffmpeg_value: str | None = None) -> Path:
    if not reviewed:
        raise ValueError("须先实际复核整曲结构候选及局部差异，明确确认后再使用--reviewed")
    ctx = context(concert_dir, section_id)
    expected, usage = propose_section(concert_dir, section_id, ffmpeg_value=ffmpeg_value, write=False)
    final = read_srt_against(ctx["output"] / "structure-candidate.srt", expected)
    final = normalized_events(final)
    if any(e["end"] > ctx["duration"] for e in final) or any(
            b["start"] < a["end"] - 0.001 for a, b in zip(final, final[1:])):
        raise ValueError("人工局部修订仍有越界或重叠，不能应用")
    path = ctx["directory"] / "events.json"
    payload = read_json(path)
    events = [{**old, "start": new["start"], "end": new["end"],
               "structure_reference": note} for old, new, note in zip(ctx["events"], final, usage)]
    report_path = ctx["directory"] / "report.json"
    report = read_json(report_path) if report_path.exists() else {}
    report["structure_timing"] = dict(input_hash=ctx["package"]["input_hash"],
        events_hash=canonical_hash(events), structure_hash=canonical_hash(read_tsv(ctx["plan"])),
        coverage=len(usage), mapped=sum(row["status"] == "mapped" for row in usage),
        local_review_completed=True)
    archive_section_results(ctx["directory"], "reviewed-structure-transfer", [])
    payload["events"] = events
    write_json(path, payload)
    write_json(report_path, report)
    update_status(ctx["directory"], "stale", "structure_transfer_requires_gate_and_main_review",
                  section_id=section_id, input_hash=ctx["package"]["input_hash"])
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="完整一番/二番校准与整曲结构时间推广")
    parser.add_argument("action", choices=["rhythm", "prepare", "propose", "apply"])
    parser.add_argument("--concert-dir", required=True, type=Path)
    parser.add_argument("--section", required=True)
    parser.add_argument("--cycle")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--reviewed", action="store_true")
    args = parser.parse_args()
    if args.action == "rhythm":
        print(rhythm_section(args.concert_dir, args.section, args.ffmpeg))
    elif args.action == "prepare":
        result = prepare_section(args.concert_dir, args.section, cycle=args.cycle, ffmpeg_value=args.ffmpeg)
        print(f"完整代表样片：{result['video']}")
    elif args.action == "propose":
        _, usage = propose_section(args.concert_dir, args.section, ffmpeg_value=args.ffmpeg)
        print(f"已生成整曲候选：{len(usage)}行，已参考映射{sum(r['status'] == 'mapped' for r in usage)}行")
    else:
        print(apply_section(args.concert_dir, args.section, reviewed=args.reviewed, ffmpeg_value=args.ffmpeg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""MV adapter: file-based structural candidates; never changes the manual master."""
from __future__ import annotations

import subprocess
from pathlib import Path

from common import ensure_workspace, find_source_video, probe_media, resolve_binary
from structure_timing import (
    FIELDS, EVENT_FIELDS, audio_onsets, normalized_events, prepare_review, proposed_review,
    read_srt_against, write_tsv, write_rhythm_hints,
)
from subtitle_io import Cue, load_cues


def events_from_candidate(candidate: Path) -> list[dict]:
    return normalized_events([dict(id=str(i), start=c.start, end=c.end,
        source_text=c.japanese, translation=c.chinese) for i, c in enumerate(load_cues(candidate), 1)])


def start_structure_review(mv_dir: Path, candidate: Path) -> Path:
    output = mv_dir / "review" / "structure"
    output.mkdir(parents=True, exist_ok=True)
    plan = mv_dir / "review" / "structure.tsv"
    if not plan.exists():
        write_tsv(plan, [], FIELDS)
    instructions = output / "start.md"
    events = events_from_candidate(candidate)
    write_tsv(output / "initial-events.tsv", events, EVENT_FIELDS)
    instructions.write_text("\n".join([
        "# 下一步：主线程建立完整代表段", "",
        "结合已知歌词和独立音频填写review/structure.tsv，表格由代理维护，不要求用户填写",
        "列出一番、二番及其他段落的主歌/过渡/副歌边界和拍数假设，证据不足就继续定位",
        "运行structure-prepare生成完整代表段，校准后运行structure-propose生成整曲参考候选",
        "不得直接把短片段精度或SOFA初稿当作已完成代表段推广", ""
    ]), encoding="utf-8")
    return instructions


def _context(mv_dir: Path, candidate: Path) -> dict:
    mv_dir = ensure_workspace(mv_dir)
    if candidate.name == "official-subtitle.srt":
        from official_subtitle import validate_official_candidate
        validate_official_candidate(mv_dir, candidate)
    source = find_source_video(mv_dir)
    info = probe_media(source)
    return dict(mv_dir=mv_dir, source=source, events=events_from_candidate(candidate),
        duration=info.duration, plan=mv_dir / "review" / "structure.tsv",
        output=mv_dir / "review" / "structure")


def prepare_structure(mv_dir: Path, candidate: Path, cycle: str | None = None) -> Path:
    ctx = _context(mv_dir, candidate)
    pointer = ctx["output"] / "candidate-source.txt"
    if pointer.exists() and Path(pointer.read_text(encoding="utf-8").strip()).resolve() != candidate.resolve():
        raise ValueError("已有代表段来自另一候选，请使用新的审核工作区")
    result = prepare_review(ctx["events"], ctx["plan"], ctx["duration"], ctx["output"], cycle=cycle)
    pointer.write_text(str(candidate.resolve()), encoding="utf-8")
    from song_candidate import _prepare_candidate_preview
    # The MV keeps the full, unchanged source. The reference retains absolute timestamps.
    _prepare_candidate_preview(mv_dir, ctx["source"], result["reference"])
    return result["report"]


def _onsets(ctx: dict) -> list[float]:
    audio = ctx["mv_dir"] / "work" / "structure-mix.wav"
    ffmpeg = resolve_binary("ffmpeg")
    if not audio.is_file() or audio.stat().st_mtime_ns < ctx["source"].stat().st_mtime_ns:
        subprocess.run([str(ffmpeg), "-v", "error", "-nostdin", "-y", "-i", str(ctx["source"]),
            "-vn", "-ac", "1", "-ar", "8000", "-c:a", "pcm_s16le", str(audio)], check=True)
    return audio_onsets(audio, ffmpeg)


def propose_structure(mv_dir: Path, *, write: bool = True) -> Path:
    output = mv_dir / "review" / "structure"
    pointer = output / "candidate-source.txt"
    if not pointer.is_file():
        raise ValueError("先运行structure-prepare并校准完整代表段")
    candidate = Path(pointer.read_text(encoding="utf-8").strip())
    ctx = _context(mv_dir, candidate)
    result, _ = proposed_review(ctx["events"], ctx["plan"], ctx["duration"], output, _onsets(ctx), write=write)
    target = output / "structure-candidate.srt"
    if write:
        from song_candidate import _prepare_candidate_preview
        _prepare_candidate_preview(mv_dir, ctx["source"], target, review_cues=[
            Cue(e["start"], e["end"], e["translation"], e["source_text"]) for e in result])
    else:
        checked = read_srt_against(target, result)
        if any(e["end"] > ctx["duration"] for e in checked) or any(
                b["start"] < a["end"] - 0.001 for a, b in zip(checked, checked[1:])):
            raise ValueError("结构候选局部修订后仍有越界或重叠")
    return target


def preview_structure(mv_dir: Path) -> Path:
    output = mv_dir / "review" / "structure"
    candidate = Path((output / "candidate-source.txt").read_text(encoding="utf-8").strip())
    ctx = _context(mv_dir, candidate)
    expected, _ = proposed_review(ctx["events"], ctx["plan"], ctx["duration"], output, _onsets(ctx), write=False)
    target = output / "structure-candidate.srt"
    edited = read_srt_against(target, expected)
    from song_candidate import _prepare_candidate_preview
    _, _, report = _prepare_candidate_preview(mv_dir, ctx["source"], target, review_cues=[
        Cue(e["start"], e["end"], e["translation"], e["source_text"]) for e in edited])
    return report


def rhythm_structure(mv_dir: Path, candidate: Path) -> Path:
    ctx = _context(mv_dir, candidate)
    return write_rhythm_hints(ctx["plan"], _onsets(ctx), ctx["output"])


def validate_structure_promotion(mv_dir: Path, candidate: Path) -> None:
    output = mv_dir / "review" / "structure"
    if not (output / "start.md").exists() and not (output / "candidate-source.txt").exists():
        return
    if candidate.resolve() != (output / "structure-candidate.srt").resolve():
        raise ValueError("当前歌曲须先完成完整代表段和结构推广，再审定structure-candidate.srt")
    propose_structure(mv_dir, write=False)

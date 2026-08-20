from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import common
from alignment_workflow import (
    build_sofa_candidate,
    prepare_alignment,
    preflight_alignment,
    validate_alignment_candidates,
)
from common import ensure_workspace, find_source_video, write_text
from runtime_manager import (
    doctor_state,
    load_json,
    profile_environment,
    resolve_relative,
)


def _run(command: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    completed = subprocess.run(command, env=env, cwd=cwd, check=False)
    if completed.returncode:
        raise RuntimeError(f"命令失败({completed.returncode})：{command[0]}")


def _pending_pronunciations(mv_dir: Path) -> int:
    path = mv_dir / "review" / "alignment" / "pronunciation-review.tsv"
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _row in csv.DictReader(handle, delimiter="\t"))


def _fresh(output: Path, *inputs: Path) -> bool:
    return (
        output.is_file()
        and output.stat().st_size > 0
        and all(output.stat().st_mtime_ns >= item.stat().st_mtime_ns for item in inputs)
    )


def _extract_mix(source: Path, target: Path, ffmpeg: str, *, seconds: int | None = None) -> None:
    pending = target.with_name(f"{target.stem}.pending{target.suffix}")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vn", "-ac", "2", "-ar", "44100"]
    if seconds is not None:
        command.extend(["-t", str(seconds)])
    command.extend(["-c:a", "pcm_s16le", str(pending)])
    _run(command)
    os.replace(pending, target)


def _separate(audio: Path, target: Path, *, python: Path, env: dict[str, str], model: Path, use_autocast: bool) -> None:
    output_name = f"{target.stem}.pending"
    pending = target.with_name(f"{output_name}.wav")
    command = [
        str(python), str(Path(__file__).with_name("separator_cli.py")),
        "--model_filename", model.name, "--model_file_dir", str(model.parent),
        "--output_dir", str(target.parent), "--output_format", "WAV",
        "--single_stem", "Vocals", "--custom_output_names",
        json.dumps({"Vocals": output_name}),
    ]
    if use_autocast:
        command.append("--use_autocast")
    command.append(str(audio))
    _run(command, env=env)
    if not pending.is_file() or pending.stat().st_size == 0:
        raise RuntimeError(f"人声分离没有生成预期文件：{pending}")
    os.replace(pending, target)


def _prepare_vocals(mv_dir: Path, source: Path, state: dict[str, Any], root: Path, manifest: dict[str, Any]) -> Path:
    work = mv_dir / "work"
    ffmpeg = state["executables"]["ffmpeg"]
    mix = work / "mix.wav"
    if not _fresh(mix, source):
        _extract_mix(source, mix, ffmpeg)

    profile = manifest["profiles"]["separator"]
    python = resolve_relative(root, profile["python"])
    model = resolve_relative(root, profile["artifacts"]["separator_model"])
    if not python or not model:
        raise RuntimeError("separator运行时清单不完整")
    env = profile_environment(root, manifest, "separator")
    use_autocast = bool(state["components"].get("separator_cuda", {}).get("cuda_available"))

    sample_mix = work / "separation-sample.wav"
    sample_vocals = work / "separation-sample" / "vocals.wav"
    sample_vocals.parent.mkdir(parents=True, exist_ok=True)
    if not _fresh(sample_mix, source):
        _extract_mix(source, sample_mix, ffmpeg, seconds=15)
    if not _fresh(sample_vocals, sample_mix, model):
        _separate(sample_mix, sample_vocals, python=python, env=env, model=model, use_autocast=use_autocast)

    vocals = work / "vocals.wav"
    if not _fresh(vocals, mix, model):
        _separate(mix, vocals, python=python, env=env, model=model, use_autocast=use_autocast)
    return vocals


def _prepare_sofa(mv_dir: Path, vocals: Path, root: Path, manifest: dict[str, Any]) -> Path:
    review = mv_dir / "review" / "alignment"
    song_lab = review / "song.lab"
    sofa_input = mv_dir / "work" / "sofa-input"
    sofa_input.mkdir(parents=True, exist_ok=True)
    sofa_wav = sofa_input / "vocals.wav"
    sofa_lab = sofa_input / "vocals.lab"
    if not _fresh(sofa_wav, vocals):
        shutil.copy2(vocals, sofa_wav)
    if not _fresh(sofa_lab, song_lab):
        shutil.copy2(song_lab, sofa_lab)

    htk = sofa_input / "htk" / "words" / "vocals.lab"
    profile = manifest["profiles"]["sofa"]
    python = resolve_relative(root, profile["python"])
    model = resolve_relative(root, profile["artifacts"]["sofa_model"])
    tool = resolve_relative(root, profile["artifacts"]["sofa_tool"])
    if not python or not model or not tool:
        raise RuntimeError("SOFA运行时清单不完整")
    infer = tool / "onnx_infer.py"
    if not _fresh(htk, sofa_wav, sofa_lab, model, infer):
        _run(
            [
                str(python), str(infer), "--onnx", str(model), "--folder", str(sofa_input),
                "--g2p", "Dictionary", "--ap_detector", "LoudnessSpectralcentroidAPDetector",
                "--out_formats", "textgrid,htk",
            ],
            env=profile_environment(root, manifest, "sofa"),
            cwd=tool,
        )
    if not htk.is_file():
        raise RuntimeError(f"SOFA没有生成预期HTK文件：{htk}")
    return htk


def _sample_source_frames(mv_dir: Path, source: Path, ffmpeg: str) -> Path:
    audit_dir = mv_dir / "review" / "source-visual-audit"
    frames = audit_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    first = frames / "frame-001.jpg"
    if not _fresh(first, source):
        _run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-vf", "fps=1/20,scale=960:-2",
                "-frames:v", "12", "-q:v", "3", str(frames / "frame-%03d.jpg"),
            ]
        )
    sampled = sorted(frames.glob("frame-*.jpg"))
    if not sampled:
        raise RuntimeError("画面文字抽样没有生成代表帧")
    report = audit_dir / "audit.md"
    write_text(
        report,
        "\n".join(
            [
                "# MV源画面文字抽样", "",
                "- 抽样间隔　20秒", "- 代表帧上限　12",
                f"- 实际代表帧　{len(sampled)}",
                "- 候选生成　不因本项暂停",
                "- 后续规则　代表帧疑似连续歌词卡时，在确认master.srt前补做逐句画面对照",
                "",
            ]
        ),
    )
    return report


def build_song_candidate(mv_dir: Path, runtime_root: str | None = None) -> Path:
    mv_dir = ensure_workspace(mv_dir)
    state = doctor_state(
        "song", runtime_root=runtime_root, project_root=str(common.project_root_for_mv(mv_dir))
    )
    if not state["ready"]:
        raise RuntimeError("song运行时未就绪：" + "；".join(state.get("issues", [])))
    root = Path(state["runtime_root"])
    manifest = load_json(root / "runtime.json")

    prepare_alignment(mv_dir)
    pending = _pending_pronunciations(mv_dir)
    if pending:
        raise RuntimeError(f"发现{pending}行真实读音疑点，请仅在lyrics/pronunciation-overrides.tsv补充确认项后重跑")

    source = find_source_video(mv_dir)
    visual_audit = _sample_source_frames(
        mv_dir, source, state["executables"]["ffmpeg"]
    )
    vocals = _prepare_vocals(mv_dir, source, state, root, manifest)
    preflight_alignment(mv_dir, "sofa", state["executables"]["ffprobe"])
    htk = _prepare_sofa(mv_dir, vocals, root, manifest)
    candidate = build_sofa_candidate(mv_dir, htk)
    validate_alignment_candidates(mv_dir, "sofa")
    report = mv_dir / "review" / "alignment" / "candidate-build.md"
    write_text(
        report,
        "\n".join(
            [
                "# MV歌曲候选构建", "", f"- 运行时状态　{state['status']}",
                f"- 分离人声　{vocals}", f"- SOFA输出　{htk}",
                f"- 画面文字抽样　{visual_audit}",
                f"- 字幕候选　{candidate}",
                "- 预览视频　未生成，候选确认并提升为master.srt后再生成外挂ASS",
                "- 人工主字幕　未修改",
                "- 下一步　人工复对候选后再导入正式字幕", "",
            ]
        ),
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="可续跑的MV歌曲候选构建器")
    parser.add_argument("--mv-dir", required=True, type=Path)
    parser.add_argument("--runtime-root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = build_song_candidate(args.mv_dir, args.runtime_root)
    except Exception as error:
        print(f"error={error}")
        return 2
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

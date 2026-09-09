from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import (
    MEDIA_SUFFIXES, MP4_COPY_AUDIO, ensure_workspace, probe_media,
    project_root_for_mv, write_text,
)
from runtime_manager import doctor_state

CLIENT_FAILURE = re.compile(
    r"HTTP Error 403|requested format is not available|No video formats found|"
    r"nsig extraction failed|signature extraction failed|challenge solver|"
    r"The page needs to be reloaded",
    re.IGNORECASE,
)
RATE_LIMIT = re.compile(
    r"HTTP Error 429|Too Many Requests|rate.?limit",
    re.IGNORECASE,
)
AUTHENTICATION = re.compile(
    r"Sign in to confirm|login required|age.restricted|members.only|private video",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AcquisitionResult:
    media: Path
    facts: dict[str, str]
    ffprobe: str
    strategy: str
    evidence: Path


def _parse_markers(stdout: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in stdout.splitlines():
        if not line.startswith("__YTDLP_GLOBAL_") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized = key.removeprefix("__YTDLP_GLOBAL_").strip("_").lower()
        if normalized == "channel":
            normalized = "artist"
        result[normalized] = re.sub(r"\s+", " ", value).strip()
    return result


def _parse_global_payload(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("operation") == "download":
            return payload
    return {}


def _error_class(output: str) -> str:
    status = _parse_global_payload(output).get("status")
    if status in {"video-not-ready", "download-stalled"}:
        return status
    if RATE_LIMIT.search(output):
        return "rate-limited"
    if AUTHENTICATION.search(output):
        return "authentication-required"
    if CLIENT_FAILURE.search(output):
        return "client-or-challenge"
    return "fatal"


def _tail(value: str, limit: int = 4000) -> str:
    compact = "\n".join(line for line in value.splitlines()[-40:] if line.strip())
    return compact[-limit:]


def _subtitle_error(output: str) -> str | None:
    missing_requested_subtitle = re.search(
        r"File \".*\.(?:ja|zh-Hans|zh-Hant)\.(?:vtt|srt|ttml|srv[123]|json3)\" cannot be found",
        output,
        re.IGNORECASE,
    )
    if "Did not get any data blocks" in output and (
        "Writing video subtitles" in output or missing_requested_subtitle
    ):
        return "empty-data"
    if re.search(r"Unable to download (?:video )?subtitles", output, re.IGNORECASE):
        return "download-failed"
    return None


def _without_subtitle_download(command: list[str]) -> list[str]:
    result: list[str] = []
    skip_next = False
    for argument in command:
        if skip_next:
            skip_next = False
            continue
        if argument == "--write-subs":
            continue
        if argument == "--sub-langs":
            skip_next = True
            continue
        result.append(argument)
    return result


def _write_evidence(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _stream_download(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env,
    )
    lines: list[str] = []
    assert process.stdout is not None
    try:
        for raw in process.stdout:
            lines.append(raw)
            print(raw, end="", flush=True)
        return subprocess.CompletedProcess(command, process.wait(), "".join(lines), "")
    finally:
        process.stdout.close()


def _run_download(
    command: list[str], *, env: dict[str, str],
    audit: dict[str, Any], evidence: Path,
) -> subprocess.CompletedProcess[str]:
    deadline = time.monotonic() + 600
    while True:
        started = time.monotonic()
        completed = _stream_download(command, env)
        payload = _parse_global_payload(completed.stdout)
        if payload.get("status") != "video-not-ready" or time.monotonic() >= deadline:
            return completed
        audit["attempts"].append({
            "strategy": "ytdlp-global-auto-vod-wait",
            "returncode": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "error_class": "video-not-ready",
            "authentication": payload.get("authentication"),
            "output_tail": _tail(completed.stdout),
        })
        _write_evidence(evidence, audit)
        remaining = max(0, deadline - time.monotonic())
        delay = min(30, remaining)
        print(f"[MV] 官方首映尚未结束，{delay:.0f}秒后通过同一入口重试", flush=True)
        time.sleep(delay)


def _global_downloader_command() -> list[str]:
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    wrapper: Path | None = None
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory.strip():
            continue
        candidate = Path(directory.strip('"')) / "ytdlp-global.ps1"
        if candidate.is_file():
            wrapper = candidate.resolve()
            break
    if not pwsh:
        raise RuntimeError("找不到PowerShell 7，无法调用全局ytdlp-global")
    if not wrapper:
        raise RuntimeError("找不到全局ytdlp-global.ps1，请检查共享入口安装")
    return [pwsh, "-NoProfile", "-File", str(wrapper)]


def _compatible_audio(
    media: Path, payload: dict[str, Any], facts: dict[str, str],
    command: list[str], env: dict[str, str], audit: dict[str, Any],
    evidence: Path, ffmpeg: str, ffprobe: str, mv_dir: Path,
) -> None:
    streams = payload.get("media", {}).get("streams", [])
    codec = next((item.get("codec_name") for item in streams
                  if item.get("codec_type") == "audio"), None)
    if codec is None:
        codec = probe_media(media, ffprobe).audio_codec
    if codec is None or codec in MP4_COPY_AUDIO:
        audit["audio_compatibility"] = {"action": "already-compatible", "codec": codec}
        return

    # Preserve the selected highest-resolution video. The global MP4 preset is
    # used only to obtain official copy-compatible audio, never as the picture.
    original = mv_dir / "work" / "youtube-original" / media.name
    original.parent.mkdir(parents=True, exist_ok=True)
    if original.exists():
        raise FileExistsError(f"原始片源保留位置已存在，拒绝覆盖：{original}")
    shutil.copy2(media, original)
    audio_dir = media.parent / "compatible-audio"
    audio_dir.mkdir()
    supplement = _without_subtitle_download(command)
    supplement = [arg for arg in supplement if arg != "--write-thumbnail"]
    supplement[supplement.index("--container") + 1] = "mp4"
    supplement[supplement.index("--output") + 1] = str(audio_dir / "source.%(ext)s")
    started = time.monotonic()
    completed = _run_download(supplement, env=env, audit=audit, evidence=evidence)
    result = _parse_global_payload(completed.stdout)
    audit["attempts"].append({
        "strategy": "ytdlp-global-compatible-audio", "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "output_tail": _tail(completed.stdout),
    })
    audit["audio_compatibility"] = {"action": "official-audio-remux", "original": str(original)}
    _write_evidence(evidence, audit)
    if completed.returncode or result.get("status") != "verified":
        raise RuntimeError("官方兼容音轨取得失败，原始片源已保留：" + str(original))
    secondary = _parse_markers(completed.stdout)
    if not facts.get("id") or secondary.get("id") != facts["id"]:
        raise RuntimeError("补充音轨与原始视频ID不一致")
    audio = Path(secondary.get("file", "")).resolve()
    if not audio.is_file() or audio.parent != audio_dir.resolve():
        raise RuntimeError("兼容音轨不在本次下载暂存目录")
    before = probe_media(media, ffprobe)
    supplement_info = probe_media(audio, ffprobe)
    if supplement_info.audio_codec not in MP4_COPY_AUDIO:
        raise RuntimeError("官方补充音轨仍不支持MP4直拷贝")
    if abs(before.duration - supplement_info.duration) > 0.25:
        raise RuntimeError("官方补充音轨与原始视频时长不一致")
    remuxed = media.with_name("source-compatible.mkv")
    subprocess.run([
        ffmpeg, "-nostdin", "-v", "error", "-i", str(media), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", str(remuxed),
    ], check=True, capture_output=True, text=True)
    after = probe_media(remuxed, ffprobe)
    if ((before.width, before.height, before.video_codec) !=
            (after.width, after.height, after.video_codec)
            or after.audio_codec != supplement_info.audio_codec
            or abs(before.duration - after.duration) > 0.25):
        raise RuntimeError("兼容音轨封装后媒体检查失败")
    os.replace(remuxed, media)
    audit["audio_compatibility"]["codec"] = after.audio_codec


def acquire_source(
    mv_dir: Path,
    url: str,
    runtime_root: str | None = None,
) -> AcquisitionResult:
    mv_dir = ensure_workspace(mv_dir)
    source_dir = mv_dir / "source"
    if any(
        path.is_file() and path.suffix.casefold() in MEDIA_SUFFIXES
        for path in source_dir.iterdir()
    ):
        raise FileExistsError("source目录已有视频，拒绝重复下载")

    state = doctor_state(
        "youtube-download",
        runtime_root=runtime_root,
        project_root=str(project_root_for_mv(mv_dir)),
    )
    if not state["ready"]:
        raise RuntimeError(
            "youtube-download运行时未就绪：" + "；".join(state.get("issues", []))
        )

    python = state["profiles"]["base"]["python"]
    ffmpeg = state["executables"]["ffmpeg"]
    ffprobe = state["executables"]["ffprobe"]
    if not python or not ffmpeg or not ffprobe:
        raise RuntimeError(
            "youtube-download预检没有返回完整的Python、FFmpeg和ffprobe"
        )

    runtime = Path(state["runtime_root"])
    staging = mv_dir / "work" / "youtube-acquire"
    evidence = mv_dir / "review" / "source-acquisition.json"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    downloader_command = _global_downloader_command()
    base_command = [
        *downloader_command,
        "download",
        "--url",
        url,
        "--output",
        str(staging / "source.%(ext)s"),
        "--auth",
        "auto",
        "--max-height",
        "4320",
        "--container",
        "mkv",
        "--prefer-sdr",
        "--require-vod",
        "--stall-timeout",
        "90",
        "--write-thumbnail",
        "--write-subs",
        "--sub-langs",
        "ja,zh-Hans,zh-Hant",
    ]
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    audit: dict[str, Any] = {
        "schema": "music-video-subtitle/source-acquisition/v1",
        "url": url,
        "runtime_root": str(runtime),
        "python": python,
        "downloader_command": downloader_command,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "requested_authentication": "auto",
        "authentication": None,
        "attempts": [],
        "selected_strategy": None,
    }

    try:
        for strategy, strategy_args in [("ytdlp-global-auto", [])]:
            started = time.monotonic()
            command = [*base_command, *strategy_args]
            completed = _run_download(
                command,
                env=child_env,
                audit=audit, evidence=evidence,
            )
            output = "\n".join(filter(None, (completed.stdout, completed.stderr)))
            global_payload = _parse_global_payload(output)
            classification = None if completed.returncode == 0 else _error_class(output)
            subtitle_error = _subtitle_error(output)
            audit["attempts"].append(
                {
                    "strategy": strategy,
                    "returncode": completed.returncode,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "error_class": classification,
                    "subtitle_error": subtitle_error,
                    "authentication": global_payload.get("authentication"),
                    "output_tail": _tail(output),
                }
            )
            _write_evidence(evidence, audit)
            selected_strategy = strategy
            if completed.returncode:
                if subtitle_error:
                    audit["subtitle_status"] = "download-failed"
                    audit["subtitle_error"] = subtitle_error
                    shutil.rmtree(staging)
                    staging.mkdir(parents=True)
                    selected_strategy = f"{strategy}-media-only"
                    started = time.monotonic()
                    command = _without_subtitle_download(command)
                    completed = _run_download(
                        command,
                        env=child_env,
                        audit=audit, evidence=evidence,
                    )
                    output = "\n".join(
                        filter(None, (completed.stdout, completed.stderr))
                    )
                    global_payload = _parse_global_payload(output)
                    classification = (
                        None if completed.returncode == 0 else _error_class(output)
                    )
                    audit["attempts"].append(
                        {
                            "strategy": selected_strategy,
                            "returncode": completed.returncode,
                            "duration_seconds": round(time.monotonic() - started, 3),
                            "error_class": classification,
                            "subtitle_error": None,
                            "authentication": global_payload.get("authentication"),
                            "output_tail": _tail(output),
                        }
                    )
                    _write_evidence(evidence, audit)
                    if completed.returncode:
                        raise RuntimeError(
                            f"ytdlp-global失败[{classification}]：{_tail(output)}"
                        )
                else:
                    raise RuntimeError(
                        f"ytdlp-global失败[{classification}]：{_tail(output)}"
                    )

            facts = _parse_markers(completed.stdout)
            facts["url"] = url
            media_value = facts.get("file")
            if not media_value:
                raise RuntimeError("ytdlp-global没有返回最终媒体路径")
            staged_media = Path(media_value).resolve()
            if not staged_media.is_file() or staged_media.parent != staging.resolve():
                raise RuntimeError(f"下载完成后未在暂存目录找到媒体：{staged_media}")

            _compatible_audio(
                staged_media, global_payload, facts, command, child_env,
                audit, evidence, ffmpeg, ffprobe, mv_dir,
            )
            moved: dict[Path, Path] = {}
            for item in staging.iterdir():
                if not item.is_file():
                    continue
                destination = source_dir / item.name
                if destination.exists():
                    raise FileExistsError(
                        f"source目录存在同名文件，拒绝覆盖：{destination}"
                    )
                os.replace(item, destination)
                moved[item.resolve()] = destination.resolve()
            media = moved.get(staged_media)
            if not media or not media.is_file():
                raise RuntimeError("暂存片源没有成功移入source目录")
            facts["file"] = str(media)
            if subtitle_error:
                facts["subtitle_status"] = "download-failed"
                audit["subtitle_status"] = "download-failed"
                audit["subtitle_error"] = subtitle_error
            else:
                facts["subtitle_status"] = "normal"
                audit["subtitle_status"] = "normal"
            audit["selected_strategy"] = selected_strategy
            audit["authentication"] = global_payload.get("authentication")
            audit["media"] = str(media)
            _write_evidence(evidence, audit)
            return AcquisitionResult(media, facts, ffprobe, selected_strategy, evidence)
        raise RuntimeError("ytdlp-global没有可用下载策略")
    finally:
        if staging.exists():
            shutil.rmtree(staging)

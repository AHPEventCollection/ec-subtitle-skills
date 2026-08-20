from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import MEDIA_SUFFIXES, ensure_workspace, project_root_for_mv, write_text
from runtime_manager import doctor_state

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
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
        if not line.startswith("__MV_") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized = key.removeprefix("__MV_").strip("_").lower()
        result[normalized] = re.sub(r"\s+", " ", value).strip()
    return result


def _is_youtube(url: str) -> bool:
    return (urllib.parse.urlparse(url).hostname or "").casefold() in YOUTUBE_HOSTS


def _error_class(output: str) -> str:
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


def _attempts(url: str) -> list[tuple[str, list[str]]]:
    attempts = [("upstream-default", [])]
    if _is_youtube(url):
        attempts.append(
            (
                "youtube-embedded-fallback",
                ["--extractor-args", "youtube:player_client=default,web_embedded"],
            )
        )
    return attempts


def _write_evidence(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def acquire_source(
    mv_dir: Path,
    url: str,
    runtime_root: str | None = None,
) -> AcquisitionResult:
    mv_dir = ensure_workspace(mv_dir)
    source_dir = mv_dir / "source"
    if any(path.is_file() and path.suffix.casefold() in MEDIA_SUFFIXES for path in source_dir.iterdir()):
        raise FileExistsError("source目录已有视频，拒绝重复下载")

    state = doctor_state(
        "youtube-download",
        runtime_root=runtime_root,
        project_root=str(project_root_for_mv(mv_dir)),
    )
    if not state["ready"]:
        raise RuntimeError("youtube-download运行时未就绪：" + "；".join(state.get("issues", [])))

    python = state["profiles"]["base"]["python"]
    ffmpeg = state["executables"]["ffmpeg"]
    ffprobe = state["executables"]["ffprobe"]
    javascript = state.get("javascript")
    if not python or not ffmpeg or not ffprobe or not javascript:
        raise RuntimeError("youtube-download预检没有返回完整的Python、FFmpeg、ffprobe和JS运行时")

    runtime = Path(state["runtime_root"])
    cache = runtime / "cache" / "yt-dlp"
    staging = mv_dir / "work" / "youtube-acquire"
    evidence = mv_dir / "review" / "source-acquisition.json"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    cache.mkdir(parents=True, exist_ok=True)

    base_command = [
        python,
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--cache-dir",
        str(cache),
        "--ffmpeg-location",
        str(Path(ffmpeg).parent),
        "--js-runtimes",
        f"{javascript['kind']}:{javascript['executable']}",
        "--socket-timeout",
        "20",
        "--extractor-retries",
        "1",
        "--retries",
        "2",
        "--fragment-retries",
        "2",
        "--no-playlist",
        "--no-overwrites",
        "--write-thumbnail",
        "--write-subs",
        "--sub-langs",
        "ja,zh-Hans,zh-Hant",
        "--format",
        (
            "bestvideo*[dynamic_range=SDR]+bestaudio[ext=m4a]/"
            "bestvideo*[dynamic_range=SDR]+bestaudio/"
            "best[dynamic_range=SDR]/bestvideo*+bestaudio/best"
        ),
        "--merge-output-format",
        "mkv",
        "--paths",
        f"home:{staging}",
        "--paths",
        f"temp:{staging / 'temp'}",
        "--output",
        "source.%(ext)s",
        "--print",
        "before_dl:__MV_TITLE__=%(title)s",
        "--print",
        "before_dl:__MV_ARTIST__=%(artist,uploader,channel)s",
        "--print",
        "before_dl:__MV_URL__=%(webpage_url)s",
        "--print",
        "after_move:__MV_FILE__=%(filepath)s",
    ]
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    audit: dict[str, Any] = {
        "schema": "music-video-subtitle/source-acquisition/v1",
        "url": url,
        "runtime_root": str(runtime),
        "python": python,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "javascript": javascript,
        "attempts": [],
        "selected_strategy": None,
    }

    try:
        for strategy, strategy_args in _attempts(url):
            started = time.monotonic()
            command = [*base_command, *strategy_args, url]
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=child_env,
            )
            output = "\n".join(filter(None, (completed.stdout, completed.stderr)))
            classification = None if completed.returncode == 0 else _error_class(output)
            audit["attempts"].append(
                {
                    "strategy": strategy,
                    "returncode": completed.returncode,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "error_class": classification,
                    "output_tail": _tail(output),
                }
            )
            _write_evidence(evidence, audit)
            if completed.returncode:
                if classification == "client-or-challenge" and strategy == "upstream-default":
                    continue
                raise RuntimeError(f"yt-dlp失败[{classification}]：{_tail(output)}")

            facts = _parse_markers(completed.stdout)
            media_value = facts.get("file")
            if not media_value:
                raise RuntimeError("yt-dlp没有返回最终媒体路径")
            staged_media = Path(media_value).resolve()
            if not staged_media.is_file() or staged_media.parent != staging.resolve():
                raise RuntimeError(f"下载完成后未在暂存目录找到媒体：{staged_media}")

            moved: dict[Path, Path] = {}
            for item in staging.iterdir():
                if not item.is_file():
                    continue
                destination = source_dir / item.name
                if destination.exists():
                    raise FileExistsError(f"source目录存在同名文件，拒绝覆盖：{destination}")
                os.replace(item, destination)
                moved[item.resolve()] = destination.resolve()
            media = moved.get(staged_media)
            if not media or not media.is_file():
                raise RuntimeError("暂存片源没有成功移入source目录")
            facts["file"] = str(media)
            audit["selected_strategy"] = strategy
            audit["media"] = str(media)
            _write_evidence(evidence, audit)
            return AcquisitionResult(media, facts, ffprobe, strategy, evidence)
        raise RuntimeError("yt-dlp没有可用下载策略")
    finally:
        if staging.exists():
            shutil.rmtree(staging)

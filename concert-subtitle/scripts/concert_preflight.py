from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any
from preview_encoding import PREVIEW_SCALE, preview_encoding_args

from common import (
    SCHEMA_VERSION,
    VERSION,
    console_version,
    file_fingerprint,
    resolve_binary,
    utc_now,
    write_json,
)

SEEK_CHECK_RATIOS = (0.17, 0.53, 0.83)
SEEK_CHECK_PREROLL_SECONDS = 8.0
SEEK_CHECK_FRAME_COUNT = 8
SEEK_CHECK_REQUIRED_MATCHES = 3


def probe_media(ffprobe: Path, source: Path) -> dict[str, Any]:
    command = [
        str(ffprobe),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(source),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(result.stdout)


def media_duration(probe: dict[str, Any]) -> float:
    format_duration = probe.get("format", {}).get("duration")
    if format_duration:
        return float(format_duration)
    durations = [
        float(stream["duration"])
        for stream in probe.get("streams", [])
        if stream.get("duration")
    ]
    if not durations:
        raise ValueError("ffprobe没有返回媒体时长")
    return max(durations)


def _seek_checkpoints(duration: float) -> list[float]:
    if duration <= 2.5:
        raise ValueError("源媒体过短，无法执行预览随机访问检查")
    upper = duration - 1.0
    points: list[float] = []
    for ratio in SEEK_CHECK_RATIOS:
        point = round(min(upper, max(1.0, duration * ratio)), 3)
        if not points or abs(point - points[-1]) >= 0.5:
            points.append(point)
    return points


def _frame_hashes(
    ffmpeg: Path,
    source: Path,
    checkpoint: float,
    *,
    reference_seek: bool,
) -> list[str]:
    command = [
        str(ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if reference_seek:
        seek_origin = max(0.0, checkpoint - SEEK_CHECK_PREROLL_SECONDS)
        command.extend(["-ss", f"{seek_origin:.3f}", "-i", str(source)])
        command.extend(["-ss", f"{checkpoint - seek_origin:.3f}"])
    else:
        command.extend(["-ss", f"{checkpoint:.3f}", "-i", str(source)])
    command.extend(
        [
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-frames:v",
            str(SEEK_CHECK_FRAME_COUNT),
            "-vf",
            "scale=160:-2:flags=bilinear,format=gray",
            "-f",
            "framemd5",
            "-",
        ]
    )
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    hashes = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = [field.strip() for field in stripped.split(",")]
        if len(fields) >= 6 and fields[-1]:
            hashes.append(fields[-1])
    return hashes


def _longest_common_run(left: list[str], right: list[str]) -> int:
    longest = 0
    for left_index in range(len(left)):
        for right_index in range(len(right)):
            length = 0
            while (
                left_index + length < len(left)
                and right_index + length < len(right)
                and left[left_index + length] == right[right_index + length]
            ):
                length += 1
            longest = max(longest, length)
    return longest


def assess_source_seek(
    ffmpeg: Path,
    source: Path,
    duration: float,
) -> dict[str, Any]:
    checks = []
    for checkpoint in _seek_checkpoints(duration):
        try:
            direct = _frame_hashes(
                ffmpeg,
                source,
                checkpoint,
                reference_seek=False,
            )
            reference = _frame_hashes(
                ffmpeg,
                source,
                checkpoint,
                reference_seek=True,
            )
            common_run = _longest_common_run(direct, reference)
            required = min(
                SEEK_CHECK_REQUIRED_MATCHES,
                len(direct),
                len(reference),
            )
            passed = required > 0 and common_run >= required
            checks.append(
                {
                    "checkpoint_seconds": checkpoint,
                    "status": "passed" if passed else "failed",
                    "direct_frame_count": len(direct),
                    "reference_frame_count": len(reference),
                    "common_frame_run": common_run,
                    "required_common_run": required,
                }
            )
        except (OSError, subprocess.CalledProcessError) as error:
            checks.append(
                {
                    "checkpoint_seconds": checkpoint,
                    "status": "failed",
                    "reason": "seek_probe_error",
                    "error": str(error),
                }
            )
    passed = bool(checks) and all(check["status"] == "passed" for check in checks)
    return {
        "status": "passed" if passed else "failed",
        "method": "direct_seek_vs_preroll_decode_framemd5",
        "checks": checks,
    }


def transcode_preview_proxy(ffmpeg: Path, source: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.stem}.partial{output_path.suffix}")
    temporary.unlink(missing_ok=True)
    command = [
        str(ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-fflags",
        "+genpts",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-sn",
        "-dn",
        "-vf",
        PREVIEW_SCALE,
        *preview_encoding_args(),
        "-movflags",
        "+faststart",
        str(temporary),
    ]
    try:
        subprocess.run(command, check=True)
        temporary.replace(output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def run_preflight(
    source: Path,
    concert_dir: Path,
    ffmpeg_value: str | None = None,
    ffprobe_value: str | None = None,
) -> dict[str, Any]:
    source = source.resolve()
    concert_dir = concert_dir.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"源媒体不存在：{source}")

    ffmpeg = resolve_binary("ffmpeg", ffmpeg_value)
    ffprobe = resolve_binary("ffprobe", ffprobe_value)
    probe = probe_media(ffprobe, source)
    video_streams = [
        stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"
    ]
    audio_streams = [
        stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"
    ]
    if not video_streams:
        raise ValueError("源媒体没有视频轨")
    if not audio_streams:
        raise ValueError("源媒体没有音轨")

    duration = media_duration(probe)

    evidence_dir = concert_dir / "run" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = concert_dir / "run" / "runtime"
    source_seek_check = assess_source_seek(ffmpeg, source, duration)
    review_media = source
    review_media_kind = "source"
    review_media_seek_check = source_seek_check
    if source_seek_check["status"] != "passed":
        review_media = runtime_dir / "source-preview.mp4"
        transcode_preview_proxy(ffmpeg, source, review_media)
        proxy_probe = probe_media(ffprobe, review_media)
        proxy_duration = media_duration(proxy_probe)
        if abs(proxy_duration - duration) > 0.5:
            raise ValueError(
                "预览代理与源片时长差超过0.5秒，不能作为同一时间轴审查媒体"
            )
        review_media_seek_check = assess_source_seek(
            ffmpeg,
            review_media,
            proxy_duration,
        )
        if review_media_seek_check["status"] != "passed":
            raise ValueError("预览代理随机访问复检仍失败，已停止建立工作包")
        review_media_kind = "transcoded_preview"

    audio_path = evidence_dir / "concert.flac"
    command = [
        str(ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "flac",
        str(audio_path),
    ]
    subprocess.run(command, check=True)

    report = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": VERSION,
        "created_at": utc_now(),
        "source_media": str(source),
        "source_fingerprint": file_fingerprint(source),
        "duration_seconds": duration,
        "video_stream": video_streams[0],
        "audio_path": str(audio_path.resolve()),
        "audio_stream": audio_streams[0],
        "source_seek_check": source_seek_check,
        "review_media": str(review_media.resolve()),
        "review_media_kind": review_media_kind,
        "review_media_fingerprint": file_fingerprint(review_media),
        "review_media_seek_check": review_media_seek_check,
        "ffmpeg": str(ffmpeg),
        "ffprobe": str(ffprobe),
    }
    write_json(evidence_dir / "preflight.json", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="预检演唱会媒体并提取独立FLAC")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--concert-dir", required=True, type=Path)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    console_version("concert_preflight")
    report = run_preflight(args.source, args.concert_dir, args.ffmpeg, args.ffprobe)
    print(f"audio={report['audio_path']}")
    print(f"duration={report['duration_seconds']:.3f}")
    print(f"preview_seek={report['review_media_seek_check']['status']}")
    print(f"review_media_kind={report['review_media_kind']}")
    print(f"review_media={report['review_media']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

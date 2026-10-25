"""Generate editor-compatible waveform data from the MV preview audio track."""
from __future__ import annotations

import array
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from common import preview_source, resolve_binary

SAMPLE_RATE = 8000
MAX_PEAKS = 500000
MAX_BYTES = 10 * 1024 * 1024


def media_timing(ffprobe, source):
    cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration,start_time:stream=codec_type,start_time",
           "-of", "json", str(source)]
    probe = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(probe.stdout)
    audio = next((stream for stream in info.get("streams", [])
                  if stream.get("codec_type") == "audio"), None)
    if audio is None:
        raise ValueError("媒体没有音轨")
    duration = float(info.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("无法取得有效媒体时长")
    offset = max(0.0, float(audio.get("start_time", 0)) -
                 float(info.get("format", {}).get("start_time", 0)))
    if not math.isfinite(offset) or offset >= duration:
        raise ValueError("音轨起点超出媒体时长")
    return duration, offset


def extract(ffmpeg, source, duration, offset, rate):
    count = min(MAX_PEAKS, max(2, math.ceil(duration * rate)))
    expected_samples = round(duration * SAMPLE_RATE)
    delay_ms = round(offset * 1000)
    audio_filter = "asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0"
    if delay_ms:
        audio_filter += f",adelay={delay_ms}:all=1"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(source),
           "-map", "0:a:0", "-vn", "-af", audio_filter,
           "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"]
    error_log = tempfile.TemporaryFile()
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=error_log, bufsize=1024 * 1024)
    except BaseException:
        error_log.close()
        raise
    peaks = []
    seen = 0
    try:
        for index in range(count):
            end = round((index + 1) * expected_samples / count)
            needed = max(0, end - seen)
            raw = process.stdout.read(needed * 2)
            samples = array.array("h")
            samples.frombytes(raw)
            if sys.byteorder != "little":
                samples.byteswap()
            peaks.append(max((abs(value) for value in samples), default=0))
            seen += len(samples)
            if len(samples) < needed:
                peaks.extend([0] * (count - len(peaks)))
                break
        # Consume any trailing audio to let FFmpeg exit normally. The video time base wins.
        while process.stdout.read(1024 * 1024):
            pass
        exit_code = process.wait()
        error_log.seek(0)
        error = error_log.read().decode("utf-8", errors="replace")
        if exit_code != 0:
            raise ValueError(f"FFmpeg解码失败：{error.strip()}")
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        process.stdout.close()
        error_log.close()
    maximum = max(peaks)
    normalized = [round(value / maximum, 4) for value in peaks] if maximum else [0] * count
    return normalized


def validate_waveform(path: Path, duration: float) -> None:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("波形超过编辑器10MB限制")
    value = json.loads(path.read_text(encoding="utf-8"))
    length = value.get("duration") if isinstance(value, dict) else None
    peaks = value.get("peaks") if isinstance(value, dict) else None
    if (isinstance(length, bool) or not isinstance(length, (int, float))
            or not math.isfinite(length) or abs(length - duration) > 0.001):
        raise ValueError("波形时长与预览视频不一致")
    if not isinstance(peaks, list) or not 2 <= len(peaks) <= MAX_PEAKS:
        raise ValueError("波形峰值数量无效")
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) or not 0 <= v <= 1 for v in peaks):
        raise ValueError("波形峰值必须为0至1的有限数值")


def generate_preview_waveform(mv_dir: Path, *, refresh: bool = False) -> Path:
    """Write the preview sidecar once; refresh only when explicitly requested."""
    source = preview_source(mv_dir)
    output = source.with_suffix(".waveform.json")
    ffmpeg, ffprobe = str(resolve_binary("ffmpeg")), str(resolve_binary("ffprobe"))
    duration, offset = media_timing(ffprobe, source)
    if output.exists() and not refresh:
        validate_waveform(output, duration)
        if output.stat().st_mtime_ns < source.stat().st_mtime_ns:
            raise ValueError("片源比波形更新，核对后使用waveform --refresh重新生成")
        return output
    peaks = extract(ffmpeg, source, duration, offset, 80)
    payload = json.dumps({"duration": duration, "peaks": peaks}, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise ValueError("输出超过编辑器10MB限制")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".waveform-", suffix=".tmp",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        validate_waveform(temporary, duration)
        if refresh:
            os.replace(temporary, output)
        else:
            os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output

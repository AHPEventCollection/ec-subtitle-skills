from __future__ import annotations

import ctypes
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any

from common import read_json, validate_filename_component
from gpu_queue import run_queued

ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")
CONFIG_NAME = ".subtitle-render.json"


def encoder_arguments(encoder: str, quality: int = 18, cpu_preset: str = "medium") -> list[str]:
    if not 0 <= quality <= 51:
        raise ValueError("质量值必须为0至51")
    options = {
        "h264_nvenc": ["-preset", "p5", "-rc", "vbr", "-cq", str(quality), "-b:v", "0"],
        "h264_qsv": ["-preset", "medium", "-global_quality", str(quality)],
        "h264_amf": ["-quality", "quality", "-rc", "cqp", "-qp_i", str(quality), "-qp_p", str(quality)],
        "libx264": ["-preset", cpu_preset, "-crf", str(quality)],
    }
    if encoder not in options:
        raise ValueError(f"不支持的编码器：{encoder}")
    return ["-c:v", encoder, *options[encoder], "-pix_fmt", "yuv420p"]


def queue_directory(concert_dir: Path) -> Path:
    for parent in (concert_dir, *concert_dir.parents):
        pointer = parent / ".subtitle-runtime.json"
        if pointer.is_file():
            runtime = Path(read_json(pointer)["runtime_root"])
            if not runtime.is_absolute():
                runtime = parent / runtime
            if not runtime.is_dir():
                raise FileNotFoundError(f"共享运行时不存在：{runtime}")
            return runtime.resolve() / "locks" / "concert-subtitle-gpu-queue"
    from gpu_queue import shared_queue_directory
    return shared_queue_directory()


def select_encoder(ffmpeg: Path, concert_dir: Path, requested: str = "auto",
                   quality: int = 18, cpu_preset: str = "medium") -> tuple[str, list[dict[str, Any]]]:
    if requested == "cpu":
        return "libx264", []
    candidates = ENCODERS if requested == "auto" else (requested,)
    if any(item not in ENCODERS for item in candidates):
        raise ValueError(f"不支持的编码选择：{requested}")
    probes = []
    for candidate in candidates:
        command = [str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-f", "lavfi", "-i", "color=black:s=1280x720:r=30:d=0.1",
                   "-frames:v", "3", *encoder_arguments(candidate, quality, cpu_preset),
                   "-f", "null", "-"]
        code = run_queued(concert_dir / "run" / "runtime", f"encoder-probe-{candidate}", command,
                          queue_dir_override=queue_directory(concert_dir))
        probes.append({"encoder": candidate, "returncode": code})
        if code == 0:
            return candidate, probes
    if requested != "auto":
        raise RuntimeError(f"指定的硬件编码器不可用：{requested}")
    print("硬件编码试运行均未通过，回退CPU/libx264")
    return "libx264", probes


def physical_disks(path: Path) -> set[int]:
    """Resolve the backing disk(s), not just the drive letter. Fail closed."""
    resolved = path.resolve()
    if os.name != "nt":
        raise RuntimeError("自动异物理盘检查目前仅支持Windows，请显式指定--output")
    drive = resolved.drive
    if len(drive) != 2 or drive[1] != ":":
        raise ValueError("无法确认网络路径或无盘符挂载的物理硬盘，请显式指定--output")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.DeviceIoControl.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW("\\\\.\\" + drive, 0, 3, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), f"无法查询物理磁盘：{drive}")

    class Extent(ctypes.Structure):
        _fields_ = [("disk", wintypes.DWORD), ("offset", ctypes.c_longlong), ("length", ctypes.c_longlong)]

    class Extents(ctypes.Structure):
        _fields_ = [("count", wintypes.DWORD), ("items", Extent * 128)]

    try:
        data, returned = Extents(), wintypes.DWORD()
        if not kernel.DeviceIoControl(handle, 0x00560000, None, 0, ctypes.byref(data),
                                      ctypes.sizeof(data), ctypes.byref(returned), None):
            raise OSError(ctypes.get_last_error(), f"无法确认物理磁盘：{drive}")
        if not 1 <= data.count <= 128:
            raise ValueError(f"物理磁盘信息异常：{drive}")
        return {int(data.items[i].disk) for i in range(data.count)}
    finally:
        kernel.CloseHandle(handle)


def default_destination(concert_dir: Path, source: Path, concert_id: str,
                        explicit: Path | None = None) -> Path:
    if explicit is not None:
        destination = explicit.expanduser().resolve()
    else:
        config_path = next((parent / CONFIG_NAME for parent in (concert_dir, *concert_dir.parents)
                            if (parent / CONFIG_NAME).is_file()), None)
        if config_path is None:
            raise ValueError("缺少.subtitle-render.json的output_root，请配置异盘输出目录或显式指定--output")
        configured = read_json(config_path).get("output_root")
        if not isinstance(configured, str) or not configured.strip() or not Path(configured).is_absolute():
            raise ValueError("output_root必须是已确认的绝对路径")
        safe_id = validate_filename_component(concert_id, "concert_id")
        destination = (Path(configured).expanduser() / safe_id / f"{safe_id}.hardsub.mp4").resolve()
        source_disks, output_disks = physical_disks(source), physical_disks(destination)
        if not source_disks or not output_disks or source_disks & output_disks:
            raise ValueError("默认输出与源片位于同一物理硬盘，请更换output_root")
    if destination == source.resolve():
        raise ValueError("输出路径不能覆盖源视频")
    if destination.suffix.lower() != ".mp4":
        raise ValueError("成片输出必须使用.mp4扩展名")
    if destination.exists():
        raise FileExistsError(f"输出已存在，拒绝覆盖：{destination}")
    return destination


def verify_encoded_media(ffmpeg: Path, output: Path, *, source: Path) -> dict[str, Any]:
    if not output.is_file() or output.stat().st_size == 0:
        raise ValueError("编码未生成有效输出文件")
    if not source.is_file() or source.stat().st_size == 0:
        raise ValueError("缺少用于核对成片的有效源视频")
    ffprobe = ffmpeg.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")

    def probe(path: Path) -> dict[str, Any]:
        result = subprocess.run(
            [str(ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            check=True, capture_output=True, text=True,
        )
        return json.loads(result.stdout)

    def duration(value: Any, label: str) -> float:
        try:
            seconds = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{label}时长非法") from error
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f"{label}时长非法")
        return seconds

    original = probe(source)
    media = probe(output)
    original_duration = duration(original.get("format", {}).get("duration"), "源视频")
    output_duration = duration(media.get("format", {}).get("duration"), "输出视频")
    streams = {kind: next((row for row in media.get("streams", [])
                           if row.get("codec_type") == kind), None) for kind in ("video", "audio")}
    if not streams["video"] or streams["video"].get("codec_name") != "h264":
        raise ValueError("输出缺少H.264视频流")
    if not streams["audio"]:
        raise ValueError("输出缺少音轨")
    tolerance = 0.25
    if abs(output_duration - original_duration) > tolerance:
        raise ValueError("成片时长与源视频差异超过0.25秒")
    stream_durations: dict[str, dict[str, float]] = {}
    for kind in ("video", "audio"):
        expected = next((row for row in original.get("streams", [])
                         if row.get("codec_type") == kind), None)
        if not expected:
            raise ValueError(f"源视频缺少{kind}流，无法核对成片")
        actual = streams[kind]
        if kind == "video":
            dimensions = (expected.get("width"), expected.get("height"))
            if not all(isinstance(value, int) and value > 0 for value in dimensions):
                raise ValueError("源视频尺寸非法")
            if (actual.get("width"), actual.get("height")) != dimensions:
                raise ValueError("成片画面尺寸与源视频不一致")
        expected_time = expected.get("duration")
        actual_time = actual.get("duration")
        expected_seconds = (original_duration if expected_time in (None, "N/A")
                            else duration(expected_time, f"源{kind}流"))
        actual_seconds = (output_duration if actual_time in (None, "N/A")
                          else duration(actual_time, f"输出{kind}流"))
        if abs(actual_seconds - expected_seconds) > tolerance:
            raise ValueError(f"成片{kind}流时长与源视频差异超过0.25秒")
        stream_durations[kind] = {"source": expected_seconds, "output": actual_seconds}
    decoded = subprocess.run(
        [str(ffmpeg), "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
         "-i", str(output), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if decoded.returncode or decoded.stderr.strip():
        raise ValueError("成片完整音视频解码失败：" + decoded.stderr.strip())
    media["verification"] = {
        "full_decode": "passed", "source_duration_seconds": original_duration,
        "duration_tolerance_seconds": tolerance, "stream_durations": stream_durations,
        "width": streams["video"]["width"], "height": streams["video"]["height"],
        "audio_codec": streams["audio"].get("codec_name"),
    }
    return media

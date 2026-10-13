from __future__ import annotations

import ctypes
import json
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


def verify_encoded_media(ffmpeg: Path, output: Path) -> dict[str, Any]:
    if not output.is_file() or output.stat().st_size == 0:
        raise ValueError("编码未生成有效输出文件")
    ffprobe = ffmpeg.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    result = subprocess.run([str(ffprobe), "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(output)], check=True, capture_output=True, text=True)
    media = json.loads(result.stdout)
    if not any(row.get("codec_type") == "video" and row.get("codec_name") == "h264"
               for row in media.get("streams", [])):
        raise ValueError("输出缺少H.264视频流")
    if float(media.get("format", {}).get("duration", 0)) <= 0:
        raise ValueError("输出视频时长非法")
    subprocess.run([str(ffmpeg), "-nostdin", "-v", "error", "-i", str(output),
                    "-t", "1", "-f", "null", "-"], check=True)
    return media

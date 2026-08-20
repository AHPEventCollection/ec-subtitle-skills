from __future__ import annotations

import configparser
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


VERSION = "2.8.0"
MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    video_codec: str
    frame_rate: str
    pixel_format: str
    color_space: str
    color_transfer: str
    color_primaries: str
    audio_codec: str | None
    audio_channels: int | None
    audio_sample_rate: int | None

    @property
    def hdr(self) -> bool:
        return self.color_transfer.casefold() in {"smpte2084", "arib-std-b67"}

    @property
    def play_res(self) -> tuple[int, int]:
        height = 1080
        width = max(2, round(height * self.width / self.height))
        if width % 2:
            width += 1
        return width, height


def project_root() -> Path:
    configured = os.environ.get("MV_SUBTITLE_PROJECT_ROOT")
    return Path(configured).expanduser().resolve() if configured else Path.cwd().resolve()


def workspace_root() -> Path:
    configured = os.environ.get("MV_SUBTITLE_WORKSPACE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return (project_root() / "workspace" / "mvs").resolve()


def validate_mv_dir(mv_dir: Path) -> Path:
    resolved = mv_dir.resolve()
    explicit_workspace = os.environ.get("MV_SUBTITLE_WORKSPACE_ROOT")
    explicit_project = os.environ.get("MV_SUBTITLE_PROJECT_ROOT")
    if explicit_workspace or explicit_project:
        expected = workspace_root()
        if resolved.parent != expected:
            raise ValueError(f"MV工作区必须直接位于：{expected}")
        return resolved
    parent = resolved.parent
    structured = parent.name.casefold() == "mvs" and parent.parent.name.casefold() == "workspace"
    if resolved.parent != workspace_root() and not structured:
        raise ValueError("MV工作区必须位于<项目根>/workspace/mvs/<mv-id>")
    return resolved


def project_root_for_mv(mv_dir: Path) -> Path:
    resolved = validate_mv_dir(mv_dir)
    if resolved.parent.name.casefold() == "mvs" and resolved.parent.parent.name.casefold() == "workspace":
        return resolved.parent.parent.parent
    return project_root()


def ensure_workspace(mv_dir: Path) -> Path:
    resolved = validate_mv_dir(mv_dir)
    resolved.mkdir(parents=True, exist_ok=True)
    marker = resolved / ".mv-workspace"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() != "music-video-subtitle":
        raise ValueError("工作区安全标记不属于music-video-subtitle")
    marker.write_text("music-video-subtitle\n", encoding="utf-8")
    for relative in ("source", "lyrics", "subtitle", "work", "review", "output"):
        (resolved / relative).mkdir(exist_ok=True)
    return resolved


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def read_required_text(path: Path, label: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"缺少{label}：{path}")
    value = path.read_text(encoding="utf-8-sig").strip()
    if not value:
        raise ValueError(f"{label}为空：{path}")
    return value


def resolve_binary(name: str, explicit: str | None = None) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"{name}不存在：{candidate}")
    environment_name = f"MV_SUBTITLE_{name.upper().replace('-', '_')}"
    environment = os.environ.get(environment_name)
    if environment:
        candidate = Path(environment).expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"{environment_name}指向不存在的文件")
    bundled = project_root() / "tool" / "bin" / f"{name}.exe"
    if bundled.is_file():
        return bundled.resolve()
    found = shutil.which(name)
    if found:
        return Path(found).resolve()
    raise FileNotFoundError(f"找不到{name}，请加入PATH或设置{environment_name}")


def find_source_video(mv_dir: Path) -> Path:
    source_dir = ensure_workspace(mv_dir) / "source"
    candidates = sorted(
        path.resolve()
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
    )
    if len(candidates) != 1:
        raise ValueError(f"source目录必须恰好包含一个视频，当前为{len(candidates)}个")
    return candidates[0]


def find_master_subtitle(mv_dir: Path) -> Path:
    subtitle_dir = ensure_workspace(mv_dir) / "subtitle"
    candidates = [
        path.resolve()
        for path in (
            subtitle_dir / "master.srt",
            subtitle_dir / "master.ass",
            subtitle_dir / "master.ssa",
        )
        if path.is_file()
    ]
    if len(candidates) != 1:
        raise ValueError("subtitle目录必须恰好包含一个master.srt、master.ass或master.ssa")
    return candidates[0]


def find_styled_ass(mv_dir: Path) -> Path:
    path = ensure_workspace(mv_dir) / "work" / f"{mv_dir.resolve().name}.styled.ass"
    if not path.is_file():
        raise FileNotFoundError(f"缺少标准样式ASS：{path}")
    return path


def _parse_ini_output(value: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(value)
    return parser


def probe_media(source: Path, ffprobe_value: str | None = None) -> MediaInfo:
    ffprobe = resolve_binary("ffprobe", ffprobe_value)
    completed = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration:"
                "stream=index,codec_type,codec_name,width,height,avg_frame_rate,"
                "pix_fmt,color_space,color_transfer,color_primaries,channels,sample_rate"
            ),
            "-of",
            "ini",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = _parse_ini_output(completed.stdout)
    video = next(
        (
            parsed[section]
            for section in parsed.sections()
            if section.startswith("streams.stream")
            and parsed[section].get("codec_type") == "video"
        ),
        None,
    )
    audio = next(
        (
            parsed[section]
            for section in parsed.sections()
            if section.startswith("streams.stream")
            and parsed[section].get("codec_type") == "audio"
        ),
        None,
    )
    if video is None or "format" not in parsed:
        raise ValueError(f"无法读取视频流：{source}")
    duration = float(parsed["format"].get("duration", "0"))
    width = int(video.get("width", "0"))
    height = int(video.get("height", "0"))
    if duration <= 0 or width <= 0 or height <= 0:
        raise ValueError(f"媒体时长或尺寸非法：{source}")
    return MediaInfo(
        duration=duration,
        width=width,
        height=height,
        video_codec=video.get("codec_name", "unknown"),
        frame_rate=video.get("avg_frame_rate", "unknown"),
        pixel_format=video.get("pix_fmt", "unknown"),
        color_space=video.get("color_space", "unknown"),
        color_transfer=video.get("color_transfer", "unknown"),
        color_primaries=video.get("color_primaries", "unknown"),
        audio_codec=audio.get("codec_name") if audio is not None else None,
        audio_channels=int(audio.get("channels", "0")) if audio is not None else None,
        audio_sample_rate=(
            int(audio.get("sample_rate", "0"))
            if audio is not None and audio.get("sample_rate")
            else None
        ),
    )


def media_summary(info: MediaInfo) -> list[str]:
    return [
        f"- 时长　{info.duration:.3f}秒",
        f"- 画面　{info.width}x{info.height}",
        f"- 视频编码　{info.video_codec}",
        f"- 帧率　{info.frame_rate}",
        f"- 像素格式　{info.pixel_format}",
        f"- 色彩　{info.color_space} / {info.color_transfer} / {info.color_primaries}",
        f"- 音频编码　{info.audio_codec or '无音轨'}",
        f"- 音频声道　{info.audio_channels or 0}",
        f"- 音频采样率　{info.audio_sample_rate or 0}",
    ]


def next_version(output_dir: Path, mv_id: str) -> int:
    versions: list[int] = []
    for path in output_dir.glob(f"{mv_id}.hardsub.v*.mp4"):
        try:
            versions.append(int(path.stem.rsplit("v", 1)[1]))
        except ValueError:
            continue
    return max(versions, default=0) + 1


def latest_version(output_dir: Path, mv_id: str) -> int:
    version = next_version(output_dir, mv_id) - 1
    if version < 1:
        raise FileNotFoundError("尚未生成硬字幕成品")
    return version

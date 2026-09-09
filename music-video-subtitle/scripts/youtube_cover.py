"""Prepare the same YouTube video's thumbnail without touching source media."""
from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from PIL import Image

from common import write_text


def youtube_video_id(mv_dir: Path) -> str | None:
    evidence = mv_dir / "review" / "source-acquisition.json"
    urls = []
    if evidence.is_file():
        urls.append(str(json.loads(evidence.read_text(encoding="utf-8-sig")).get("url", "")))
    source_note = mv_dir / "source" / "source.md"
    if source_note.is_file():
        urls.extend(re.findall(r"https?://[^\\s<>]+", source_note.read_text(encoding="utf-8-sig")))
    for url in urls:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        parts = parsed.path.strip("/").split("/")
        value = ""
        if host in {"youtu.be", "www.youtu.be"}:
            value = parts[0]
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            value = parse_qs(parsed.query).get("v", [""])[0]
            if not value and len(parts) == 2 and parts[0] in {"shorts", "embed", "live"}:
                value = parts[1]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
            return value
    return None


def prepared_youtube_cover(mv_dir: Path) -> Path | None:
    video_id = youtube_video_id(mv_dir)
    cover = mv_dir / "work" / "cover-source.jpg"
    proof = cover.with_suffix(".md")
    if not video_id or not cover.is_file() or not proof.is_file():
        return None
    if f"- YouTube视频ID　{video_id}\n" not in proof.read_text(encoding="utf-8"):
        return None
    if cover.stat().st_mtime_ns > proof.stat().st_mtime_ns:
        return None
    with Image.open(cover) as opened:
        return cover if min(opened.size) >= 720 else None


def prepare_youtube_cover(mv_dir: Path) -> Path | None:
    video_id = youtube_video_id(mv_dir)
    if not video_id:
        return None
    ready = prepared_youtube_cover(mv_dir)
    if ready:
        return ready
    candidates = []
    for path in (mv_dir / "source").glob("source.*"):
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        try:
            with Image.open(path) as opened:
                if min(opened.size) >= 720:
                    candidates.append((opened.width * opened.height, path))
        except OSError:
            continue
    if candidates:
        source = max(candidates, key=lambda item: item[0])[1]
        data = source.read_bytes()
        origin = str(source)
    else:
        origin = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
        with urlopen(origin, timeout=15) as response:
            data = response.read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024:
            raise ValueError("YouTube封面超过20MB")
    destination = mv_dir / "work" / "cover-source.jpg"
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(".cover-source.partial.jpg")
    try:
        with Image.open(io.BytesIO(data)) as opened:
            if min(opened.size) < 720:
                raise ValueError(f"YouTube封面尺寸不足：{opened.width}x{opened.height}，不放大或改用歌曲封面")
            size = opened.size
            opened.convert("RGB").save(partial, format="JPEG", quality=95, subsampling=0)
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    write_text(destination.with_suffix(".md"), "\n".join([
        "# YouTube视频封面", "", f"- YouTube视频ID　{video_id}",
        f"- 来源　{origin}", f"- 尺寸　{size[0]}x{size[1]}",
        "- 保留原构图与比例，只转换为JPEG；未从MV截图", "",
    ]))
    return destination

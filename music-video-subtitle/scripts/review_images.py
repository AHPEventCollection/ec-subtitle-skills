from __future__ import annotations

from pathlib import Path


def contact_sheet(frames: list[Path], destination: Path, columns: int = 2) -> Path:
    from PIL import Image, ImageDraw

    if not frames:
        raise ValueError("没有可供检查的抽帧")
    width, height, label = 720, 405, 28
    rows = (len(frames) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * width, rows * (height + label)), "#202020")
    draw = ImageDraw.Draw(sheet)
    for index, frame in enumerate(frames):
        x, y = (index % columns) * width, (index // columns) * (height + label)
        with Image.open(frame) as opened:
            thumb = opened.convert("RGB")
            thumb.thumbnail((width, height))
            sheet.paste(thumb, (x + (width - thumb.width) // 2, y))
        draw.text((x + 8, y + height + 5), frame.stem, fill="white")
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, quality=90)
    return destination

from __future__ import annotations

import shutil
import uuid
from pathlib import Path


def copy_delivery(source: Path, destination: Path) -> Path:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_dir():
        raise NotADirectoryError(f"交付源目录不存在：{source}")
    if destination.exists():
        raise FileExistsError(f"交付目标已存在，拒绝覆盖：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.parent / f".{destination.name}.partial-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source, partial, copy_function=shutil.copy2)
        partial.replace(destination)
    except Exception:
        if partial.exists():
            shutil.rmtree(partial)
        raise
    return destination

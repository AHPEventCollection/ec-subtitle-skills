from __future__ import annotations

import shutil
import hashlib
import uuid
from pathlib import Path


def _workspace_files(source: Path, destination: Path) -> dict[Path, Path]:
    if any(p.is_symlink() or getattr(p.lstat(), "st_reparse_tag", 0) == 0xA0000003
           for p in (source, *source.parents)):
        raise ValueError("源工作区路径包含符号链接或目录联接")
    source = source.resolve()
    destination = destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("归档与工作目录不得相同或互相包含")
    if source.parent.name != "mvs" or source.parent.parent.name != "workspace":
        raise ValueError("只允许归档workspace/mvs下的单支MV目录")
    if (source / ".mv-workspace").read_text(encoding="utf-8").strip() != "music-video-subtitle":
        raise ValueError("MV工作区标记不匹配")
    files = {}
    for path in source.rglob("*"):
        if path.is_symlink() or getattr(path.lstat(), "st_reparse_tag", 0) == 0xA0000003:
            raise ValueError(f"工作区包含链接，需先明确归档范围：{path}")
        if path.is_file():
            relative = path.relative_to(source)
            mapped = (relative.relative_to("output") if relative.parts[0] == "output"
                      else Path("工程") / relative)
            if mapped in files.values():
                raise ValueError(f"归档路径冲突：{mapped}")
            files[path] = mapped
    return files


def verify_workspace_archive(source: Path, destination: Path) -> Path:
    """只读核对全部文件；不以报告或上次成功代替本次回读。"""
    files = _workspace_files(source, destination)
    for path, relative in files.items():
        target = destination / relative
        if not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError(f"归档文件越界：{target}")
        with path.open("rb") as original, target.open("rb") as archived:
            if hashlib.file_digest(original, "sha256").digest() != hashlib.file_digest(archived, "sha256").digest():
                raise ValueError(f"归档内容不一致：{relative}")
    return destination.resolve()


def archive_workspace(source: Path, destination: Path) -> Path:
    """保留正式output及完整工程；清理由原生Shell单独执行。"""
    files = _workspace_files(source, destination)
    source, destination = source.resolve(), destination.resolve()
    if destination.exists():
        raise FileExistsError(f"归档目标已存在；用--verify-only核对，禁止覆盖：{destination}")
    destination.mkdir(parents=True)
    # 失败保留已复制内容和源目录，便于核查，不执行递归删除
    for path, relative in files.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return verify_workspace_archive(source, destination)


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

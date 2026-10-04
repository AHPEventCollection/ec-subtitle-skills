from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPORT_VERSION = 1
CHUNK_SIZE = 1024 * 1024


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path) -> list[dict[str, Any]]:
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"归档目录不存在：{root}")
    entries: list[dict[str, Any]] = []
    paths = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"归档目录不允许符号链接：{path}")
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": file_sha256(path),
                }
            )
    return entries


def manifest_sha256(entries: list[dict[str, Any]]) -> str:
    encoded = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _require_separate_trees(source: Path, destination: Path) -> None:
    if source == destination or _contains(source, destination) or _contains(destination, source):
        raise ValueError("归档源目录和目标目录不得相同或互相包含")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp-{uuid.uuid4().hex}"
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def archive_directory(source: Path, destination: Path, report: Path) -> Path:
    source = source.resolve()
    destination = destination.resolve()
    report = report.resolve()
    if not source.is_dir():
        raise NotADirectoryError(f"归档源目录不存在：{source}")
    if destination.exists():
        raise FileExistsError(f"归档目标已存在，拒绝覆盖：{destination}")
    _require_separate_trees(source, destination)
    expected = inventory(source)
    if not expected:
        raise ValueError("拒绝归档空目录")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.parent / f".{destination.name}.partial-{uuid.uuid4().hex}"
    destination_created = False
    try:
        shutil.copytree(source, partial, copy_function=shutil.copy2)
        if inventory(partial) != expected:
            raise OSError("临时归档副本与源目录的清单或SHA256不一致")
        partial.replace(destination)
        destination_created = True
        if inventory(destination) != expected:
            raise OSError("正式归档目录与源目录的清单或SHA256不一致")
        payload = {
            "report_version": REPORT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": str(source),
            "destination": str(destination),
            "file_count": len(expected),
            "manifest_sha256": manifest_sha256(expected),
            "entries": expected,
        }
        _write_json_atomic(report, payload)
        return report
    except Exception:
        if partial.exists():
            shutil.rmtree(partial)
        if destination_created and destination.exists():
            shutil.rmtree(destination)
        raise


def load_report(report: Path) -> dict[str, Any]:
    payload = json.loads(report.resolve().read_text(encoding="utf-8"))
    if payload.get("report_version") != REPORT_VERSION:
        raise ValueError("不支持的归档报告版本")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("归档报告没有有效文件清单")
    if payload.get("file_count") != len(entries):
        raise ValueError("归档报告文件数量与清单不一致")
    if payload.get("manifest_sha256") != manifest_sha256(entries):
        raise ValueError("归档报告清单摘要不一致")
    return payload


def verify_report(report: Path, target: Path | None = None) -> dict[str, Any]:
    payload = load_report(report)
    source = Path(payload["source"]).resolve()
    destination = Path(payload["destination"]).resolve()
    expected = payload["entries"]
    if target is not None and target.resolve() != source:
        raise ValueError(f"清理目标与归档报告源目录不一致：{target.resolve()} != {source}")
    if not destination.is_dir():
        raise NotADirectoryError(f"正式归档目录不存在：{destination}")
    if inventory(destination) != expected:
        raise ValueError("正式归档目录已变化，拒绝清理本地工作区")
    if source.exists() and inventory(source) != expected:
        raise ValueError("本地源目录在归档后已变化，拒绝清理")
    return payload


def cleanup_verified_source(report: Path, target: Path) -> Path:
    target = target.resolve()
    if not target.is_dir():
        raise NotADirectoryError(f"清理目标不存在：{target}")
    anchor = Path(target.anchor).resolve()
    if target == anchor or target == Path.home().resolve():
        raise ValueError(f"拒绝清理宽泛目录：{target}")
    verify_report(report, target)
    shutil.rmtree(target)
    if target.exists():
        raise OSError(f"清理后目录仍存在：{target}")
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="复制、SHA256复核并按报告清理项目工作区")
    subparsers = parser.add_subparsers(dest="command", required=True)
    archive = subparsers.add_parser("archive")
    archive.add_argument("--source", required=True, type=Path)
    archive.add_argument("--destination", required=True, type=Path)
    archive.add_argument("--report", required=True, type=Path)
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--report", required=True, type=Path)
    cleanup.add_argument("--target", required=True, type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--report", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "archive":
            result: Any = archive_directory(args.source, args.destination, args.report)
        elif args.command == "cleanup":
            result = cleanup_verified_source(args.report, args.target)
        else:
            result = verify_report(args.report)
    except Exception as error:
        print(f"error={error}", file=sys.stderr)
        return 2
    if isinstance(result, dict):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"result={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

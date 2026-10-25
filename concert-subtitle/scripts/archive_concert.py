from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from common import VERSION, console_version, read_json


DEFAULT_ARCHIVE_TOOL = Path(__file__).resolve().with_name("verified_archive.py")


def resolve_archive_tool(explicit: Path | None = None) -> Path:
    configured = explicit or (
        Path(os.environ["MEDIA_WORKFLOW_ARCHIVE_TOOL"])
        if os.environ.get("MEDIA_WORKFLOW_ARCHIVE_TOOL")
        else DEFAULT_ARCHIVE_TOOL
    )
    resolved = configured.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(
            f"找不到Skill内置归档工具：{resolved}；请确认Skill目录安装完整，"
            "或设置MEDIA_WORKFLOW_ARCHIVE_TOOL"
        )
    return resolved


def archive_concert(
    concert_dir: Path,
    destination: Path,
    report: Path | None = None,
    archive_tool: Path | None = None,
) -> Path:
    concert_dir = concert_dir.resolve()
    # Archive the operator-selected state, including unfinished projects. A
    # receipt only locates an external file; it is not an acceptance gate.
    external_files: list[Path] = []
    receipt_path = concert_dir / "run" / "evidence" / "render-receipt.json"
    if receipt_path.is_file():
        try:
            receipt = read_json(receipt_path)
            output = receipt.get("output") if isinstance(receipt, dict) else None
            if isinstance(output, str) and output.strip():
                hard_sub = Path(output)
                if not hard_sub.is_absolute():
                    hard_sub = concert_dir / hard_sub
                hard_sub = hard_sub.resolve()
                if hard_sub.is_file():
                    if not hard_sub.is_relative_to(concert_dir):
                        external_files.append(hard_sub)
                else:
                    print(f"归档说明：回执所指文件不存在，保留当前工程：{hard_sub}", file=sys.stderr)
        except (OSError, ValueError) as error:
            print(f"归档说明：压制回执无法读取，原文件随工程保存：{error}", file=sys.stderr)
    destination = destination.resolve()
    report_path = (
        report.resolve()
        if report
        else destination.parent / f"{destination.name}.archive-verification.json"
    )
    tool = resolve_archive_tool(archive_tool)
    subprocess.run(
        [
            sys.executable,
            str(tool),
            "archive",
            "--source",
            str(concert_dir),
            "--destination",
            str(destination),
            "--report",
            str(report_path),
            *[argument for file in external_files for argument in ("--include", str(file))],
        ],
        check=True,
    )
    return report_path


def cleanup_concert(
    concert_dir: Path,
    report: Path,
    archive_tool: Path | None = None,
) -> Path:
    concert_dir = concert_dir.resolve()
    tool = resolve_archive_tool(archive_tool)
    subprocess.run(
        [
            sys.executable,
            str(tool),
            "cleanup",
            "--report",
            str(report.resolve()),
            "--target",
            str(concert_dir),
        ],
        check=True,
    )
    return concert_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按操作人员指令归档当前演唱会工程并核验副本")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="action", required=True)
    archive_parser = subparsers.add_parser("archive")
    archive_parser.add_argument("--concert-dir", required=True, type=Path)
    archive_parser.add_argument("--destination", required=True, type=Path)
    archive_parser.add_argument("--report", type=Path)
    archive_parser.add_argument("--archive-tool", type=Path)
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--concert-dir", required=True, type=Path)
    cleanup_parser.add_argument("--report", required=True, type=Path)
    cleanup_parser.add_argument("--archive-tool", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console_version("archive_concert")
    try:
        if args.action == "archive":
            result = archive_concert(
                args.concert_dir,
                args.destination,
                args.report,
                args.archive_tool,
            )
        else:
            result = cleanup_concert(
                args.concert_dir,
                args.report,
                args.archive_tool,
            )
    except Exception as error:
        print(f"error={error}", file=sys.stderr)
        return 2
    print(f"result={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

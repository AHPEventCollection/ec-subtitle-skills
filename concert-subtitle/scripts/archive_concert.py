from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from common import VERSION, console_version, load_manifest
from validate_concert import validate_concert


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
    gate = validate_concert(concert_dir)
    if gate["result"] != "passed":
        codes = ",".join(item.get("code", "unknown") for item in gate["failures"])
        raise ValueError(f"演唱会全局验收未通过：{codes}")
    manifest = load_manifest(concert_dir)
    hard_sub = concert_dir / "output" / f"{manifest['concert_id']}.hardsub.mp4"
    if not hard_sub.is_file():
        raise FileNotFoundError(f"缺少终版硬字幕视频：{hard_sub}")
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
    parser = argparse.ArgumentParser(description="验收、可靠归档并清理演唱会工作区")
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

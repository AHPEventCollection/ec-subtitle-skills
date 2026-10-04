from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_work_packages import build_packages
from common import (
    VERSION,
    console_version,
    load_manifest,
    manifest_path,
    read_json,
    section_directory,
)
from concert_preflight import run_preflight
from index_concert import create_manifest


def prepare(args: argparse.Namespace) -> int:
    existing_manifest = manifest_path(args.concert_dir)
    if existing_manifest.exists() and not args.overwrite:
        raise FileExistsError(
            f"Manifest已存在，未执行预检或覆盖：{existing_manifest}"
        )
    report = run_preflight(
        args.source,
        args.concert_dir,
        args.ffmpeg,
        args.ffprobe,
    )
    create_manifest(
        args.concert_dir,
        args.source,
        Path(report["audio_path"]),
        args.sections,
        args.concert_id,
        float(report["duration_seconds"]),
        args.context_seconds,
        args.overwrite,
        args.setlist,
        args.netease_cookie_file,
    )
    summaries = build_packages(
        args.concert_dir,
        ffmpeg_value=args.ffmpeg,
        force=args.overwrite,
    )
    print(f"concert_dir={args.concert_dir.resolve()}")
    print(f"preview_seek={report['review_media_seek_check']['status']}")
    print(f"review_media_kind={report['review_media_kind']}")
    print(f"review_media={report['review_media']}")
    print(f"sections={len(summaries)}")
    return 0


def status(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.concert_dir)
    rows = []
    for section in manifest["sections"]:
        status_path = section_directory(args.concert_dir, section["id"]) / "status.json"
        section_status = read_json(status_path) if status_path.is_file() else {}
        rows.append(
            {
                "id": section["id"],
                "type": section["type"],
                "title": section.get("title", ""),
                "status": section_status.get("status", "missing"),
                "reason": section_status.get("reason", ""),
            }
        )
    if args.json:
        print(json.dumps({"concert_id": manifest["concert_id"], "sections": rows}, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(
                f"{row['id']}\t{row['type']}\t{row['status']}\t"
                f"{row['title']}\t{row['reason']}"
            )
        counts = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        print("summary=" + ",".join(f"{key}:{value}" for key, value in sorted(counts.items())))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="演唱会字幕主编排入口")
    subparsers = parser.add_subparsers(dest="action", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--concert-dir", required=True, type=Path)
    prepare_parser.add_argument("--source", required=True, type=Path)
    prepare_parser.add_argument("--sections", required=True, type=Path)
    prepare_parser.add_argument("--concert-id")
    prepare_parser.add_argument("--setlist", type=Path)
    prepare_parser.add_argument("--netease-cookie-file", type=Path)
    prepare_parser.add_argument("--context-seconds", type=float, default=20.0)
    prepare_parser.add_argument("--ffmpeg")
    prepare_parser.add_argument("--ffprobe")
    prepare_parser.add_argument("--overwrite", action="store_true")
    prepare_parser.set_defaults(handler=prepare)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--concert-dir", required=True, type=Path)
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(handler=status)

    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    console_version("concert_pipeline")
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())

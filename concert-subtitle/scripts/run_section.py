from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from common import (
    VERSION,
    archive_section_results,
    console_version,
    ensure_audio_path,
    package_input_hash,
    read_json,
    update_status,
)
from gpu_queue import run_queued
from validate_section import validate_section


def _expand_command(command: list[str], section_dir: Path) -> list[str]:
    package = read_json(section_dir / "input.json")
    replacements = {
        "section_dir": str(section_dir),
        "audio": str(ensure_audio_path(section_dir / package["audio_path"])),
        "input": str(section_dir / "input.json"),
        "events": str(section_dir / "events.json"),
        "report": str(section_dir / "report.json"),
    }
    return [
        argument.format_map(replacements)
        for argument in command
    ]


def run_section(
    section_dir: Path,
    command: list[str],
    use_gpu: bool,
    runtime_dir: Path | None,
    gpu_timeout: float,
    force: bool = False,
) -> int:
    section_dir = section_dir.resolve()
    package = read_json(section_dir / "input.json")
    if package.get("input_hash") != package_input_hash(package):
        raise ValueError("input.json内容与input_hash不一致，请重建工作包")
    status_path = section_dir / "status.json"
    current_status = read_json(status_path).get("status") if status_path.is_file() else None
    if current_status == "approved" and not force:
        raise ValueError("该分段已经批准，拒绝重复执行；确需重跑时使用--force")
    if force:
        archive_section_results(
            section_dir,
            "forced-rerun",
            [section_dir.parents[1] / "review" / package["section_id"]],
        )
        update_status(
            section_dir,
            "stale",
            "forced_rerun",
            section_id=package["section_id"],
            input_hash=package["input_hash"],
        )
    audio = ensure_audio_path(section_dir / package["audio_path"])
    if package.get("gpu_required", True) and not use_gpu:
        raise ValueError(
            "该工作包要求GPU，必须使用--gpu并通过机器级GPU队列执行"
        )
    command = _expand_command(command, section_dir)
    if not command:
        raise ValueError("缺少分段执行命令")

    environment = os.environ.copy()
    environment.update(
        {
            "CONCERT_SUBTITLE_SECTION_DIR": str(section_dir),
            "CONCERT_SUBTITLE_AUDIO": str(audio),
            "CONCERT_SUBTITLE_INPUT": str(section_dir / "input.json"),
            "CONCERT_SUBTITLE_EVENTS": str(section_dir / "events.json"),
            "CONCERT_SUBTITLE_REPORT": str(section_dir / "report.json"),
        }
    )
    update_status(
        section_dir,
        "queued" if use_gpu else "running",
        "section_command_queued" if use_gpu else "section_command_started",
        section_id=package["section_id"],
        input_hash=package["input_hash"],
    )

    try:
        if use_gpu:
            if runtime_dir is None:
                raise ValueError("--gpu必须同时提供--runtime-dir")

            def mark_running() -> None:
                update_status(
                    section_dir,
                    "running",
                    "gpu_slot_acquired",
                    section_id=package["section_id"],
                    input_hash=package["input_hash"],
                )

            returncode = run_queued(
                runtime_dir,
                package["section_id"],
                command,
                timeout=gpu_timeout,
                environment=environment,
                on_acquired=mark_running,
            )
        else:
            returncode = subprocess.run(command, env=environment).returncode
    except Exception as error:
        update_status(
            section_dir,
            "failed",
            "section_command_exception",
            section_id=package["section_id"],
            input_hash=package["input_hash"],
            error_type=type(error).__name__,
        )
        raise

    if returncode != 0:
        update_status(
            section_dir,
            "failed",
            "section_command_failed",
            section_id=package["section_id"],
            input_hash=package["input_hash"],
            returncode=returncode,
        )
        return returncode

    try:
        gate = validate_section(section_dir, update=True)
    except Exception:
        update_status(
            section_dir,
            "failed",
            "section_output_validation_error",
            section_id=package["section_id"],
            input_hash=package["input_hash"],
        )
        raise
    return 2 if gate["automatic_result"] == "failed" else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="执行单个分段并自动运行gate")
    parser.add_argument("--section-dir", required=True, type=Path)
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--gpu-timeout", type=float, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    console_version("run_section")
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    return run_section(
        args.section_dir,
        command,
        args.gpu,
        args.runtime_dir,
        args.gpu_timeout,
        args.force,
    )


if __name__ == "__main__":
    raise SystemExit(main())

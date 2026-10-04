from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable, Sequence

from common import (
    SCHEMA_VERSION,
    VERSION,
    console_version,
    find_project_root,
    utc_now,
    write_json,
)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_ticket(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _unlink_with_retry(path: Path, attempts: int = 5, delay: float = 0.05) -> None:
    for attempt in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt + 1 == attempts:
                raise
            time.sleep(delay)


def _cleanup_stale(queue_dir: Path, stale_after: float) -> None:
    now = time.time()
    for ticket in queue_dir.glob("*.ticket.json"):
        data = _read_ticket(ticket)
        created = float(data.get("created_epoch", 0))
        pid = int(data.get("pid", 0))
        if now - created > stale_after and not _pid_alive(pid):
            _unlink_with_retry(ticket)

    lock = queue_dir / "gpu.lock"
    if lock.is_file():
        data = _read_ticket(lock)
        created = float(data.get("created_epoch", 0))
        pid = int(data.get("pid", 0))
        if now - created > stale_after and not _pid_alive(pid):
            _unlink_with_retry(lock)


def shared_queue_directory(override: Path | None = None) -> Path:
    if override:
        return override.resolve()
    environment_value = os.environ.get("CONCERT_SUBTITLE_GPU_QUEUE_DIR")
    if environment_value:
        return Path(environment_value).expanduser().resolve()

    runtime_root = os.environ.get("SUBTITLE_RUNTIME_ROOT")
    if runtime_root:
        return (
            Path(runtime_root).expanduser().resolve()
            / "locks"
            / "concert-subtitle-gpu-queue"
        )

    project_root = find_project_root()
    runtime_pointer = project_root / ".subtitle-runtime.json"
    if runtime_pointer.is_file():
        pointer = _read_ticket(runtime_pointer)
        configured_root = pointer.get("runtime_root")
        if isinstance(configured_root, str) and configured_root.strip():
            return (
                Path(configured_root).expanduser().resolve()
                / "locks"
                / "concert-subtitle-gpu-queue"
            )

    raise RuntimeError(
        "未绑定机器级GPU队列目录：请先设置项目.subtitle-runtime.json、"
        "SUBTITLE_RUNTIME_ROOT或CONCERT_SUBTITLE_GPU_QUEUE_DIR"
    )


def run_queued(
    runtime_dir: Path,
    task_id: str,
    command: Sequence[str],
    timeout: float = 0,
    poll_interval: float = 1.0,
    stale_after: float = 21_600,
    environment: dict[str, str] | None = None,
    on_acquired: Callable[[], None] | None = None,
    queue_dir_override: Path | None = None,
) -> int:
    if not command:
        raise ValueError("GPU队列缺少执行命令")
    queue_dir = shared_queue_directory(queue_dir_override)
    queue_dir.mkdir(parents=True, exist_ok=True)
    local_log_dir = runtime_dir.resolve() / "gpu-runs"
    local_log_dir.mkdir(parents=True, exist_ok=True)
    created_epoch = time.time()
    ticket_name = f"{time.time_ns():020d}-{uuid.uuid4().hex}.ticket.json"
    ticket_path = queue_dir / ticket_name
    ticket_data = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": VERSION,
        "task_id": task_id,
        "pid": os.getpid(),
        "created_at": utc_now(),
        "created_epoch": created_epoch,
        "command": list(command),
    }
    write_json(ticket_path, ticket_data)
    lock_path = queue_dir / "gpu.lock"
    acquired = False

    try:
        while True:
            _cleanup_stale(queue_dir, stale_after)
            tickets = sorted(queue_dir.glob("*.ticket.json"), key=lambda item: item.name)
            is_first = tickets and tickets[0].name == ticket_path.name
            if is_first:
                try:
                    descriptor = os.open(
                        lock_path,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    )
                    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                        json.dump(ticket_data, handle, ensure_ascii=False, indent=2)
                    acquired = True
                    break
                except FileExistsError:
                    pass
            if timeout and time.time() - created_epoch > timeout:
                raise TimeoutError(f"GPU队列等待超时：{task_id}")
            time.sleep(max(0.1, poll_interval))

        write_json(
            local_log_dir / f"{task_id}.running.json",
            {
                **ticket_data,
                "started_at": utc_now(),
                "status": "running",
            },
        )
        if on_acquired:
            on_acquired()
        result = subprocess.run(list(command), env=environment)
        write_json(
            local_log_dir / f"{task_id}.last.json",
            {
                **ticket_data,
                "finished_at": utc_now(),
                "status": "completed" if result.returncode == 0 else "failed",
                "returncode": result.returncode,
            },
        )
        return result.returncode
    finally:
        _unlink_with_retry(ticket_path)
        _unlink_with_retry(local_log_dir / f"{task_id}.running.json")
        if acquired:
            _unlink_with_retry(lock_path)


def queue_status(
    runtime_dir: Path,
    queue_dir_override: Path | None = None,
) -> dict:
    queue_dir = shared_queue_directory(queue_dir_override)
    queue_dir.mkdir(parents=True, exist_ok=True)
    lock_path = queue_dir / "gpu.lock"
    return {
        "shared_queue_dir": str(queue_dir),
        "local_log_dir": str((runtime_dir.resolve() / "gpu-runs")),
        "locked": lock_path.is_file(),
        "lock": _read_ticket(lock_path) if lock_path.is_file() else None,
        "waiting": [
            _read_ticket(path)
            for path in sorted(queue_dir.glob("*.ticket.json"), key=lambda item: item.name)
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="串行执行本地GPU任务")
    subparsers = parser.add_subparsers(dest="action", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--runtime-dir", required=True, type=Path)
    run_parser.add_argument("--task-id", required=True)
    run_parser.add_argument("--timeout", type=float, default=0)
    run_parser.add_argument("--stale-after", type=float, default=21_600)
    run_parser.add_argument("command", nargs=argparse.REMAINDER)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--runtime-dir", required=True, type=Path)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    console_version("gpu_queue")
    if args.action == "status":
        print(json.dumps(queue_status(args.runtime_dir), ensure_ascii=False, indent=2))
        return 0
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    return run_queued(
        args.runtime_dir,
        args.task_id,
        command,
        args.timeout,
        stale_after=args.stale_after,
    )


if __name__ == "__main__":
    raise SystemExit(main())

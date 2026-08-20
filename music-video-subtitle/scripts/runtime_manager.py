from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

VERSION = "1.2.0"
RUNTIME_SCHEMA = "ec-subtitle-runtime/v1"
REQUIREMENTS_SCHEMA = "ec-subtitle-runtime-requirements/v1"
POINTER_NAME = ".subtitle-runtime.json"
MANIFEST_NAME = "runtime.json"
EVERYTHING_URL = "http://127.0.0.1:39080"
SKILL_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_FILE = SKILL_ROOT / "runtime-requirements.json"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def load_requirements(path: Path = REQUIREMENTS_FILE) -> dict[str, Any]:
    value = load_json(path)
    if value.get("schema") != REQUIREMENTS_SCHEMA:
        raise ValueError(f"Unsupported requirements schema: {value.get('schema')!r}")
    return value


def resolve_relative(root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def pointer_roots(start: Path) -> list[Path]:
    roots: list[Path] = []
    current = start.resolve()
    for directory in (current, *current.parents):
        pointer = directory / POINTER_NAME
        if not pointer.is_file():
            continue
        try:
            configured = load_json(pointer).get("runtime_root")
            resolved = resolve_relative(directory, configured)
            if resolved:
                roots.append(resolved)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return roots


def everything_roots() -> list[Path]:
    if os.name != "nt":
        return []
    query = urllib.parse.urlencode(
        {
            "search": MANIFEST_NAME,
            "json": "1",
            "path_column": "1",
            "size_column": "0",
            "date_modified_column": "0",
            "count": "100",
        }
    )
    try:
        with urllib.request.urlopen(f"{EVERYTHING_URL}/?{query}", timeout=1.0) as response:
            payload = json.load(response)
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    roots: list[Path] = []
    for result in payload.get("results", []):
        if result.get("name", "").lower() != MANIFEST_NAME:
            continue
        path = Path(result.get("path", ""))
        if path:
            roots.append(path.resolve())
    return roots


def preferred_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "ec-subtitle" / "runtime"
    return Path.home() / ".local" / "share" / "ec-subtitle" / "runtime"


def validate_runtime_root(root: Path) -> tuple[bool, str | None]:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        return False, f"missing {MANIFEST_NAME}"
    try:
        manifest = load_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return False, str(error)
    if manifest.get("schema") != RUNTIME_SCHEMA:
        return False, f"unsupported schema {manifest.get('schema')!r}"
    return True, None


def discover_roots(
    explicit: str | None = None,
    project_root: str | None = None,
    include_everything: bool = True,
) -> list[dict[str, Any]]:
    raw: list[tuple[str, Path]] = []
    if explicit:
        raw.append(("explicit", Path(explicit).expanduser().resolve()))
    configured = os.environ.get("SUBTITLE_RUNTIME_ROOT")
    if configured:
        raw.append(("environment", Path(configured).expanduser().resolve()))
    start = Path(project_root).expanduser() if project_root else Path.cwd()
    raw.extend(("project-pointer", path) for path in pointer_roots(start))

    seen: set[str] = set()
    results: list[dict[str, Any]] = []

    def append_candidates(candidates: list[tuple[str, Path]]) -> None:
        for source, root in candidates:
            key = os.path.normcase(str(root))
            if key in seen:
                continue
            seen.add(key)
            valid, error = validate_runtime_root(root)
            results.append({"source": source, "root": str(root), "valid": valid, "error": error})

    append_candidates(raw)
    if include_everything and not any(item["valid"] for item in results):
        append_candidates([("everything", path) for path in everything_roots()])
    return results


def choose_runtime_root(explicit: str | None, project_root: str | None) -> tuple[Path | None, list[dict[str, Any]]]:
    candidates = discover_roots(explicit, project_root)
    if explicit:
        selected = Path(explicit).expanduser().resolve()
        valid = any(item["valid"] and Path(item["root"]) == selected for item in candidates)
        return (selected if valid else None), candidates
    for source in ("environment", "project-pointer", "everything"):
        roots = []
        for item in candidates:
            root = Path(item["root"])
            if item["valid"] and item["source"] == source and root not in roots:
                roots.append(root)
        if len(roots) == 1:
            return roots[0], candidates
        if len(roots) > 1:
            return None, candidates
    return None, candidates


def executable_path(root: Path, manifest: dict[str, Any], name: str) -> str | None:
    shared = manifest.get("shared", {})
    bin_dir = resolve_relative(root, shared.get("bin"))
    suffixes = (".exe", "") if os.name == "nt" else ("",)
    if bin_dir:
        for suffix in suffixes:
            candidate = bin_dir / f"{name}{suffix}"
            if candidate.is_file():
                return str(candidate)
    return shutil.which(name)


def profile_environment(root: Path, manifest: dict[str, Any], profile_name: str) -> dict[str, str]:
    profile = manifest.get("profiles", {}).get(profile_name, {})
    shared = manifest.get("shared", {})
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env["SUBTITLE_RUNTIME_ROOT"] = str(root)
    env["SUBTITLE_RUNTIME_PROFILE"] = profile_name

    python_deps = resolve_relative(root, profile.get("python_deps"))
    if python_deps and python_deps.is_dir():
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = os.pathsep.join([str(python_deps), *([existing] if existing else [])])

    path_parts: list[str] = []
    for key in ("cuda", "bin"):
        value = resolve_relative(root, shared.get(key))
        if value and value.is_dir():
            path_parts.append(str(value))
    javascript = javascript_runtime(root, manifest)
    if javascript:
        path_parts.append(str(Path(javascript["executable"]).parent))
        env["SUBTITLE_RUNTIME_JS_KIND"] = javascript["kind"]
        env["SUBTITLE_RUNTIME_JS_EXECUTABLE"] = javascript["executable"]
    if path_parts:
        env["PATH"] = os.pathsep.join([*path_parts, env.get("PATH", "")])

    artifacts = profile.get("artifacts", {})
    environment_keys = {
        "whisper_model": "SUBTITLE_RUNTIME_WHISPER_MODEL",
        "sofa_model": "SUBTITLE_RUNTIME_SOFA_MODEL",
        "sofa_tool": "SUBTITLE_RUNTIME_SOFA_TOOL",
        "separator_model": "SUBTITLE_RUNTIME_SEPARATOR_MODEL",
    }
    for key, environment_name in environment_keys.items():
        value = resolve_relative(root, artifacts.get(key))
        if value:
            env[environment_name] = str(value)
    bin_dir = resolve_relative(root, shared.get("bin"))
    cuda_dir = resolve_relative(root, shared.get("cuda"))
    if bin_dir:
        env["SUBTITLE_RUNTIME_FFMPEG_DIR"] = str(bin_dir)
    if cuda_dir:
        env["SUBTITLE_RUNTIME_CUDA_DIR"] = str(cuda_dir)
    return env


def python_probe(python: Path, imports: list[str], env: dict[str, str]) -> dict[str, Any]:
    if not python.is_file():
        return {"python_exists": False, "version": None, "imports": {name: False for name in imports}}
    code = (
        "import importlib.util,json,sys;"
        f"mods={imports!r};"
        "print(json.dumps({'version':list(sys.version_info[:3]),'imports':{m:bool(importlib.util.find_spec(m)) for m in mods}}))"
    )
    try:
        completed = subprocess.run(
            [str(python), "-c", code],
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        result = json.loads(completed.stdout.strip())
        result["python_exists"] = True
        return result
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as error:
        return {
            "python_exists": True,
            "version": None,
            "imports": {name: False for name in imports},
            "probe_error": str(error),
        }


def command_probe(command: list[str], env: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=60,
        )
        output = (completed.stdout or completed.stderr).splitlines()
        return {"ok": True, "version_line": output[0].strip() if output else ""}
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "error": str(error)}


def javascript_runtime(root: Path, manifest: dict[str, Any]) -> dict[str, str] | None:
    configured = manifest.get("shared", {}).get("javascript")
    if isinstance(configured, dict):
        kind = str(configured.get("kind", "")).casefold()
        executable = resolve_relative(root, configured.get("executable"))
        if kind and executable and executable.is_file():
            return {"kind": kind, "executable": str(executable)}
    for kind in ("deno", "node"):
        executable = shutil.which(kind)
        if executable:
            return {"kind": kind, "executable": str(Path(executable).resolve())}
    return None


def _version_tuple(value: str) -> tuple[int, int] | None:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)", value)
    return (int(match.group(1)), int(match.group(2))) if match else None


def python_json_probe(python: Path, code: str, env: dict[str, str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [str(python), "-c", code],
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=120,
        )
        return {"ok": True, **json.loads(completed.stdout.strip())}
    except subprocess.CalledProcessError as error:
        return {
            "ok": False,
            "error": (error.stderr or error.stdout or str(error)).strip(),
        }
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as error:
        return {"ok": False, "error": str(error)}


def component_probes(
    root: Path,
    manifest: dict[str, Any],
    profiles: list[str],
    executables: dict[str, str | None],
    javascript: dict[str, str] | None = None,
    requirements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    components: dict[str, Any] = {}
    for name, resolved in executables.items():
        if not resolved:
            components[name] = {"ok": False, "error": "executable not resolved"}
            continue
        if name == "javascript":
            probe = command_probe([resolved, "--version"])
            kind = javascript.get("kind") if javascript else None
            version = _version_tuple(probe.get("version_line", ""))
            minimum = (
                (requirements or {}).get("javascript", {})
                .get("accepted", {})
                .get(kind or "", {})
                .get("minimum")
            )
            version_ok = bool(version and minimum and version >= tuple(minimum[:2]))
            probe.update({"kind": kind, "version": list(version) if version else None, "version_ok": version_ok})
            if probe.get("ok") and not version_ok:
                probe["ok"] = False
                probe["error"] = f"unsupported JavaScript runtime {kind} {version}"
            components[name] = probe
            continue
        components[name] = command_probe([resolved, "-version"])

    if "separator" in profiles:
        profile = manifest.get("profiles", {}).get("separator", {})
        python = resolve_relative(root, profile.get("python"))
        if python:
            code = (
                "import json,torch;"
                "print(json.dumps({'cuda_available':torch.cuda.is_available(),"
                "'device_count':torch.cuda.device_count(),"
                "'torch_cuda':torch.version.cuda}))"
            )
            components["separator_cuda"] = python_json_probe(
                python, code, profile_environment(root, manifest, "separator")
            )
        else:
            components["separator_cuda"] = {"ok": False, "error": "separator python not resolved"}

    if "sofa" in profiles:
        profile = manifest.get("profiles", {}).get("sofa", {})
        python = resolve_relative(root, profile.get("python"))
        if python:
            code = """import json
import os
import onnxruntime as ort

model = os.environ["SUBTITLE_RUNTIME_SOFA_MODEL"]
available = ort.get_available_providers()
cuda_error = None
try:
    session = ort.InferenceSession(
        model, providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
    )
except Exception as error:
    cuda_error = repr(error)
    session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
print(json.dumps({
    "available_providers": available,
    "session_providers": session.get_providers(),
    "cuda_error": cuda_error,
}))
"""
            components["sofa_onnx"] = python_json_probe(
                python, code, profile_environment(root, manifest, "sofa")
            )
        else:
            components["sofa_onnx"] = {"ok": False, "error": "sofa python not resolved"}
    return components


def component_status(components: dict[str, Any]) -> tuple[str, list[str]]:
    issues: list[str] = []
    blocked = False
    degraded = False
    for name, probe in components.items():
        if not probe.get("ok"):
            blocked = True
            issues.append(f"{name}: {probe.get('error', 'probe failed')}")
            continue
        if name == "separator_cuda" and not probe.get("cuda_available"):
            degraded = True
            issues.append("separator_cuda: CUDA unavailable, CPU fallback only")
        if name == "sofa_onnx":
            if probe.get("cuda_error"):
                degraded = True
                issues.append(f"sofa_onnx: CUDA initialization failed: {probe['cuda_error']}")
            elif "CUDAExecutionProvider" not in probe.get("session_providers", []):
                degraded = True
                issues.append("sofa_onnx: CUDA provider not active, CPU fallback only")
    if blocked:
        return "BLOCKED", issues
    if degraded:
        return "DEGRADED", issues
    return "READY", issues


def version_in_range(version: list[int] | None, minimum: list[int], maximum_exclusive: list[int]) -> bool:
    if not version:
        return False
    value = tuple(version[:2])
    return tuple(minimum) <= value < tuple(maximum_exclusive)


def selected_task(requirements: dict[str, Any], task_name: str) -> dict[str, Any]:
    tasks = requirements.get("tasks", {})
    if task_name not in tasks:
        raise ValueError(f"Unknown runtime task {task_name!r}; choose from {', '.join(sorted(tasks))}")
    task = tasks[task_name]
    return {
        "profiles": list(task.get("profiles", [])),
        "executables": list(dict.fromkeys([*requirements.get("executables", []), *task.get("executables", [])])),
    }


def doctor_state(
    task_name: str,
    runtime_root: str | None = None,
    project_root: str | None = None,
    only_profile: str | None = None,
) -> dict[str, Any]:
    requirements = load_requirements()
    task = selected_task(requirements, task_name)
    if only_profile:
        if only_profile not in requirements.get("profiles", {}):
            raise ValueError(f"Unknown runtime profile {only_profile!r}")
        task["profiles"] = [only_profile]
    root, candidates = choose_runtime_root(runtime_root, project_root)
    state: dict[str, Any] = {
        "manager_version": VERSION,
        "skill": requirements.get("skill"),
        "task": task_name,
        "runtime_root": str(root) if root else None,
        "requested_runtime_root": str(Path(runtime_root).expanduser().resolve()) if runtime_root else None,
        "preferred_install_root": str(preferred_root()),
        "candidates": candidates,
        "ambiguous": root is None and sum(1 for item in candidates if item["valid"]) > 1,
        "profiles": {},
        "executables": {},
        "ready": False,
    }
    if root is None:
        return state

    manifest = load_json(root / MANIFEST_NAME)
    profiles_ready = True
    for profile_name in task["profiles"]:
        expected = requirements["profiles"][profile_name]
        actual = manifest.get("profiles", {}).get(profile_name, {})
        python = resolve_relative(root, actual.get("python"))
        imports = list(expected.get("imports", []))
        probe = python_probe(python or Path(""), imports, profile_environment(root, manifest, profile_name))
        version_ok = version_in_range(
            probe.get("version"),
            list(expected.get("python_min", [3, 10])),
            list(expected.get("python_max_exclusive", [3, 15])),
        )
        artifacts: dict[str, Any] = {}
        artifacts_ready = True
        for artifact in expected.get("artifacts", []):
            key = artifact["key"]
            path = resolve_relative(root, actual.get("artifacts", {}).get(key))
            kind = artifact.get("kind", "file")
            exists = bool(path and (path.is_dir() if kind == "directory" else path.is_file()))
            artifacts[key] = {"path": str(path) if path else None, "kind": kind, "exists": exists}
            artifacts_ready = artifacts_ready and exists
        imports_ready = all(probe.get("imports", {}).values())
        ready = bool(probe.get("python_exists")) and version_ok and imports_ready and artifacts_ready
        profiles_ready = profiles_ready and ready
        state["profiles"][profile_name] = {
            "python": str(python) if python else None,
            "python_exists": probe.get("python_exists", False),
            "version": probe.get("version"),
            "version_ok": version_ok,
            "imports": probe.get("imports", {}),
            "artifacts": artifacts,
            "ready": ready,
            **({"probe_error": probe["probe_error"]} if "probe_error" in probe else {}),
        }

    executables_ready = True
    javascript: dict[str, str] | None = None
    for name in task["executables"]:
        if name == "javascript":
            javascript = javascript_runtime(root, manifest)
            resolved = javascript["executable"] if javascript else None
            state["javascript"] = javascript
        else:
            resolved = executable_path(root, manifest, name)
        state["executables"][name] = resolved
        executables_ready = executables_ready and bool(resolved)
    state["ready"] = profiles_ready and executables_ready
    state["components"] = component_probes(
        root,
        manifest,
        task["profiles"],
        state["executables"],
        javascript,
        requirements,
    )
    status, issues = component_status(state["components"])
    if not state["ready"]:
        status = "BLOCKED"
    if status == "BLOCKED":
        state["ready"] = False
    state["status"] = status
    state["degraded"] = status == "DEGRADED"
    state["issues"] = issues
    return state


def install_plan(state: dict[str, Any]) -> dict[str, Any]:
    requirements = load_requirements()
    task = selected_task(requirements, state["task"])
    missing_profiles = [name for name in task["profiles"] if not state.get("profiles", {}).get(name, {}).get("ready")]
    missing_executables = [name for name in task["executables"] if not state.get("executables", {}).get(name)]
    profile_resources = [requirements["profiles"][name].get("install") for name in missing_profiles]
    tool_resources = [
        item
        for item in requirements.get("tools", [])
        if any(name in missing_executables for name in item.get("names", []))
    ]
    resources = [item for item in [*profile_resources, *tool_resources] if item]
    return {
        "skill": requirements.get("skill"),
        "task": state["task"],
        "runtime_root": state.get("runtime_root") or state.get("requested_runtime_root") or state["preferred_install_root"],
        "missing_profiles": missing_profiles,
        "missing_executables": missing_executables,
        "estimated_additional_bytes": sum(int(item.get("estimated_bytes", 0)) for item in resources),
        "resources": resources,
        "requires_user_confirmation": bool(missing_profiles or missing_executables),
    }


def run_profile(args: argparse.Namespace) -> int:
    state = doctor_state(
        args.task,
        runtime_root=args.runtime_root,
        project_root=args.project_root,
        only_profile=args.profile,
    )
    if not state["ready"]:
        print(json.dumps({"doctor": state, "plan": install_plan(state)}, ensure_ascii=False, indent=2))
        return 2
    root = Path(state["runtime_root"])
    manifest = load_json(root / MANIFEST_NAME)
    profile = manifest["profiles"][args.profile]
    python = resolve_relative(root, profile["python"])
    command_args = list(args.args)
    if command_args and command_args[0] == "--":
        command_args = command_args[1:]
    if args.module:
        command = [str(python), "-m", args.module, *command_args]
    else:
        if not command_args:
            raise ValueError("run requires a script path or --module")
        if not command_args[0].startswith("-"):
            script = Path(command_args[0]).expanduser()
            caller_candidate = Path(args.cwd or Path.cwd()) / script
            skill_candidate = SKILL_ROOT / script
            if not script.is_absolute() and not caller_candidate.is_file() and skill_candidate.is_file():
                command_args[0] = str(skill_candidate.resolve())
        command = [str(python), *command_args]
    completed = subprocess.run(
        command,
        cwd=args.cwd or str(SKILL_ROOT),
        env=profile_environment(root, manifest, args.profile),
        check=False,
    )
    return completed.returncode


def dispatch_script_in_profile(
    task_name: str,
    profile_name: str,
    project_root: Path,
    script: Path,
    argv: list[str],
    runtime_root: str | None = None,
) -> int | None:
    state = doctor_state(
        task_name,
        runtime_root=runtime_root,
        project_root=str(project_root),
        only_profile=profile_name,
    )
    if not state["ready"]:
        print(json.dumps({"doctor": state, "plan": install_plan(state)}, ensure_ascii=False, indent=2))
        return 2
    selected_root = Path(state["runtime_root"])
    active_root = os.environ.get("SUBTITLE_RUNTIME_ROOT")
    if (
        os.environ.get("SUBTITLE_RUNTIME_PROFILE") == profile_name
        and active_root
        and os.path.normcase(str(Path(active_root).resolve())) == os.path.normcase(str(selected_root.resolve()))
    ):
        return None
    manifest = load_json(selected_root / MANIFEST_NAME)
    profile = manifest["profiles"][profile_name]
    python = resolve_relative(selected_root, profile["python"])
    if not python:
        raise RuntimeError(f"{profile_name} Python未解析")
    completed = subprocess.run(
        [str(python), "-B", str(script.resolve()), *argv],
        cwd=os.getcwd(),
        env=profile_environment(selected_root, manifest, profile_name),
        check=False,
    )
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover, verify and use a shared subtitle runtime")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("locate", "doctor", "plan"):
        command = subparsers.add_parser(name)
        command.add_argument("--runtime-root")
        command.add_argument("--project-root")
        command.add_argument("--task", default="default")

    run = subparsers.add_parser("run")
    run.add_argument("--runtime-root")
    run.add_argument("--project-root")
    run.add_argument("--task", default="default")
    run.add_argument("--profile", required=True)
    run.add_argument("--module")
    run.add_argument("--cwd")
    run.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    if args.command == "locate":
        root, candidates = choose_runtime_root(args.runtime_root, args.project_root)
        print(
            json.dumps(
                {"runtime_root": str(root) if root else None, "candidates": candidates, "preferred_install_root": str(preferred_root())},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if root else 2
    if args.command in {"doctor", "plan"}:
        state = doctor_state(args.task, args.runtime_root, args.project_root)
        payload = state if args.command == "doctor" else install_plan(state)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if state["ready"] else 2
    if args.command == "run":
        return run_profile(args)
    raise ValueError(f"Unknown command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

# ruff: noqa: E402
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from runtime_manager import (
    component_probes,
    component_status,
    discover_roots,
    javascript_runtime,
    profile_environment,
)


class RuntimeComponentTests(unittest.TestCase):
    def test_component_failure_blocks(self) -> None:
        status, issues = component_status({"ffmpeg": {"ok": False, "error": "broken"}})
        self.assertEqual("BLOCKED", status)
        self.assertIn("broken", issues[0])

    def test_cpu_fallback_is_degraded(self) -> None:
        status, issues = component_status(
            {
                "sofa_onnx": {
                    "ok": True,
                    "session_providers": ["CPUExecutionProvider"],
                    "cuda_error": "missing cufft64_11.dll",
                }
            }
        )
        self.assertEqual("DEGRADED", status)
        self.assertIn("cufft64_11.dll", issues[0])

    def test_active_cuda_is_ready(self) -> None:
        status, issues = component_status(
            {
                "separator_cuda": {"ok": True, "cuda_available": True},
                "sofa_onnx": {
                    "ok": True,
                    "session_providers": [
                        "CUDAExecutionProvider",
                        "CPUExecutionProvider",
                    ],
                    "cuda_error": None,
                },
            }
        )
        self.assertEqual("READY", status)
        self.assertEqual([], issues)

    def test_valid_project_pointer_skips_everything_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            root.mkdir()
            (root / "runtime.json").write_text(
                '{"schema":"ec-subtitle-runtime/v1"}', encoding="utf-8"
            )
            with (
                patch.dict(os.environ, {"SUBTITLE_RUNTIME_ROOT": ""}),
                patch("runtime_manager.pointer_roots", return_value=[root]),
                patch("runtime_manager.everything_roots") as everything,
            ):
                candidates = discover_roots(project_root=temporary)

            self.assertEqual("project-pointer", candidates[0]["source"])
            self.assertTrue(candidates[0]["valid"])
            everything.assert_not_called()

    def test_bound_node_runtime_is_version_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            node = root / "node.exe"
            node.write_bytes(b"node")
            manifest = {
                "shared": {
                    "javascript": {
                        "kind": "node",
                        "executable": str(node),
                    }
                }
            }
            descriptor = javascript_runtime(root, manifest)
            requirements = {"javascript": {"accepted": {"node": {"minimum": [22, 0]}}}}
            with patch(
                "runtime_manager.command_probe",
                return_value={"ok": True, "version_line": "v24.19.0"},
            ):
                probes = component_probes(
                    root,
                    manifest,
                    [],
                    {"javascript": str(node)},
                    descriptor,
                    requirements,
                )

            self.assertTrue(probes["javascript"]["ok"])
            self.assertTrue(probes["javascript"]["version_ok"])
            self.assertEqual("node", probes["javascript"]["kind"])

    def test_profile_environment_drops_inherited_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            separator = root / "separator"
            separator.mkdir()
            manifest = {
                "profiles": {
                    "base": {"python_deps": "base-deps"},
                    "separator": {},
                },
                "shared": {},
            }
            with patch.dict(
                os.environ, {"PYTHONPATH": str(root / "base-deps")}, clear=False
            ):
                env = profile_environment(root, manifest, "separator")

            self.assertNotIn("PYTHONPATH", env)


if __name__ == "__main__":
    unittest.main()

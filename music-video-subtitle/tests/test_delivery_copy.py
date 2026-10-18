from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from delivery_copy import archive_workspace, copy_delivery, verify_workspace_archive  # noqa: E402


class DeliveryCopyTests(unittest.TestCase):
    def workspace(self, root: Path) -> Path:
        source = root / "workspace" / "mvs" / "fixture"
        (source / "output").mkdir(parents=True)
        (source / ".mv-workspace").write_text("music-video-subtitle\n", encoding="utf-8")
        (source / "output" / "final.mp4").write_bytes(b"video")
        (source / "output" / "copy.md").write_text("human edit", encoding="utf-8")
        (source / "source").mkdir()
        (source / "source" / "original.mp4").write_bytes(b"original")
        return source

    def test_archive_preserves_full_workspace_and_final_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.workspace(root)
            destination = archive_workspace(source, root / "archive")
            self.assertEqual(b"original", (destination / "工程/source/original.mp4").read_bytes())
            self.assertEqual("human edit", (destination / "copy.md").read_text(encoding="utf-8"))
            self.assertTrue(source.exists())
            self.assertEqual(destination, verify_workspace_archive(source, destination))

    def test_verification_detects_same_size_corruption_and_new_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.workspace(root)
            destination = archive_workspace(source, root / "archive")
            (destination / "final.mp4").write_bytes(b"wrong")
            with self.assertRaises(ValueError):
                verify_workspace_archive(source, destination)
            (destination / "final.mp4").write_bytes(b"video")
            (source / "new.txt").write_text("new")
            with self.assertRaises(FileNotFoundError):
                verify_workspace_archive(source, destination)
            self.assertTrue(source.exists())

    def test_archive_rejects_overwrite_containment_and_wrong_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.workspace(root)
            archive_workspace(source, root / "archive")
            with self.assertRaises(FileExistsError):
                archive_workspace(source, root / "archive")
            for destination in (source, source / "archive", source.parent):
                with self.assertRaises(ValueError):
                    archive_workspace(source, destination)
            (source / ".mv-workspace").write_text("other")
            with self.assertRaises(ValueError):
                archive_workspace(source, root / "other")

    def test_failed_workspace_copy_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.workspace(root)
            with patch("delivery_copy.shutil.copy2", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    archive_workspace(source, root / "archive")
            self.assertEqual(b"original", (source / "source/original.mp4").read_bytes())

    def test_copies_directory_without_removing_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "output"
            source.mkdir()
            (source / "final.mp4").write_bytes(b"video")
            destination = root / "delivery"

            result = copy_delivery(source, destination)

            self.assertEqual(destination.resolve(), result)
            self.assertTrue((source / "final.mp4").is_file())
            self.assertEqual(b"video", (destination / "final.mp4").read_bytes())

    def test_existing_destination_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "output"
            source.mkdir()
            (source / "final.mp4").write_bytes(b"new")
            destination = root / "delivery"
            destination.mkdir()
            (destination / "final.mp4").write_bytes(b"old")

            with self.assertRaises(FileExistsError):
                copy_delivery(source, destination)

            self.assertEqual(b"old", (destination / "final.mp4").read_bytes())

    def test_failed_copy_removes_partial_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "output"
            source.mkdir()
            (source / "final.mp4").write_bytes(b"video")
            destination = root / "delivery"

            with patch("delivery_copy.shutil.copytree", side_effect=OSError("fail")):
                with self.assertRaises(OSError):
                    copy_delivery(source, destination)

            self.assertEqual([], list(root.glob(".delivery.partial-*")))


if __name__ == "__main__":
    unittest.main()

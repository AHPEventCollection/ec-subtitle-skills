from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from delivery_copy import copy_delivery  # noqa: E402


class DeliveryCopyTests(unittest.TestCase):
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

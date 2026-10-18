from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from common import write_json  # noqa: E402
from render_policy import default_destination, encoder_arguments, select_encoder  # noqa: E402
from song_progress import build_parser  # noqa: E402


class RenderPolicyTests(unittest.TestCase):
    def test_default_encoder_is_auto(self):
        args = build_parser().parse_args(["render", "--concert-dir", "fixture"])
        self.assertEqual("auto", args.encoder)

    def test_hardware_is_actually_probed_and_stops_at_first_success(self):
        with patch("render_policy.queue_directory", return_value=Path("queue")), patch(
            "render_policy.run_queued", return_value=0
        ) as queued:
            chosen, probes = select_encoder(Path("ffmpeg"), Path("concert"))
        self.assertEqual("h264_nvenc", chosen)
        self.assertEqual(1, queued.call_count)
        self.assertEqual(0, probes[0]["returncode"])
        self.assertIn("lavfi", queued.call_args.args[2])
        self.assertIn("h264_nvenc", queued.call_args.args[2])

    def test_all_hardware_failures_fall_back_to_cpu(self):
        with patch("render_policy.queue_directory", return_value=Path("queue")), patch(
            "render_policy.run_queued", return_value=1
        ):
            chosen, probes = select_encoder(Path("ffmpeg"), Path("concert"))
        self.assertEqual("libx264", chosen)
        self.assertEqual(3, len(probes))

    def test_explicit_hardware_does_not_silently_fall_back(self):
        with patch("render_policy.queue_directory", return_value=Path("queue")), patch(
            "render_policy.run_queued", return_value=1
        ), self.assertRaises(RuntimeError):
            select_encoder(Path("ffmpeg"), Path("concert"), "h264_nvenc")

    def test_explicit_cpu_does_not_probe_gpu(self):
        with patch("render_policy.run_queued") as queued:
            self.assertEqual(("libx264", []), select_encoder(Path("ffmpeg"), Path("concert"), "cpu"))
        queued.assert_not_called()

    def test_quality_arguments_are_encoder_specific(self):
        self.assertIn("-cq", encoder_arguments("h264_nvenc"))
        self.assertNotIn("-crf", encoder_arguments("h264_nvenc"))
        self.assertIn("-global_quality", encoder_arguments("h264_qsv"))
        self.assertIn("-qp_i", encoder_arguments("h264_amf"))
        self.assertIn("-crf", encoder_arguments("libx264"))

    def test_default_output_must_be_on_different_physical_disk(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            concert = root / "workspace" / "concerts" / "sample"
            concert.mkdir(parents=True)
            target = root / "external"
            write_json(root / ".subtitle-render.json", {"output_root": str(target)})
            with patch("render_policy.physical_disks", side_effect=[{3}, {2}]):
                dest = default_destination(concert, root / "source.mkv", "sample")
            self.assertEqual(target / "sample" / "sample.hardsub.mp4", dest)
            with patch("render_policy.physical_disks", side_effect=[{3}, {3}]), self.assertRaisesRegex(ValueError, "同一物理"):
                default_destination(concert, root / "source.mkv", "sample")
            with patch("render_policy.physical_disks", side_effect=[{3}, {2, 3}]), self.assertRaises(ValueError):
                default_destination(concert, root / "source.mkv", "sample")
            self.assertFalse(target.exists())

    def test_missing_config_stops_instead_of_using_concert_output(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError, "output_root"):
            default_destination(Path(temporary), Path(temporary) / "source.mkv", "sample")

    def test_explicit_output_is_allowed_but_never_overwrites_source_or_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            output = root / "explicit.mp4"
            with patch("render_policy.physical_disks") as disks:
                self.assertEqual(output, default_destination(root, source, "sample", output))
                disks.assert_not_called()
            with self.assertRaises(ValueError):
                default_destination(root, source, "sample", source)
            output.write_bytes(b"previous")
            with self.assertRaises(FileExistsError):
                default_destination(root, source, "sample", output)
            self.assertEqual(b"previous", output.read_bytes())

    def test_no_output_file_is_not_a_completed_render(self):
        from render_policy import verify_encoded_media
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            verify_encoded_media(Path("ffmpeg"), Path(temporary) / "missing.mp4",
                                 source=Path(temporary) / "source.mp4")

    def test_encoding_failure_propagates(self):
        # Probe failures are return codes; failures launching FFmpeg are not treated as no GPU
        with patch("render_policy.queue_directory", return_value=Path("queue")), patch(
            "render_policy.run_queued", side_effect=subprocess.TimeoutExpired("ffmpeg", 1)
        ), self.assertRaises(subprocess.TimeoutExpired):
            select_encoder(Path("ffmpeg"), Path("concert"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audit_concert_coverage import timeline_gaps  # noqa: E402


class CoverageAuditTests(unittest.TestCase):
    def test_reports_leading_internal_and_trailing_gaps(self) -> None:
        gaps = timeline_gaps(
            100.0,
            [
                {"start": 10.0, "end": 30.0},
                {"start": 35.0, "end": 90.0},
            ],
        )
        self.assertEqual(
            [
                {"start": 0.0, "end": 10.0, "duration": 10.0},
                {"start": 30.0, "end": 35.0, "duration": 5.0},
                {"start": 90.0, "end": 100.0, "duration": 10.0},
            ],
            gaps,
        )

    def test_ignores_sub_half_second_rounding_gaps(self) -> None:
        gaps = timeline_gaps(
            20.0,
            [
                {"start": 0.2, "end": 10.0},
                {"start": 10.4, "end": 20.0},
            ],
        )
        self.assertEqual([], gaps)


if __name__ == "__main__":
    unittest.main()

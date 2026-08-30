"""Tests de apex.obs.status — REAL/SIMULATED/UNKNOWN, vocabulaire partagé."""

from __future__ import annotations

import unittest

from apex.obs.status import DataStatus, Measurement


class TestMeasurement(unittest.TestCase):
    def test_real_helper(self) -> None:
        m = Measurement.real(42)
        self.assertEqual(m.value, 42)
        self.assertEqual(m.status, DataStatus.REAL)

    def test_unknown_helper_carries_no_value(self) -> None:
        m = Measurement.unknown()
        self.assertIsNone(m.value)
        self.assertEqual(m.status, DataStatus.UNKNOWN)

    def test_simulated_helper(self) -> None:
        m = Measurement.simulated(1.5)
        self.assertEqual(m.value, 1.5)
        self.assertEqual(m.status, DataStatus.SIMULATED)

    def test_to_dict_shape(self) -> None:
        self.assertEqual(Measurement.real(7).to_dict(), {"value": 7, "status": "real"})

    def test_frozen(self) -> None:
        m = Measurement.real(1)
        with self.assertRaises(Exception):
            m.value = 2  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()

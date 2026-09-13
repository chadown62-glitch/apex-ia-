"""Tests d'`apex.doctor.startup_history` — écriture réelle sur disque
(fichier temporaire, jamais mocké : `open()`/`os.makedirs` réels)."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from apex.doctor.startup_history import (
    RepairRecord,
    StartupRecord,
    append_record,
    read_history,
    recurring_problems,
)


class TestAppendAndReadHistory(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "sub", "startup_history.jsonl")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_a_missing_file_yields_empty_history_never_an_exception(self) -> None:
        self.assertEqual(read_history(path=self.path), ())

    def test_b_a_written_record_round_trips_exactly(self) -> None:
        record = StartupRecord.new(
            problems_detected=("service.postgres",),
            repairs=(RepairRecord(check_name="service.postgres", action="safe_auto_fix", attempted=True, succeeded=True),),
            final_status="ready_to_serve", duration_s=1.23,
        )
        append_record(record, path=self.path)
        self.assertTrue(os.path.exists(self.path))
        history = read_history(path=self.path)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].startup_id, record.startup_id)
        self.assertEqual(history[0].problems_detected, ("service.postgres",))
        self.assertEqual(history[0].final_status, "ready_to_serve")

    def test_c_history_is_append_only_never_overwritten(self) -> None:
        for i in range(3):
            append_record(
                StartupRecord.new(
                    problems_detected=(), repairs=(), final_status=f"run-{i}", duration_s=0.1,
                ),
                path=self.path,
            )
        history = read_history(path=self.path)
        self.assertEqual(len(history), 3)
        self.assertEqual([r.final_status for r in history], ["run-0", "run-1", "run-2"])

    def test_d_limit_returns_only_the_most_recent_records(self) -> None:
        for i in range(5):
            append_record(
                StartupRecord.new(problems_detected=(), repairs=(), final_status=f"run-{i}", duration_s=0.1),
                path=self.path,
            )
        history = read_history(path=self.path, limit=2)
        self.assertEqual([r.final_status for r in history], ["run-3", "run-4"])


class TestRecurringProblems(unittest.TestCase):
    def test_a_counts_occurrences_and_success_rate_from_real_records(self) -> None:
        records = (
            StartupRecord.new(
                problems_detected=("service.postgres",),
                repairs=(RepairRecord("service.postgres", "safe_auto_fix", True, True),),
                final_status="ready_to_serve", duration_s=1.0,
            ),
            StartupRecord.new(
                problems_detected=("service.postgres",),
                repairs=(RepairRecord("service.postgres", "safe_auto_fix", True, False),),
                final_status="blocked_doctor", duration_s=1.0,
            ),
        )
        problems = recurring_problems(records)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].check_name, "service.postgres")
        self.assertEqual(problems[0].occurrences, 2)
        self.assertEqual(problems[0].auto_fix_attempts, 2)
        self.assertEqual(problems[0].auto_fix_successes, 1)
        self.assertEqual(problems[0].auto_fix_success_rate, 0.5)

    def test_b_no_attempt_yields_none_success_rate_never_zero(self) -> None:
        records = (
            StartupRecord.new(
                problems_detected=("gpu.present",),
                repairs=(RepairRecord("gpu.present", "manual_required", False, None),),
                final_status="blocked_doctor", duration_s=1.0,
            ),
        )
        problems = recurring_problems(records)
        self.assertIsNone(problems[0].auto_fix_success_rate)

    def test_c_empty_history_yields_no_recurring_problems(self) -> None:
        self.assertEqual(recurring_problems(()), ())


if __name__ == "__main__":
    unittest.main()

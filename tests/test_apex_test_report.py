"""`scripts/apex_test_report.py` : compteurs exacts pris des rapports de
pytest, sous-tests séparés, UNKNOWN quand la session est incomplète."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "apex_test_report.py"

SAMPLE = textwrap.dedent(
    """
    import unittest
    import pytest

    class T(unittest.TestCase):
        def test_sub(self):
            for i in range(3):
                with self.subTest(i=i):
                    self.assertNotEqual(i, 1)

    def test_ok():
        pass

    def test_fail():
        assert False

    @pytest.mark.skip
    def test_skip():
        pass

    @pytest.mark.xfail
    def test_xf():
        assert False

    @pytest.mark.xfail
    def test_xp():
        pass

    @pytest.fixture
    def bad():
        raise RuntimeError("fixture cassée")

    def test_err(bad):
        pass
    """
)


class TestApexTestReport(unittest.TestCase):
    def _run(self, source: str) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "test_sample.py").write_text(source, encoding="utf-8")
            report = Path(tmp) / "report.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--json",
                    str(report),
                    "--",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    tmp,
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=tmp,
            )
            return completed.returncode, json.loads(report.read_text(encoding="utf-8"))

    def test_counts_are_exact_and_failed_subtests_are_not_hidden(self) -> None:
        code, report = self._run(SAMPLE)
        self.assertEqual(code, 1)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["counts_status"], "EXACT")
        self.assertEqual(report["collected"], 7)
        self.assertEqual(
            report["counts"],
            {
                "passed": 2,
                "subtests_passed": 2,
                "failed": 1,
                "subtests_failed": 1,
                "skipped": 1,
                "subtests_skipped": 0,
                "xfailed": 1,
                "subtests_xfailed": 0,
                "xpassed": 1,
                "subtests_xpassed": 0,
                "error": 1,
                "subtests_error": 0,
            },
        )
        self.assertEqual(report["passed_tests_with_failed_subtests"], ["test_sample.py::T::test_sub"])
        self.assertIn("test_sample.py::test_err", report["failing_nodeids"])

    def test_an_interrupted_session_has_unknown_counts(self) -> None:
        code, report = self._run("import this_module_does_not_exist\n")
        self.assertEqual(code, 2)
        self.assertEqual(report["counts_status"], "UNKNOWN")
        self.assertIsNone(report["counts"])
        self.assertIsNone(report["collected"])


if __name__ == "__main__":
    unittest.main()

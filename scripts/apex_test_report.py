"""Lance pytest et rapporte des compteurs EXACTS, jamais estimés.

    .venv/bin/python scripts/apex_test_report.py --json report.json -- [arguments pytest...]

Les compteurs viennent des rapports de pytest lui-même (catégories de
`terminalreporter.stats`), pas d'une lecture de la sortie texte. Les
sous-tests sont comptés à part : pytest classe un sous-test échoué dans
`failed` tout en comptant le test parent comme `passed` — une recherche de
lignes « FAILED » dans la sortie les manque.

`counts_status` vaut EXACT quand la session s'est terminée normalement
(codes 0, 1 ou 5). Sinon (interruption, erreur interne, usage) les
compteurs ne couvrent qu'une partie de la suite : UNKNOWN.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_COMPLETE_EXIT_CODES = frozenset({0, 1, 5})
_OUTCOMES = ("passed", "failed", "skipped", "xfailed", "xpassed", "error")


class _Counter:
    def __init__(self) -> None:
        self.stats: dict[str, list[Any]] = {}
        self.collected: int | None = None

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.collected = len(session.items)

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        self.stats = {key: list(reports) for key, reports in terminalreporter.stats.items()}


def _is_subtest(report: Any) -> bool:
    return type(report).__name__ == "SubtestReport"


def summarize(counter: _Counter, exit_code: int) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for outcome in _OUTCOMES:
        reports = counter.stats.get(outcome, [])
        counts[outcome] = sum(1 for r in reports if not _is_subtest(r))
        counts[f"subtests_{outcome}"] = sum(1 for r in reports if _is_subtest(r))
    counts["subtests_passed"] += len(counter.stats.get("subtests passed", []))
    counts["subtests_skipped"] += len(counter.stats.get("subtests skipped", []))
    failing = sorted(
        {
            r.nodeid
            for key in ("failed", "error")
            for r in counter.stats.get(key, [])
            if hasattr(r, "nodeid")
        }
    )
    passed_parents = {r.nodeid for r in counter.stats.get("passed", []) if not _is_subtest(r)}
    failed_subtest_parents = {
        r.nodeid for r in counter.stats.get("failed", []) if _is_subtest(r) and hasattr(r, "nodeid")
    }
    complete = exit_code in _COMPLETE_EXIT_CODES
    return {
        "exit_code": exit_code,
        "counts_status": "EXACT" if complete else "UNKNOWN",
        "collected": counter.collected if complete else None,
        "counts": counts if complete else None,
        "partial_counts": None if complete else counts,
        "failing_nodeids": failing,
        "passed_tests_with_failed_subtests": sorted(passed_parents & failed_subtest_parents),
    }


def human(summary: dict[str, Any]) -> str:
    if summary["counts_status"] != "EXACT":
        return f"exit={summary['exit_code']} — compteurs UNKNOWN (session incomplète)"
    c = summary["counts"]
    parts = [f"{c[o]} {o}" for o in _OUTCOMES]
    sub = [
        f"{c['subtests_' + o]} subtests {o}" for o in ("passed", "failed", "skipped") if c["subtests_" + o]
    ]
    return f"exit={summary['exit_code']} collected={summary['collected']} — " + ", ".join(parts + sub)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", type=Path, help="écrire le rapport JSON ici")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    pytest_args = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
    counter = _Counter()
    exit_code = int(pytest.main(pytest_args, plugins=[counter]))
    summary = {"pytest_args": pytest_args, **summarize(counter, exit_code)}
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("APEX_TEST_REPORT " + human(summary), file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

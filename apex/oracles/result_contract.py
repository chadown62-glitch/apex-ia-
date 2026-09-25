"""Contrat de résultat — jugement d'une solution générée HORS du sandbox.

Défaut confirmé par le Continuous Improvement Engine (cycle 2) : juger une
solution générée par le seul code de sortie laisse le candidat décider de
son propre verdict. Son code s'exécute AVANT les tests cachés, dans le même
processus : `sys.exit(0)`, `os._exit(0)`, un `sys.exit` neutralisé ou un
résumé forgé obtenaient PASS sans jamais résoudre la tâche.

Le contrat :

- le script sandboxé (harness) ne contient que les ENTRÉES des cas cachés ;
  il publie les sorties réellement calculées sur UNE ligne
  `RESULT_LINE_PREFIX + {"results": [...]}` ;
- les valeurs ATTENDUES restent sur l'hôte (`Task.expected_answer`, liste
  JSON) — jamais dans le script, donc jamais lisibles par le candidat ;
- `ResultContractOracle` compare hors sandbox. Aucune ligne, plusieurs
  lignes, une ligne illisible, un nombre de résultats différent ou un type
  différent (`1` n'est pas `True`) -> FAIL. Un contrat déclaré sans valeurs
  attendues valides -> ERROR (tâche mal formée), jamais PASS.

Une tâche qui ne déclare pas le contrat (harness sans préfixe, ou aucun
harness) garde exactement le jugement de l'oracle interne (code de sortie)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from apex.oracles.model import OracleResult, OracleVerdict
from apex.oracles.provider import OracleProvider
from apex.sandbox.model import ExecutionStatus
from apex.sandbox.result import SandboxResult
from apex.tasks.model import Task

RESULT_LINE_PREFIX = "APEX_RESULT:"
#: Préfixe du détail d'oracle quand le candidat ne respecte pas le contrat de sortie.
CONTRACT_VIOLATION = "contrat de résultat violé : "

_PASSTHROUGH = frozenset({OracleVerdict.TIMEOUT, OracleVerdict.ERROR, OracleVerdict.UNKNOWN})


def declares_result_contract(task: Task) -> bool:
    return task.solution_harness is not None and RESULT_LINE_PREFIX in task.solution_harness


@dataclass(frozen=True)
class PublishedResults:
    results: list[Any] | None
    problem: str | None


def read_published_results(stdout: str) -> PublishedResults:
    payloads = [
        line[len(RESULT_LINE_PREFIX) :]
        for line in stdout.splitlines()
        if line.startswith(RESULT_LINE_PREFIX)
    ]
    if not payloads:
        return PublishedResults(
            None, "aucun résultat publié (sortie anticipée, plantage ou contrat ignoré)"
        )
    if len(payloads) > 1:
        return PublishedResults(None, f"résultat publié {len(payloads)} fois — contrat violé")
    try:
        parsed = json.loads(payloads[0])
    except ValueError:
        return PublishedResults(None, "résultat publié illisible (JSON invalide)")
    results = parsed.get("results") if isinstance(parsed, dict) else None
    if not isinstance(results, list):
        return PublishedResults(None, "résultat publié sans liste « results »")
    return PublishedResults(results, None)


def expected_results(task: Task) -> list[Any] | None:
    if task.expected_answer is None:
        return None
    try:
        parsed = json.loads(task.expected_answer)
    except ValueError:
        return None
    return parsed if isinstance(parsed, list) else None


def _same(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    return bool(actual == expected)


def compare_results(results: list[Any], expected: list[Any]) -> tuple[bool, str]:
    if len(results) != len(expected):
        return False, f"{len(results)} résultat(s) publié(s) pour {len(expected)} cas attendus"
    wrong = [i for i, (a, e) in enumerate(zip(results, expected, strict=True)) if not _same(a, e)]
    errors = sum(1 for r in results if isinstance(r, dict) and "error" in r)
    if wrong:
        suffix = f", dont {errors} exception(s) levée(s) par le candidat" if errors else ""
        return False, f"{len(wrong)}/{len(expected)} cas incorrects (indices {wrong}){suffix}"
    return (
        True,
        f"{len(expected)}/{len(expected)} résultats conformes aux valeurs attendues "
        "(comparées hors sandbox)",
    )


class ResultContractOracle(OracleProvider):
    def __init__(self, inner: OracleProvider | None = None) -> None:
        if inner is None:
            from apex.oracles.exit_code import ExitCodeOracle

            inner = ExitCodeOracle()
        self._inner = inner

    def judge(self, *, sandbox_result: SandboxResult, task: Task) -> OracleResult:
        base = self._inner.judge(sandbox_result=sandbox_result, task=task)
        if not declares_result_contract(task) or base.verdict in _PASSTHROUGH:
            return base
        expected = expected_results(task)
        if expected is None:
            return OracleResult(
                verdict=OracleVerdict.ERROR,
                detail=(
                    "tâche mal formée : contrat de résultat déclaré sans « expected_answer » "
                    "(liste JSON)"
                ),
            )
        if sandbox_result.status is not ExecutionStatus.SUCCESS:
            return OracleResult(
                verdict=OracleVerdict.FAIL, detail=f"le script n'a pas terminé normalement — {base.detail}"
            )
        published = read_published_results(sandbox_result.stdout)
        if published.results is None:
            return OracleResult(
                verdict=OracleVerdict.FAIL,
                detail=CONTRACT_VIOLATION + (published.problem or "résultat absent"),
            )
        ok, detail = compare_results(published.results, expected)
        return OracleResult(verdict=OracleVerdict.PASS if ok else OracleVerdict.FAIL, detail=detail)

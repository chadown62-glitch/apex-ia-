"""Tests d'`apex.metalearner.failure_taxonomy.classify_failure` — passe N+20.
Chaque test construit un `EvalRun` déjà réel (jamais un nouveau système
d'exécution) et vérifie que la classification suit strictement les preuves
disponibles, jamais une déduction du type « fitness mauvais = stratégie
mauvaise »."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from apex.evalrunner.model import EvalMethod, EvalRun
from apex.metalearner.failure_taxonomy import FailureCategory, classify_failure
from apex.oracles.model import OracleVerdict
from apex.sandbox.model import ExecutionStatus


def _an_eval_run(**overrides) -> EvalRun:
    defaults = dict(
        id="run-1", task_id="task-1", method=EvalMethod.EXPERIMENTATION,
        oracle_verdict=OracleVerdict.PASS, oracle_detail=None, exit_code=0, elapsed_s=0.1,
        facts={}, created_by="core", clone_id="clone-1", sandbox_status=ExecutionStatus.SUCCESS,
        model_id="fake-model", started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return EvalRun(**defaults)


def _harness_stdout(*, failures: list[dict]) -> str:
    return json.dumps({"total": 6, "failures": failures})


class TestClassifyFailure(unittest.TestCase):
    def test_a_pass_is_success(self) -> None:
        run = _an_eval_run(oracle_verdict=OracleVerdict.PASS)
        self.assertIs(classify_failure(run), FailureCategory.SUCCESS)

    def test_b_timeout_verdict_is_timeout(self) -> None:
        run = _an_eval_run(oracle_verdict=OracleVerdict.TIMEOUT, sandbox_status=ExecutionStatus.TIMEOUT)
        self.assertIs(classify_failure(run), FailureCategory.TIMEOUT)

    def test_c_unknown_verdict_is_infrastructure_failure(self) -> None:
        run = _an_eval_run(oracle_verdict=OracleVerdict.UNKNOWN, sandbox_status=ExecutionStatus.CANCELLED)
        self.assertIs(classify_failure(run), FailureCategory.INFRASTRUCTURE_FAILURE)

    def test_d_no_model_id_and_no_sandbox_status_is_generation_failure(self) -> None:
        run = _an_eval_run(
            oracle_verdict=OracleVerdict.ERROR, model_id=None, sandbox_status=None, clone_id="clone-1",
            oracle_detail="génération impossible : boom",
        )
        self.assertIs(classify_failure(run), FailureCategory.GENERATION_FAILURE)

    def test_e_sandbox_unavailable_detail_is_infrastructure_failure(self) -> None:
        run = _an_eval_run(
            oracle_verdict=OracleVerdict.ERROR, model_id="fake-model",
            sandbox_status=ExecutionStatus.PERMISSION_DENIED,
            oracle_detail="sandbox indisponible : isolation introuvable",
        )
        self.assertIs(classify_failure(run), FailureCategory.INFRASTRUCTURE_FAILURE)

    def test_f_resource_limit_statuses_are_resource_failure(self) -> None:
        for status in (
            ExecutionStatus.OOM_KILLED, ExecutionStatus.RESOURCE_LIMIT_EXCEEDED,
            ExecutionStatus.PROCESS_LIMIT_EXCEEDED, ExecutionStatus.DISK_LIMIT_EXCEEDED,
        ):
            with self.subTest(status=status):
                run = _an_eval_run(
                    oracle_verdict=OracleVerdict.ERROR, sandbox_status=status,
                    oracle_detail=f"échec de l'infrastructure d'exécution ({status.value})",
                )
                self.assertIs(classify_failure(run), FailureCategory.RESOURCE_FAILURE)

    def test_g_network_or_permission_denied_with_real_model_is_policy_security_rejection(self) -> None:
        for status in (ExecutionStatus.NETWORK_DENIED, ExecutionStatus.PERMISSION_DENIED):
            with self.subTest(status=status):
                run = _an_eval_run(
                    oracle_verdict=OracleVerdict.ERROR, model_id="fake-model", sandbox_status=status,
                    oracle_detail=f"échec de l'infrastructure d'exécution ({status.value})",
                )
                self.assertIs(classify_failure(run), FailureCategory.POLICY_SECURITY_REJECTION)

    def test_h_fail_with_valid_json_summary_and_no_errors_is_incorrect_solution(self) -> None:
        stdout = _harness_stdout(failures=[{"input": "hello", "expected": False, "actual": True}])
        run = _an_eval_run(
            oracle_verdict=OracleVerdict.FAIL, sandbox_status=ExecutionStatus.FAILED, exit_code=1,
            facts={"candidate_execution": {"stdout": stdout, "stderr": "", "stdout_truncated": False, "stderr_truncated": False}},
        )
        self.assertIs(classify_failure(run), FailureCategory.INCORRECT_SOLUTION)

    def test_i_fail_with_a_caught_exception_entry_is_invalid_candidate(self) -> None:
        stdout = _harness_stdout(failures=[{"input": "racecar", "error": "NameError(\"name 'is_palindrome' is not defined\")"}])
        run = _an_eval_run(
            oracle_verdict=OracleVerdict.FAIL, sandbox_status=ExecutionStatus.FAILED, exit_code=1,
            facts={"candidate_execution": {"stdout": stdout, "stderr": "", "stdout_truncated": False, "stderr_truncated": False}},
        )
        self.assertIs(classify_failure(run), FailureCategory.INVALID_CANDIDATE)

    def test_j_fail_with_unparseable_stdout_is_invalid_candidate(self) -> None:
        """Un `SyntaxError` empêche le script de produire son résumé JSON —
        preuve d'un candidat structurellement invalide, jamais une simple
        réponse fausse."""
        run = _an_eval_run(
            oracle_verdict=OracleVerdict.FAIL, sandbox_status=ExecutionStatus.FAILED, exit_code=1,
            facts={"candidate_execution": {"stdout": "", "stderr": "SyntaxError: invalid syntax", "stdout_truncated": False, "stderr_truncated": False}},
        )
        self.assertIs(classify_failure(run), FailureCategory.INVALID_CANDIDATE)

    def test_k_fail_without_any_candidate_execution_facts_is_invalid_candidate(self) -> None:
        run = _an_eval_run(oracle_verdict=OracleVerdict.FAIL, sandbox_status=ExecutionStatus.FAILED, facts={})
        self.assertIs(classify_failure(run), FailureCategory.INVALID_CANDIDATE)

    def test_l_never_deduces_incorrect_solution_from_fitness_alone(self) -> None:
        """§ mission : ne jamais confondre échec d'infrastructure et
        réponse incorrecte — un ERROR/timeout ne devient jamais
        `incorrect_solution`/`invalid_candidate`, quel que soit le contenu
        de `facts`."""
        run = _an_eval_run(
            oracle_verdict=OracleVerdict.ERROR, model_id=None, sandbox_status=None,
            oracle_detail="génération impossible : timeout réseau",
            facts={"candidate_execution": {"stdout": _harness_stdout(failures=[{"input": "x", "error": "boom"}])}},
        )
        self.assertIs(classify_failure(run), FailureCategory.GENERATION_FAILURE)


if __name__ == "__main__":
    unittest.main()

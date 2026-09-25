"""Tests d'`EvalRunnerService.run_generated_solution` — passe « Generated-
Solution Evaluation Loop » (N+19), le TROISIÈME chemin d'exécution
d'EvalRunner : TASK -> MODEL PROVIDER -> CODE CANDIDAT -> SANDBOX RÉEL ->
ORACLE -> EVAL_RUN.

Même discipline que `tests/test_evalrunner_model_evaluation.py` :
`_FakeModelProvider` n'est jamais un modèle, seulement une implémentation
fidèle du contrat `ModelProvider`. Contrairement à ce fichier, l'exécution
sandboxée ELLE-MÊME est RÉELLE (vraie `AgentRuntime` + vraie sandbox
namespaced, même patron que `tests/test_evalrunner_service.py`) — seul le
`ModelProvider` est un double, car aucun Ollama réel n'est requis pour
prouver que la boucle sandbox/oracle/fitness est réelle. `TestRealOllama...`
est le SEUL test contre un VRAI Ollama, sauté si aucun n'est joignable —
jamais un faux PASS d'intégration."""

from __future__ import annotations

import json
import os
import socket
import unittest

from sqlalchemy import create_engine

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, ModelEndpoint, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import ModelProviderError
from apex.evalrunner.generated_solution import (
    CANDIDATE_CODE_MARKER,
    MissingCandidateMarkerError,
    build_candidate_command,
    extract_candidate_code,
)
from apex.evalrunner.model import EvalMethod
from apex.evalrunner.service import EvalRunnerService
from apex.genome.clone_config import resolve_generation_temperature
from apex.metalearner.generated_solution_cycle import CandidateStrategy, compare_generated_solutions
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement
from apex.oracles.model import OracleVerdict
from apex.oracles.result_contract import compare_results, read_published_results
from apex.policy.model import Subject
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService
from apex.taskgen.solution_tasks import build_palindrome_task_spec

_CORRECT_IS_PALINDROME = (
    "def is_palindrome(s: str) -> bool:\n"
    "    cleaned = s.lower()\n"
    "    return cleaned == cleaned[::-1]\n"
)
_WRONG_IS_PALINDROME = (
    "def is_palindrome(s: str) -> bool:\n"
    "    return False\n"
)
_BROKEN_CODE_MISSING_FUNCTION = "x = 1\n"


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def a_pool(**overrides) -> ComputePool:
    defaults = dict(
        pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
        capabilities=(Capability.CPU_EVAL,),
    )
    defaults.update(overrides)
    return ComputePool(**defaults)


def an_endpoint(**overrides) -> ModelEndpoint:
    defaults = {
        "pool_id": "local", "runtime": "ollama", "url": "http://127.0.0.1:11434",
        "served_model": "qwen2.5-coder:7b-instruct", "max_context": 8192,
    }
    defaults.update(overrides)
    return ModelEndpoint(**defaults)


class _FakeModelProvider(ModelProvider):
    """Double fidèle au contrat `ModelProvider` — voir la docstring du
    module. `response_by_temperature` permet de simuler des stratégies de
    génération réellement distinctes (utilisé par les tests multi-candidats)."""

    def __init__(self, *, response_text: str = _CORRECT_IS_PALINDROME) -> None:
        self.response_text = response_text
        self.response_by_temperature: dict[float | None, str] = {}
        self.model_used = "qwen2.5-coder:7b-instruct-actually-served"
        self.should_fail = False
        self.received_requests: list[GenerationRequest] = []

    def detect(self) -> ModelCapabilityReport:
        return ModelCapabilityReport(
            status=CapabilityStatus.AVAILABLE, backend="fake-test-double",
            detail="double de test — jamais un vrai backend",
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.received_requests.append(request)
        if self.should_fail:
            raise ModelProviderError(
                "FakeModelProvider.generate", endpoint_url=request.endpoint.url,
                detail="échec simulé pour test",
            )
        text = self.response_by_temperature.get(request.temperature, self.response_text)
        return GenerationResult(
            text=text, model_used=self.model_used,
            tokens_prompt=Measurement.real(20), tokens_completion=Measurement.real(15),
            finish_reason="stop", elapsed_s=0.01, facts={"fake": True},
        )


class TestBuildCandidateCommand(unittest.TestCase):
    """1/2 : construction du script sandboxé — pure, sans réseau ni sandbox."""

    def test_a_marker_is_substituted_by_the_candidate_code(self) -> None:
        harness = f"{CANDIDATE_CODE_MARKER}\nprint('after')\n"
        command = build_candidate_command(solution_harness=harness, candidate_code="print('candidate')")
        self.assertEqual(command[:2], ("python3", "-c"))
        self.assertIn("print('candidate')", command[2])
        self.assertIn("print('after')", command[2])
        self.assertNotIn(CANDIDATE_CODE_MARKER, command[2])

    def test_b_missing_marker_raises_before_any_execution(self) -> None:
        with self.assertRaises(MissingCandidateMarkerError):
            build_candidate_command(solution_harness="no marker here", candidate_code="pass")

    def test_c_a_correct_solution_against_the_real_palindrome_harness_exits_zero(self) -> None:
        """Vérifie le harness lui-même (sans EvalRunner/sandbox) : un VRAI
        sous-processus python3, exécuté directement, avec une solution
        correcte."""
        import subprocess

        spec = build_palindrome_task_spec()
        command = build_candidate_command(
            solution_harness=spec.solution_harness, candidate_code=_CORRECT_IS_PALINDROME,
        )
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        published = read_published_results(result.stdout)
        self.assertTrue(compare_results(published.results, json.loads(spec.expected_answer))[0])

    def test_d_a_wrong_solution_publishes_results_that_differ_from_the_hidden_expected_values(self) -> None:
        import subprocess

        spec = build_palindrome_task_spec()
        command = build_candidate_command(
            solution_harness=spec.solution_harness, candidate_code=_WRONG_IS_PALINDROME,
        )
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        published = read_published_results(result.stdout)
        self.assertIsNone(published.problem)
        ok, detail = compare_results(published.results, json.loads(spec.expected_answer))
        self.assertFalse(ok)
        self.assertIn("cas incorrects", detail)

    def test_f_a_markdown_fenced_solution_against_the_real_harness_now_exits_zero(self) -> None:
        """Le cas précisément diagnostiqué par l'audit : reproduit ici avec
        un VRAI sous-processus python3 — avant le correctif, les balises
        ``` produisaient un `SyntaxError` immédiat, indiscernable d'un vrai
        échec de solution côté `ExitCodeOracle`."""
        import subprocess

        spec = build_palindrome_task_spec()
        fenced = f"```python\n{_CORRECT_IS_PALINDROME}```"
        command = build_candidate_command(solution_harness=spec.solution_harness, candidate_code=fenced)
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_e_a_solution_missing_the_function_fails_with_a_real_nameerror(self) -> None:
        import subprocess

        spec = build_palindrome_task_spec()
        command = build_candidate_command(
            solution_harness=spec.solution_harness, candidate_code=_BROKEN_CODE_MISSING_FUNCTION,
        )
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        # Le harness capture l'exception PAR cas (jamais un crash brut) et la
        # publie dans le résultat — c'est là qu'on la vérifie.
        self.assertIn("NameError", result.stdout)
        published = read_published_results(result.stdout)
        ok, detail = compare_results(published.results, json.loads(spec.expected_answer))
        self.assertFalse(ok)
        self.assertIn("exception", detail)


class TestExtractCandidateCode(unittest.TestCase):
    """Correctif diagnostiqué par l'audit : un modèle qui enveloppe sa
    réponse dans un bloc markdown produisait, avant ce correctif, un
    `SyntaxError` immédiat sur les balises elles-mêmes — indiscernable,
    côté `ExitCodeOracle`, d'un vrai échec de solution."""

    def test_a_fenced_block_with_language_tag_is_isolated(self) -> None:
        text = f"```python\n{_CORRECT_IS_PALINDROME}```"
        self.assertEqual(extract_candidate_code(text), _CORRECT_IS_PALINDROME)

    def test_b_fenced_block_without_language_tag_is_isolated(self) -> None:
        text = f"```\n{_CORRECT_IS_PALINDROME}```"
        self.assertEqual(extract_candidate_code(text), _CORRECT_IS_PALINDROME)

    def test_c_explanatory_text_around_a_fenced_block_is_discarded(self) -> None:
        text = f"Voici la solution :\n```python\n{_CORRECT_IS_PALINDROME}```\nJ'espère que ça aide !"
        self.assertEqual(extract_candidate_code(text), _CORRECT_IS_PALINDROME)

    def test_d_text_without_any_fence_is_returned_unchanged(self) -> None:
        self.assertEqual(extract_candidate_code(_CORRECT_IS_PALINDROME), _CORRECT_IS_PALINDROME)

    def test_e_only_the_first_fenced_block_is_used(self) -> None:
        text = f"```python\n{_CORRECT_IS_PALINDROME}```\n\nautre bloc :\n```\n{_WRONG_IS_PALINDROME}```"
        self.assertEqual(extract_candidate_code(text), _CORRECT_IS_PALINDROME)


class TestResolveGenerationTemperature(unittest.TestCase):
    """3 : câblage Genome -> stratégie de génération (température seule,
    voir la docstring d'`apex.genome.clone_config`)."""

    def test_a_absent_genome_yields_none(self) -> None:
        self.assertIsNone(resolve_generation_temperature(None))
        self.assertIsNone(resolve_generation_temperature({}))

    def test_b_valid_temperature_is_returned(self) -> None:
        self.assertEqual(resolve_generation_temperature({"generation": {"temperature": 0.7}}), 0.7)

    def test_c_out_of_bounds_temperature_is_rejected_never_clamped_silently(self) -> None:
        self.assertIsNone(resolve_generation_temperature({"generation": {"temperature": 5.0}}))
        self.assertIsNone(resolve_generation_temperature({"generation": {"temperature": -1.0}}))

    def test_d_non_numeric_temperature_is_rejected(self) -> None:
        self.assertIsNone(resolve_generation_temperature({"generation": {"temperature": "hot"}}))
        self.assertIsNone(resolve_generation_temperature({"generation": {"temperature": True}}))


class TestRunGeneratedSolution(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.task_service = TaskBankService(self.factory)
        self.evalrunner = EvalRunnerService(self.factory, agent_runtime=self.runtime)
        self.clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:x", spawned_by=Subject.CORE,
        )
        self.endpoint = an_endpoint()

    def _a_solution_task(self):
        spec = build_palindrome_task_spec()
        return self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
            command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness, expected_answer=spec.expected_answer,
        )

    # -- 1/2/3 : chemin heureux, correct de bout en bout ---------------------

    def test_a_a_correct_generated_solution_executes_in_a_real_sandbox_and_passes(self) -> None:
        provider = _FakeModelProvider(response_text=_CORRECT_IS_PALINDROME)
        task = self._a_solution_task()
        run = self.evalrunner.run_generated_solution(
            task_id=task.id, clone_id=self.clone.clone_id, endpoint=self.endpoint,
            method=EvalMethod.EXPERIMENTATION, provider=provider,
        )
        self.assertIs(run.oracle_verdict, OracleVerdict.PASS)
        self.assertEqual(run.exit_code, 0)
        self.assertEqual(run.model_id, provider.model_used)
        self.assertEqual(run.clone_id, self.clone.clone_id)
        self.assertIsNotNone(run.sandbox_status)

    def test_b_a_wrong_generated_solution_runs_to_completion_and_is_failed_outside_the_sandbox(self) -> None:
        """Contrat de résultat : le script termine normalement (code 0) —
        le verdict FAIL est décidé hors sandbox, par comparaison aux valeurs
        attendues, jamais par le code de sortie que le candidat contrôle."""
        provider = _FakeModelProvider(response_text=_WRONG_IS_PALINDROME)
        task = self._a_solution_task()
        run = self.evalrunner.run_generated_solution(
            task_id=task.id, clone_id=self.clone.clone_id, endpoint=self.endpoint,
            method=EvalMethod.EXPERIMENTATION, provider=provider,
        )
        self.assertIs(run.oracle_verdict, OracleVerdict.FAIL)
        self.assertEqual(run.exit_code, 0)
        self.assertIn("cas incorrects", run.oracle_detail)
        self.assertEqual(run.model_id, provider.model_used)

    def test_c_both_model_id_and_sandbox_status_are_persisted_together(self) -> None:
        """Le point central de la passe : un EvalRun du chemin
        `run_generated_solution` porte SIMULTANÉMENT `model_id` (qui a
        généré) ET `clone_id`/`sandbox_status`/`exit_code` (comment ça s'est
        réellement exécuté) — jamais l'un sans l'autre, contrairement à
        `run()` (jamais de model_id) et `run_model_eval()` (jamais de
        sandbox_status)."""
        provider = _FakeModelProvider(response_text=_CORRECT_IS_PALINDROME)
        task = self._a_solution_task()
        run = self.evalrunner.run_generated_solution(
            task_id=task.id, clone_id=self.clone.clone_id, endpoint=self.endpoint,
            method=EvalMethod.EXPERIMENTATION, provider=provider,
        )
        reread = self.evalrunner.get(run.id)
        self.assertIsNotNone(reread)
        self.assertIsNotNone(reread.model_id)
        self.assertIsNotNone(reread.sandbox_status)
        self.assertIn("model_response", reread.facts)
        self.assertEqual(reread.facts["model_response"]["text"], _CORRECT_IS_PALINDROME)

    # -- 4 : jamais de texte généré exécuté avant le sandbox (sécurité) ------

    def test_d_the_generated_text_is_never_evaluated_in_this_process_only_via_subprocess(self) -> None:
        """§30 : une solution générée reste sandboxée. Une charge qui
        tenterait d'affecter CE processus (ex. lever une exception Python
        native) ne peut réellement le faire qu'à l'intérieur du
        sous-processus sandboxé — la preuve : le test complète normalement
        et le verdict est FAIL (le script a réellement tourné, séparément)."""
        hostile_but_contained = "raise RuntimeError('ne doit jamais atteindre le process de test')\n"
        provider = _FakeModelProvider(response_text=hostile_but_contained)
        task = self._a_solution_task()
        run = self.evalrunner.run_generated_solution(
            task_id=task.id, clone_id=self.clone.clone_id, endpoint=self.endpoint,
            method=EvalMethod.EXPERIMENTATION, provider=provider,
        )
        # Le test lui-même n'a jamais levé — la preuve que le code candidat
        # n'a jamais exécuté dans ce process. Le sandbox a réellement
        # constaté l'échec du sous-processus.
        self.assertIn(run.oracle_verdict, (OracleVerdict.FAIL, OracleVerdict.ERROR))

    # -- 5 : échecs propres, jamais d'exception remontée ---------------------

    def test_e_model_provider_failure_yields_a_clean_error_eval_run(self) -> None:
        provider = _FakeModelProvider()
        provider.should_fail = True
        task = self._a_solution_task()
        run = self.evalrunner.run_generated_solution(
            task_id=task.id, clone_id=self.clone.clone_id, endpoint=self.endpoint,
            method=EvalMethod.EXPERIMENTATION, provider=provider,
        )
        self.assertIs(run.oracle_verdict, OracleVerdict.ERROR)
        self.assertIsNone(run.model_id)
        self.assertIsNone(run.sandbox_status)

    def test_f_a_task_without_solution_harness_is_refused(self) -> None:
        task = self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        provider = _FakeModelProvider()
        with self.assertRaises(ValueError):
            self.evalrunner.run_generated_solution(
                task_id=task.id, clone_id=self.clone.clone_id, endpoint=self.endpoint,
                method=EvalMethod.EXPERIMENTATION, provider=provider,
            )

    def test_g_missing_task_raises_lookup_error(self) -> None:
        provider = _FakeModelProvider()
        with self.assertRaises(LookupError):
            self.evalrunner.run_generated_solution(
                task_id="does-not-exist", clone_id=self.clone.clone_id, endpoint=self.endpoint,
                method=EvalMethod.EXPERIMENTATION, provider=provider,
            )

    # -- 6 : trace réelle -----------------------------------------------------

    def test_h_trace_is_recorded_as_a_real_event_and_audit_row(self) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository, EventRepository

        provider = _FakeModelProvider(response_text=_CORRECT_IS_PALINDROME)
        task = self._a_solution_task()
        run = self.evalrunner.run_generated_solution(
            task_id=task.id, clone_id=self.clone.clone_id, endpoint=self.endpoint,
            method=EvalMethod.EXPERIMENTATION, provider=provider,
        )
        with session_scope(self.factory) as session:
            events = EventRepository(session).recent(limit=10)
            audits = AuditLogRepository(session).recent(limit=10)
        self.assertIn("evalrun.generated_solution_completed", [e.msg for e in events])
        audit_actions = [a.action for a in audits]
        self.assertIn("evalrun.generated_solution_completed", audit_actions)


class TestCompareGeneratedSolutions(unittest.TestCase):
    """7/8 : boucle multi-candidats (§11) — plusieurs stratégies, fitness
    réel par candidat (`compute_fitness`, réutilisé), le meilleur retenu."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.task_service = TaskBankService(self.factory)
        self.evalrunner = EvalRunnerService(self.factory, agent_runtime=self.runtime)
        self.clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:x", spawned_by=Subject.CORE,
        )
        self.endpoint = an_endpoint()
        spec = build_palindrome_task_spec()
        self.task = self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
            command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness, expected_answer=spec.expected_answer,
        )

    def test_a_the_configured_number_of_candidates_is_exactly_what_runs(self) -> None:
        provider = _FakeModelProvider(response_text=_CORRECT_IS_PALINDROME)
        strategies = [
            CandidateStrategy(label="a", genome_content={"generation": {"temperature": 0.2}}),
            CandidateStrategy(label="b", genome_content={"generation": {"temperature": 0.8}}),
            CandidateStrategy(label="c", genome_content=None),
        ]
        comparison = compare_generated_solutions(
            self.evalrunner, self.runtime, task_id=self.task.id, endpoint=self.endpoint,
            strategies=strategies, pool=a_pool(), provider=provider,
        )
        self.assertEqual(len(comparison.candidates), 3)

    def test_b_a_correct_strategy_is_selected_as_best_over_a_wrong_one(self) -> None:
        provider = _FakeModelProvider()
        provider.response_by_temperature = {0.1: _WRONG_IS_PALINDROME, 0.9: _CORRECT_IS_PALINDROME}
        strategies = [
            CandidateStrategy(label="low-temp-wrong", genome_content={"generation": {"temperature": 0.1}}),
            CandidateStrategy(label="high-temp-correct", genome_content={"generation": {"temperature": 0.9}}),
        ]
        comparison = compare_generated_solutions(
            self.evalrunner, self.runtime, task_id=self.task.id, endpoint=self.endpoint,
            strategies=strategies, pool=a_pool(), provider=provider,
        )
        self.assertIsNotNone(comparison.best)
        self.assertEqual(comparison.best.strategy_label, "high-temp-correct")
        self.assertIs(comparison.best.eval_run.oracle_verdict, OracleVerdict.PASS)
        # Le fitness du meilleur candidat est strictement supérieur à celui
        # du candidat raté — jamais un choix arbitraire.
        worst = next(c for c in comparison.candidates if c.strategy_label == "low-temp-wrong")
        self.assertGreater(comparison.best.fitness.value, worst.fitness.value)

    def test_c_empty_strategies_is_refused(self) -> None:
        provider = _FakeModelProvider()
        with self.assertRaises(ValueError):
            compare_generated_solutions(
                self.evalrunner, self.runtime, task_id=self.task.id,
                endpoint=self.endpoint, strategies=[], pool=a_pool(), provider=provider,
            )


def _real_ollama_url() -> str | None:
    """Constat réel — jamais supposé. Même patron que
    `tests/test_evalrunner_model_evaluation.py::_real_ollama_url`."""
    configured = os.environ.get("APEX_OLLAMA_URL")
    if configured:
        return configured
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=0.5):
            return "http://127.0.0.1:11434"
    except OSError:
        return None


@unittest.skipUnless(
    _real_ollama_url() is not None,
    "aucun Ollama réel joignable (définir APEX_OLLAMA_URL pour pointer vers le vôtre) — "
    "voir docs/HOW_TO_LAUNCH.md pour lancer ce test sur une machine avec Ollama réel",
)
class TestRealOllamaGeneratedSolution(unittest.TestCase):
    """Le SEUL test de ce fichier contre un VRAI Ollama local — sauté
    automatiquement dans cet environnement (Ollama non joignable, vérifié),
    jamais remplacé par une simulation présentée comme équivalente."""

    def test_a_a_real_local_model_attempts_the_palindrome_task_in_a_real_sandbox(self) -> None:
        url = _real_ollama_url()
        assert url is not None
        served_model = os.environ.get("APEX_OLLAMA_MODEL", "qwen2.5-coder:7b-instruct")
        endpoint = an_endpoint(url=url, served_model=served_model)

        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        task_service = TaskBankService(factory)
        evalrunner = EvalRunnerService(factory, agent_runtime=runtime)
        clone = runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:x", spawned_by=Subject.CORE)
        spec = build_palindrome_task_spec()
        task = task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
            command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness, expected_answer=spec.expected_answer,
        )
        run = evalrunner.run_generated_solution(
            task_id=task.id, clone_id=clone.clone_id, endpoint=endpoint, method=EvalMethod.EXPERIMENTATION,
        )
        self.assertIsNotNone(run.model_id)
        self.assertIsNotNone(run.sandbox_status)
        self.assertIn(run.oracle_verdict, (OracleVerdict.PASS, OracleVerdict.FAIL, OracleVerdict.ERROR))



class TestResultContract(unittest.TestCase):
    """Défaut CRITIQUE confirmé par le Continuous Improvement Engine
    (cycle 2) : juger au code de sortie laissait un candidat obtenir PASS
    sans résoudre la tâche. Sandbox RÉEL, oracle par défaut réel."""

    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.factory = build_session_factory(engine)
        self.runtime = AgentRuntime(self.factory)
        self.tasks = TaskBankService(self.factory)
        self.evalrunner = EvalRunnerService(self.factory, agent_runtime=self.runtime)
        self.endpoint = ModelEndpoint(
            pool_id="local", runtime="ollama", url="http://127.0.0.1:11434",
            served_model="qwen2.5-coder:7b-instruct", max_context=8192,
        )
        self.spec = build_palindrome_task_spec()
        self.task = self._task()

    def _task(self, *, harness: str | None = None, expected: str | None = "default"):
        return self.tasks.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description=self.spec.description,
            command=self.spec.command, created_by=Subject.HUMAN,
            solution_harness=harness or self.spec.solution_harness,
            expected_answer=self.spec.expected_answer if expected == "default" else expected,
        )

    def _run(self, code: str, task=None):
        clone = self.runtime.spawn(
            Subject.RESEARCH, pool=ComputePool(
                pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
                capabilities=(Capability.CPU_EVAL,),
            ), genome_ref="sha256:x", spawned_by=Subject.CORE,
        )
        provider = _FakeModelProvider(response_text=code)
        run = self.evalrunner.run_generated_solution(
            task_id=(task or self.task).id, clone_id=clone.clone_id, endpoint=self.endpoint,
            method=EvalMethod.EXPERIMENTATION, provider=provider,
        )
        return run, provider

    def test_a_candidates_that_do_not_solve_the_task_never_pass(self) -> None:
        cheats = {
            "sys_exit0": "import sys\nsys.exit(0)\n",
            "os_exit0": "import os\nos._exit(0)\n",
            "neutered_exit": "import sys\nsys.exit = lambda *a, **k: None\ndef is_palindrome(s):\n    return False\n",
            "forged_legacy_summary": "import json, os\nprint(json.dumps({'total': 6, 'failures': []}))\nos._exit(0)\n",
            "forged_contract_line": "print('APEX_RESULT:' + '{\"results\": []}')\ndef is_palindrome(s):\n    return True\n",
            "ints_instead_of_bools": "def is_palindrome(s):\n    c = s.lower()\n    return int(c == c[::-1])\n",
        }
        for label, code in cheats.items():
            with self.subTest(candidate=label):
                run, _ = self._run(code)
                self.assertIsNot(run.oracle_verdict, OracleVerdict.PASS, run.oracle_detail)

    def test_b_a_correct_candidate_passes_with_a_host_side_comparison(self) -> None:
        run, _ = self._run(_CORRECT_IS_PALINDROME)
        self.assertIs(run.oracle_verdict, OracleVerdict.PASS)
        self.assertIn("hors sandbox", run.oracle_detail)

    def test_c_expected_values_never_enter_the_sandboxed_script(self) -> None:
        self.assertNotIn(self.spec.expected_answer, self.spec.solution_harness)
        self.assertNotIn("True", self.spec.solution_harness)
        self.assertNotIn("False", self.spec.solution_harness)

    def test_d_a_contract_without_expected_values_is_an_error_never_a_pass(self) -> None:
        run, _ = self._run(_CORRECT_IS_PALINDROME, task=self._task(expected=None))
        self.assertIs(run.oracle_verdict, OracleVerdict.ERROR)

    def test_e_a_legacy_exit_code_harness_keeps_its_semantics(self) -> None:
        legacy = (
            "# APEX_CANDIDATE_CODE_HERE\n"
            "import sys\nsys.exit(0 if is_palindrome('abba') and not is_palindrome('ab') else 1)\n"
        )
        task = self._task(harness=legacy, expected=None)
        self.assertIs(self._run(_CORRECT_IS_PALINDROME, task=task)[0].oracle_verdict, OracleVerdict.PASS)
        self.assertIs(self._run(_WRONG_IS_PALINDROME, task=task)[0].oracle_verdict, OracleVerdict.FAIL)

    def test_g_the_project_tree_is_not_visible_from_the_sandbox(self) -> None:
        """Juge externe (cycle 2) : les sources du dépôt, qui définissent les
        cas cachés, étaient lisibles depuis le sandbox NAMESPACED."""
        from apex.sandbox.linux_namespace import _PROJECT_ROOT, project_root_to_mask

        self.assertEqual(project_root_to_mask(), os.path.realpath(_PROJECT_ROOT))
        code = f"import os\nprint('VISIBLE', os.listdir({_PROJECT_ROOT!r}))\n" + _CORRECT_IS_PALINDROME
        run, _ = self._run(code)
        self.assertIn("VISIBLE []", run.facts["candidate_execution"]["stdout"])
        self.assertIs(run.oracle_verdict, OracleVerdict.PASS)

    def test_g2_masking_never_targets_tmp_or_system_directories(self) -> None:
        """Défaut trouvé en mesurant le cycle 3 : un dépôt sous /tmp (déjà
        masqué par le tmpfs) faisait échouer TOUTE exécution sandboxée ; un
        paquet installé aurait masqué le répertoire de l'interpréteur."""
        import tempfile

        from apex.sandbox.linux_namespace import _PROJECT_ROOT, project_root_to_mask

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "apex"))
            open(os.path.join(tmp, "pyproject.toml"), "w").close()
            self.assertIsNone(project_root_to_mask(tmp))
        self.assertIsNone(project_root_to_mask("/usr/lib/python3"))
        self.assertIsNone(project_root_to_mask("/"))
        self.assertIsNone(project_root_to_mask(os.path.join(_PROJECT_ROOT, "docs")))

    def test_h_hidden_cases_reject_plausible_wrong_algorithms(self) -> None:
        wrong = {
            "extremites": "def is_palindrome(s):\n    return s[:1].lower() == s[-1:].lower()\n",
            "constante": "def is_palindrome(s):\n    return s != 'hello'\n",
            "sans_ponctuation": (
                "def is_palindrome(s):\n    t = ''.join(c for c in s.lower() if c.isalnum())\n"
                "    return t == t[::-1]\n"
            ),
        }
        for label, code in wrong.items():
            with self.subTest(candidate=label):
                self.assertIs(self._run(code)[0].oracle_verdict, OracleVerdict.FAIL)

    def test_i_a_contract_violation_is_classified_as_an_invalid_candidate(self) -> None:
        from apex.metalearner.failure_taxonomy import FailureCategory, classify_failure

        run, _ = self._run("import json, sys\nprint(json.dumps({'total': 6, 'failures': []}))\nsys.exit(0)\n")
        self.assertIs(run.oracle_verdict, OracleVerdict.FAIL)
        self.assertIs(classify_failure(run), FailureCategory.INVALID_CANDIDATE)

    def test_f_a_malformed_task_is_refused_before_any_model_call(self) -> None:
        task = self._task(harness="print('pas de marqueur')\n", expected=None)
        provider = _FakeModelProvider(response_text=_CORRECT_IS_PALINDROME)
        clone = self.runtime.spawn(
            Subject.RESEARCH, pool=ComputePool(
                pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
                capabilities=(Capability.CPU_EVAL,),
            ), genome_ref="sha256:x", spawned_by=Subject.CORE,
        )
        with self.assertRaises(MissingCandidateMarkerError):
            self.evalrunner.run_generated_solution(
                task_id=task.id, clone_id=clone.clone_id, endpoint=self.endpoint,
                method=EvalMethod.EXPERIMENTATION, provider=provider,
            )
        self.assertEqual(provider.received_requests, [])


if __name__ == "__main__":
    unittest.main()

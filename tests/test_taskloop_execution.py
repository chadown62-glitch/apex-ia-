"""Tests de `apex.taskloop.loop.run_task_loop` — le moteur d'orchestration
N+27, intégration RÉELLE (SQLite en mémoire + vraie `AgentRuntime` + vraie
sandbox namespaced pour l'exécution + vrai `Tier0Judge`/`ExitCodeOracle` +
vrai `apex.recovery.decide` + vrai `apex.routing.router.select_model`).
Seul le `ModelProvider` est un double (même discipline que `tests/
test_evalrunner_generated_solution.py`/`tests/test_routing_generation.py`) —
aucun Ollama réel n'est requis pour prouver que la boucle
sélection/génération/exécution/jugement/recovery est réelle.

**Limite honnête découverte pendant l'implémentation** : `Tier0Judge` ne
compose aujourd'hui que `ExitCodeOracle`, qui lie strictement
`ExecutionStatus.SUCCESS <=> OracleVerdict.PASS` — la branche
`FailureCategory.INCORRECT_SOLUTION` de `apex.taskloop.loop._run` (sandbox
RÉELLEMENT réussie mais Judge en désaccord) est donc INATTEIGNABLE par preuve
avec ce seul Oracle réel aujourd'hui (un « mauvais » candidat fait échouer le
harness lui-même, `sys.exit(1)`, donc `ExecutionStatus.FAILED`, jamais
`SUCCESS`) — exactement la même limite qu'`apex.recovery.model.
FailureCategory.INCORRECT_SOLUTION` documentait déjà pour N+26 (« un Judge
qui n'existe pas encore »). `TestIncorrectSolutionClassificationIsWired`
teste donc cette branche directement (fonction pure `_handle_failure`),
jamais fabriquée via un faux Oracle qui mentirait sur ce qui est réellement
observable — et `TestJudgeRejectionNeverCompletesTheTask` prouve la même
exigence de la mission (§ test 8 : « le modèle dit succès mais les tests/le
judge échouent -> jamais COMPLETED ») par le chemin RÉELLEMENT atteignable
(`EXECUTION_FAILURE`, un mauvais candidat qui fait échouer le harness)."""

from __future__ import annotations

import threading
import unittest

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, ModelEndpoint, ModelRef, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory, session_scope
from apex.db.models import Base
from apex.db.repositories import EventRepository
from apex.errors import ModelProviderError, TaskAlreadyRunning, TaskLoopAtCapacity
from apex.evalrunner.repository import EvalRunRepository
from apex.metalearner.experience import ExperienceRepository
from apex.metalearner.recommendation import RecommendationSource
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.models.registry import ModelCandidate
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement
from apex.policy.model import Subject
from apex.recovery import FailureCategory
from apex.recovery.policy import decide as recovery_decide
from apex.sandbox.linux_namespace import LinuxNamespaceSandbox
from apex.sandbox.model import IsolationLevel, SandboxCapabilityReport
from apex.taskgen.solution_tasks import build_palindrome_task_spec
from apex.taskloop.loop import _handle_failure, run_task_loop
from apex.taskloop.registry import TaskLoopHandle, TaskLoopRegistry
from apex.taskloop.request import TaskExecutionMode, TaskLimits, TaskRequest
from apex.taskloop.result import TaskLoopStatus
from apex.tasks.model import TaskSplit
from apex.tasks.repository import TaskRepository
from apex.tasks.service import TaskBankService

_CORRECT_IS_PALINDROME = (
    "def is_palindrome(s: str) -> bool:\n"
    "    cleaned = s.lower()\n"
    "    return cleaned == cleaned[::-1]\n"
)
_WRONG_IS_PALINDROME = "def is_palindrome(s: str) -> bool:\n    return False\n"


class _FakeModelProvider(ModelProvider):
    """Double fidèle au contrat `ModelProvider` — jamais un vrai modèle.
    `mode` contrôle le comportement : "ok" / "provider_error" / "timeout" /
    "blocked" (attend `release` — utilisé par le test d'idempotence).
    `detect_status` (par défaut `AVAILABLE`, comme avant la correction
    physique du routing) contrôle ce que rapporte `detect()` — utilisé par
    `TestPhysicalAvailabilityProbe` pour simuler un candidat déclaré mais
    physiquement absent, SANS jamais toucher `apex.models.registry`/
    `apex.routing.router`."""

    def __init__(
        self, *, mode: str = "ok", text: str = _CORRECT_IS_PALINDROME,
        detect_status: CapabilityStatus = CapabilityStatus.AVAILABLE,
    ) -> None:
        self.mode = mode
        self.text = text
        self.calls = 0
        self.detect_status = detect_status
        self.detect_calls = 0
        self.release: threading.Event | None = None

    def detect(self) -> ModelCapabilityReport:
        self.detect_calls += 1
        return ModelCapabilityReport(
            status=self.detect_status, backend="fake-test-double",
            detail="double de test — jamais un vrai backend",
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls += 1
        if self.mode == "blocked":
            assert self.release is not None
            self.release.wait(timeout=30.0)
        if self.mode == "provider_error":
            raise ModelProviderError(
                "FakeModelProvider.generate", endpoint_url=request.endpoint.url, detail="échec simulé",
            )
        return GenerationResult(
            text=self.text, model_used=request.endpoint.served_model,
            tokens_prompt=Measurement.unknown(), tokens_completion=Measurement.unknown(),
            finish_reason="stop", elapsed_s=0.001, facts={},
        )


def sqlite_session_factory():
    """`StaticPool` + `check_same_thread=False` — indispensable ici
    (`TestIdempotence` exerce une VRAIE concurrence multi-thread, même
    patron que `tests/test_scheduler_heartbeat.py`/`tests/
    test_learning_orchestrator.py`) : sans ça, chaque thread verrait sa
    propre base `:memory:` vide (`SingletonThreadPool`, le défaut
    SQLAlchemy pour `sqlite://`), pas la même base que le thread
    principal."""
    engine = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def a_pool(**overrides) -> ComputePool:
    defaults = dict(
        pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
        capabilities=(Capability.CPU_EVAL,),
    )
    defaults.update(overrides)
    return ComputePool(**defaults)


def a_ref(**overrides) -> ModelRef:
    defaults = dict(family="a-model", version="1", quantization="q4_k_m", capability_class="coder")
    defaults.update(overrides)
    return ModelRef(**defaults)


def an_endpoint(**overrides) -> ModelEndpoint:
    defaults = dict(
        pool_id="local", runtime="ollama", url="http://127.0.0.1:11434",
        served_model="a-model:1", max_context=8192,
    )
    defaults.update(overrides)
    return ModelEndpoint(**defaults)


def a_candidate(**overrides) -> ModelCandidate:
    defaults = dict(role="coder_main", ref=a_ref(), resident=False, endpoint=an_endpoint())
    defaults.update(overrides)
    return ModelCandidate(**defaults)


def _events(factory) -> list[dict]:
    """Ordre CHRONOLOGIQUE — même discipline que `tests/
    test_missions_execution_recovery.py` (`EventRepository.recent()` trie
    du plus récent au plus ancien)."""
    with session_scope(factory) as session:
        rows = EventRepository(session).recent(limit=500)
    return [e.context for e in reversed(rows)]


class _TaskLoopTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.pool = a_pool()
        spec = build_palindrome_task_spec()
        self.task = TaskBankService(self.factory).create(
            split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
            command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness,
        )

    def _request(self, **overrides) -> TaskRequest:
        defaults = dict(
            task_id=self.task.id, objective="résoudre is_palindrome",
            execution_mode=TaskExecutionMode.BOUNDED,
            limits=TaskLimits(max_attempts=3, max_retries=3, max_candidates=1, max_wall_clock_s=60.0),
        )
        defaults.update(overrides)
        return TaskRequest(**defaults)

    def _run(self, request, **kwargs):
        kwargs.setdefault("candidates", (a_candidate(),))
        kwargs.setdefault("provider_factory", lambda c: _FakeModelProvider())
        kwargs.setdefault("registry", TaskLoopRegistry())
        return run_task_loop(
            request, session_factory=self.factory, agent_runtime=self.runtime, pool=self.pool, **kwargs,
        )


class TestHappyPath(_TaskLoopTestCase):
    """1/16 : CREATED -> PLANNING -> PROVIDER_SELECTION -> GENERATING ->
    EXECUTING -> TESTING -> JUDGING -> COMPLETED."""

    def test_a_single_correct_attempt_completes(self) -> None:
        result = self._run(
            self._request(),
            provider_factory=lambda c: _FakeModelProvider(mode="ok", text=_CORRECT_IS_PALINDROME),
        )
        self.assertEqual(result.status, TaskLoopStatus.COMPLETED)
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.failure, None)
        self.assertIsNotNone(result.final_output)
        self.assertEqual(len(result.eval_run_ids), 1)


class TestRetryThenSuccess(_TaskLoopTestCase):
    """2/16 : attempt 1 FAIL (INCORRECT_SOLUTION, wrong-but-well-formed
    code that still exits 0 is impossible with ExitCodeOracle — voir la
    docstring du module — donc ce test retente sur une CATÉGORIE réellement
    atteignable : EXECUTION_FAILURE, via un candidat cassé au premier essai
    puis correct au second)."""

    def test_attempt_one_fails_attempt_two_succeeds(self) -> None:
        provider = _FakeModelProvider(mode="ok", text="x = 1\n")  # NameError -> FAILED

        def factory(_candidate):
            if provider.calls == 0:
                return provider
            return _FakeModelProvider(mode="ok", text=_CORRECT_IS_PALINDROME)

        result = self._run(self._request(), provider_factory=factory)
        self.assertEqual(result.status, TaskLoopStatus.COMPLETED)
        self.assertEqual(len(result.attempts), 2)
        self.assertEqual(result.attempts[0].category, FailureCategory.EXECUTION_FAILURE)
        self.assertEqual(result.attempts[0].decision.value, "retry")
        self.assertIsNone(result.attempts[1].category)

        kinds = [e.get("kind") for e in _events(self.factory) if e.get("task_id") == self.task.id]
        self.assertEqual(kinds.count("retry.started"), 1)
        self.assertEqual(kinds.count("recovery.decided"), 1)


class TestReassignment(_TaskLoopTestCase):
    """3/16 : provider A FAIL (provider_error) -> N+26 REASSIGN -> provider
    B -> succès."""

    def test_provider_a_fails_reassigns_to_provider_b_which_succeeds(self) -> None:
        candidate_a = a_candidate(ref=a_ref(family="model-a"), endpoint=an_endpoint(served_model="model-a"))
        candidate_b = a_candidate(ref=a_ref(family="model-b"), endpoint=an_endpoint(served_model="model-b"))
        provider_a = _FakeModelProvider(mode="provider_error")
        provider_b = _FakeModelProvider(mode="ok", text=_CORRECT_IS_PALINDROME)

        def factory(candidate: ModelCandidate) -> ModelProvider:
            return provider_a if candidate.ref.family == "model-a" else provider_b

        result = self._run(
            self._request(limits=TaskLimits(max_attempts=3, max_retries=1, max_candidates=2, max_wall_clock_s=60.0)),
            candidates=(candidate_a, candidate_b), provider_factory=factory,
        )
        self.assertEqual(result.status, TaskLoopStatus.COMPLETED)
        self.assertEqual(result.providers_tried, (candidate_a.ref.id, candidate_b.ref.id))
        self.assertEqual(result.attempts[0].category, FailureCategory.PROVIDER_FAILURE)
        self.assertEqual(result.attempts[0].decision.value, "reassign")
        self.assertEqual(provider_a.calls, 1)
        self.assertEqual(provider_b.calls, 1)

        kinds = [e.get("kind") for e in _events(self.factory) if e.get("task_id") == self.task.id]
        self.assertIn("reassignment.decided", kinds)


class TestExhaustion(_TaskLoopTestCase):
    """4/16 : max_attempts atteint -> FAILED, jamais de tentative 4.

    `no_progress_patience` volontairement large ici : le même échec
    identique répété relèverait autrement `NO_PROGRESS` avant même
    d'épuiser `max_attempts` (garde-fou DISTINCT, testé isolément par
    `TestNoProgress`) — ce test isole SPÉCIFIQUEMENT la borne
    `max_attempts`."""

    def test_max_attempts_reached_stops_at_exactly_that_many_attempts(self) -> None:
        provider = _FakeModelProvider(mode="ok", text="x = 1\n")  # NameError à chaque tentative
        result = self._run(
            self._request(limits=TaskLimits(max_attempts=3, max_retries=5, max_candidates=1, max_wall_clock_s=60.0)),
            provider_factory=lambda c: provider, no_progress_patience=10,
        )
        self.assertEqual(result.status, TaskLoopStatus.FAILED)
        self.assertEqual(len(result.attempts), 3)
        self.assertEqual(provider.calls, 3)
        self.assertEqual(result.attempts[-1].decision.value, "block")


class TestCancellation(_TaskLoopTestCase):
    """5/16 : CANCELLED -> aucun retry, aucune tentative supplémentaire."""

    def test_a_pre_cancelled_token_stops_before_any_attempt(self) -> None:
        from apex.sandbox import CancellationToken

        handle = TaskLoopHandle(task_id=self.task.id, trace_id="t")
        handle.cancel_token.request_cancel()
        registry = TaskLoopRegistry()
        # Injecte directement le handle déjà annulé — `run_task_loop` en
        # crée normalement un neuf ; ce test vérifie `check_stop` seul.
        registry.track(handle)
        registry.discard(self.task.id)  # libère la place pour le run réel

        provider = _FakeModelProvider(mode="ok", text=_CORRECT_IS_PALINDROME)
        # `run_task_loop` ne prend pas de cancel_token externe directement
        # (chaque exécution a le sien, propre à son `TaskLoopHandle`) — la
        # seule façon honnête de tester CANCELLED ici est via l'annulation
        # RÉELLE en cours d'exécution (sandbox), couverte séparément par
        # `apex.sandbox`/`apex.missions.execution` (N+26). Ce test vérifie
        # directement `check_stop` avec un token pré-annulé, la même
        # fonction PURE que `run_task_loop` consulte à chaque tentative.
        from apex.taskloop.model import StopReason, TaskLoopStopCriteria, check_stop

        criteria = TaskLoopStopCriteria(
            max_attempts=3, max_wall_clock_s=60.0, resource_budget_description="test",
            success_criteria=lambda _r: False, failure_criteria=lambda _r: False,
        )
        cancelled_token = CancellationToken()
        cancelled_token.request_cancel()
        reason = check_stop(attempt_number=1, elapsed_s=0.0, criteria=criteria, cancel_token=cancelled_token)
        self.assertEqual(reason, StopReason.CANCELLED)
        self.assertEqual(provider.calls, 0)


class TestNoProgress(_TaskLoopTestCase):
    """6/16 : même échec répété -> NO_PROGRESS, arrêt AVANT max_attempts."""

    def test_identical_failures_stop_before_the_attempt_budget_is_spent(self) -> None:
        provider = _FakeModelProvider(mode="ok", text="x = 1\n")  # toujours le même échec
        result = self._run(
            self._request(limits=TaskLimits(max_attempts=10, max_retries=10, max_candidates=1, max_wall_clock_s=60.0)),
            provider_factory=lambda c: provider, no_progress_patience=2,
        )
        self.assertEqual(result.status, TaskLoopStatus.FAILED)
        self.assertEqual(result.stop_reason, "no_progress")
        self.assertEqual(len(result.attempts), 2, "patience=2 -> arrêt après 2 échecs identiques, jamais 10")
        self.assertLess(provider.calls, 10)


class TestSandboxUnavailable(_TaskLoopTestCase):
    """7/16 : sandbox indisponible -> BLOCKED (jamais FAILED — structurel)."""

    def test_isolation_unavailable_blocks_immediately(self) -> None:
        fake_report = SandboxCapabilityReport(
            level=IsolationLevel.RLIMIT_ONLY, namespaces_available=False, cgroups_available=False,
            privilege_drop_available=False, network_isolation_available=False,
            docker_daemon_reachable=False, seccomp_available=False, detail="test: forced unavailable",
        )
        unavailable_sandbox = LinuxNamespaceSandbox(capability_report=fake_report)
        result = self._run(
            self._request(), sandbox=unavailable_sandbox,
            provider_factory=lambda c: _FakeModelProvider(mode="ok", text=_CORRECT_IS_PALINDROME),
        )
        self.assertEqual(result.status, TaskLoopStatus.BLOCKED)
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].category, FailureCategory.SANDBOX_FAILURE)


class TestJudgeRejectionNeverCompletesTheTask(_TaskLoopTestCase):
    """8/16 : le modèle produit une réponse, mais le Judge (`ExitCodeOracle`,
    via le harness caché) la juge incorrecte -> jamais COMPLETED."""

    def test_a_wrong_but_well_formed_candidate_never_completes(self) -> None:
        result = self._run(
            self._request(limits=TaskLimits(max_attempts=1, max_retries=0, max_candidates=1, max_wall_clock_s=60.0)),
            provider_factory=lambda c: _FakeModelProvider(mode="ok", text=_WRONG_IS_PALINDROME),
        )
        self.assertNotEqual(result.status, TaskLoopStatus.COMPLETED)
        self.assertEqual(result.status, TaskLoopStatus.FAILED)
        self.assertIsNone(result.final_output)


class TestIncorrectSolutionClassificationIsWired(unittest.TestCase):
    """8bis/16 : preuve UNITAIRE que `FailureCategory.INCORRECT_SOLUTION`
    est réellement câblée dans `_handle_failure` (RETRY tant que le budget
    n'est pas épuisé, ESCALATE ensuite) — voir la docstring du module pour
    pourquoi ce chemin n'est pas atteignable end-to-end avec le seul Oracle
    réel de ce dépôt aujourd'hui."""

    def test_incorrect_solution_retries_then_escalates(self) -> None:
        from apex.config.loader import load_config
        from apex.recovery import AttemptHistory
        from apex.taskloop.model import TaskLoopStage

        policy = load_config().recovery
        from dataclasses import replace

        policy = replace(policy, max_attempts=2)
        outcome = _handle_failure(
            category=FailureCategory.INCORRECT_SOLUTION, failure_text="mauvaise réponse",
            provider_name="m", sandbox_level="namespaced", duration_s=0.1, attempt_number=1,
            effective_policy=policy, history=AttemptHistory(), eligible_candidates=(a_candidate(),),
            candidate_index=0, request=TaskRequest(
                task_id="t", objective="o", limits=TaskLimits(max_attempts=2, max_retries=5, max_candidates=1),
            ),
            retries_used=0, stage=TaskLoopStage.JUDGE, no_progress_patience=99, emit=lambda *_a: None,
        )
        self.assertIsNone(outcome.terminal)
        self.assertEqual(outcome.stage, TaskLoopStage.EXECUTION)

        outcome2 = _handle_failure(
            category=FailureCategory.INCORRECT_SOLUTION, failure_text="mauvaise réponse",
            provider_name="m", sandbox_level="namespaced", duration_s=0.1, attempt_number=2,
            effective_policy=policy, history=AttemptHistory(), eligible_candidates=(a_candidate(),),
            candidate_index=0, request=TaskRequest(
                task_id="t", objective="o", limits=TaskLimits(max_attempts=2, max_retries=5, max_candidates=1),
            ),
            retries_used=1, stage=TaskLoopStage.JUDGE, no_progress_patience=99, emit=lambda *_a: None,
        )
        self.assertEqual(outcome2.terminal, TaskLoopStatus.FAILED)
        self.assertEqual(outcome2.stop_reason, "recovery_escalated")
        self.assertEqual(outcome2.stage, TaskLoopStage.BLOCK)


class TestInvalidTransition(unittest.TestCase):
    """9/16 : une transition d'étape absente de la table -> rejet explicite
    (`InvalidTaskLoopTransition`), jamais silencieusement acceptée. Preuve
    directe sur la même fonction que `run_task_loop` utilise
    (`_advance_stage`), plus la nouvelle transition N+27 (`EXECUTION ->
    ANALYZE`, additive) elle-même testée pour ne PAS régresser la machine à
    états N+26."""

    def test_execution_to_analyze_is_now_legal_additive_transition(self) -> None:
        from apex.taskloop.model import TaskLoopStage, assert_stage_transition_allowed

        assert_stage_transition_allowed(TaskLoopStage.EXECUTION, TaskLoopStage.ANALYZE)  # ne lève pas

    def test_execution_to_complete_directly_is_still_illegal(self) -> None:
        from apex.taskloop.model import InvalidTaskLoopTransition, TaskLoopStage, assert_stage_transition_allowed

        with self.assertRaises(InvalidTaskLoopTransition):
            assert_stage_transition_allowed(TaskLoopStage.EXECUTION, TaskLoopStage.COMPLETE)


class TestStopCriteria(_TaskLoopTestCase):
    """10/16 : aucune tentative supplémentaire après la limite explicite."""

    def test_wall_clock_budget_stops_the_loop(self) -> None:
        provider = _FakeModelProvider(mode="ok", text="x = 1\n")
        result = self._run(
            self._request(limits=TaskLimits(max_attempts=100, max_retries=100, max_candidates=1, max_wall_clock_s=1e-9)),
            provider_factory=lambda c: provider,
        )
        self.assertEqual(result.status, TaskLoopStatus.FAILED)
        self.assertEqual(result.stop_reason, "wall_clock_exceeded")
        self.assertEqual(len(result.attempts), 0)
        self.assertEqual(provider.calls, 0)


class TestObservability(_TaskLoopTestCase):
    """11/16 : les événements attendus sont réellement présents."""

    def test_happy_path_emits_the_expected_event_kinds(self) -> None:
        self._run(
            self._request(limits=TaskLimits(max_attempts=1, max_retries=0, max_candidates=1)),
            provider_factory=lambda c: _FakeModelProvider(mode="ok", text=_CORRECT_IS_PALINDROME),
        )
        kinds = [e.get("kind") for e in _events(self.factory) if e.get("task_id") == self.task.id]
        for expected in (
            "task.created", "task.planning.started", "task.planning.completed",
            "provider.selection.started", "provider.selected",
            "task.attempt.started", "candidate.generation.started", "candidate.generated",
            "execution.started", "execution.completed", "test.started", "test.completed",
            "judge.started", "judge.completed", "task.attempt.completed", "task.completed",
        ):
            self.assertIn(expected, kinds, f"événement manquant : {expected}")
        # Jamais d'événement de recovery sur un succès du premier coup.
        self.assertNotIn("recovery.decided", kinds)


class TestExperience(_TaskLoopTestCase):
    """12/16 : chaque tentative jugée produit une Experience cohérente avec
    les événements réellement produits."""

    def test_experience_is_persisted_and_matches_the_eval_run(self) -> None:
        result = self._run(
            self._request(limits=TaskLimits(max_attempts=1, max_retries=0, max_candidates=1)),
            provider_factory=lambda c: _FakeModelProvider(mode="ok", text=_CORRECT_IS_PALINDROME),
        )
        self.assertEqual(result.status, TaskLoopStatus.COMPLETED)
        experiences = ExperienceRepository(self.factory).list_by_task(self.task.id)
        self.assertEqual(len(experiences), 1)
        self.assertEqual(experiences[0].eval_run_id, result.eval_run_ids[0])
        self.assertEqual(experiences[0].oracle_verdict.value, "pass")

        eval_run = EvalRunRepository(self.factory).get(result.eval_run_ids[0])
        self.assertIsNotNone(eval_run)
        self.assertEqual(eval_run.task_id, self.task.id)


class TestEvalRunCapturesStdoutStderr(_TaskLoopTestCase):
    """Correction d'observabilité (risque identifié pendant le diagnostic
    physique N+27 bis) : avant cette correction, `_persist_eval_run` ne
    copiait que `sandbox_result.facts` (cgroup/conteneur, propre au
    backend sandbox) dans `EvalRun.facts` — jamais `sandbox_result.
    stdout`/`.stderr`, la seule preuve textuelle disponible pour expliquer
    un `exit_code` non nul (`FailureCategory.EXECUTION_FAILURE`) après
    coup, sans ré-exécuter la tentative. Même clé/troncature que
    `apex.evalrunner.service.EvalRunnerService._candidate_execution_facts`
    (`facts["candidate_execution"]`), jamais un second format."""

    def test_a_successful_run_persists_the_real_harness_stdout(self) -> None:
        result = self._run(
            self._request(limits=TaskLimits(max_attempts=1, max_retries=0, max_candidates=1)),
            provider_factory=lambda c: _FakeModelProvider(mode="ok", text=_CORRECT_IS_PALINDROME),
        )
        self.assertEqual(result.status, TaskLoopStatus.COMPLETED)
        eval_run = EvalRunRepository(self.factory).get(result.eval_run_ids[0])
        execution_facts = eval_run.facts["candidate_execution"]
        # Le harness (`apex.taskgen.solution_tasks`) imprime un JSON réel
        # `{"total": ..., "failures": []}` sur un succès — preuve que ceci
        # est bien la sortie RÉELLE du sous-processus, jamais une valeur
        # fabriquée par ce test.
        self.assertIn('"failures": []', execution_facts["stdout"])
        self.assertEqual(execution_facts["stderr"], "")
        self.assertFalse(execution_facts["stdout_truncated"])
        self.assertFalse(execution_facts["stderr_truncated"])

    def test_b_a_failed_run_persists_the_real_harness_stderr(self) -> None:
        result = self._run(
            self._request(limits=TaskLimits(max_attempts=1, max_retries=0, max_candidates=1)),
            provider_factory=lambda c: _FakeModelProvider(mode="ok", text="x = 1\n"),  # NameError
        )
        self.assertIn(result.status, (TaskLoopStatus.FAILED, TaskLoopStatus.BLOCKED))
        self.assertEqual(len(result.eval_run_ids), 1)
        eval_run = EvalRunRepository(self.factory).get(result.eval_run_ids[0])
        execution_facts = eval_run.facts["candidate_execution"]
        # `sys.exit(1)` du harness imprime "ECHEC: ..." sur stderr AVANT de
        # sortir (voir `_HARNESS_TEMPLATE`) — preuve réelle, jamais
        # fabriquée, que cette tentative a échoué et pourquoi.
        self.assertIn("ECHEC", execution_facts["stderr"])
        self.assertIn('"failures"', execution_facts["stdout"])


class TestRecommendationBoundary(unittest.TestCase):
    """13/16 : une Recommendation (N+28) ne peut structurellement pas
    déclencher une exécution — le contrat `RecommendationSource` n'a
    aucune méthode `decide`/`apply`/`enforce` (déjà vérifié par N+26,
    reconfirmé ici du point de vue N+27 : aucun import d'`apex.taskloop`
    n'existe dans `apex.metalearner.recommendation`, donc aucune
    Recommendation ne peut techniquement appeler `run_task_loop`)."""

    def test_recommendation_source_has_no_execution_authority(self) -> None:
        for forbidden in ("decide", "apply", "enforce", "execute", "run"):
            self.assertFalse(hasattr(RecommendationSource, forbidden))

    def test_the_recommendation_module_never_imports_taskloop(self) -> None:
        import inspect

        import apex.metalearner.recommendation as mod

        import_lines = [
            line for line in inspect.getsource(mod).splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        self.assertFalse(any("apex.taskloop" in line for line in import_lines))


class TestIdempotence(_TaskLoopTestCase):
    """14/16 : le même `task_id` ne lance jamais deux exécutions
    accidentelles concurrentes."""

    def test_a_second_concurrent_run_for_the_same_task_is_refused(self) -> None:
        release = threading.Event()
        blocked_provider = _FakeModelProvider(mode="blocked")
        blocked_provider.release = release
        shared_registry = TaskLoopRegistry()

        errors: list[BaseException] = []

        def run_first() -> None:
            try:
                self._run(self._request(), provider_factory=lambda c: blocked_provider, registry=shared_registry)
            except BaseException as exc:  # noqa: BLE001 - capturé pour assertion dans le thread principal
                errors.append(exc)

        thread = threading.Thread(target=run_first)
        thread.start()
        try:
            # Laisse le premier run réellement atteindre la génération
            # (donc `TaskLoopRegistry.track` réellement effectué) avant de
            # tenter le second.
            for _ in range(200):
                if self.task.id in shared_registry:
                    break
                threading.Event().wait(0.01)
            self.assertIn(self.task.id, shared_registry)
            with self.assertRaises(TaskAlreadyRunning):
                self._run(
                    self._request(), provider_factory=lambda c: _FakeModelProvider(mode="ok"),
                    registry=shared_registry,
                )
        finally:
            release.set()
            thread.join(timeout=10)
        self.assertEqual(errors, [])

    def test_capacity_limit_refuses_a_task_beyond_max_concurrent(self) -> None:
        registry = TaskLoopRegistry(max_concurrent=1)
        registry.track(TaskLoopHandle(task_id="already-running", trace_id="t1"))
        with self.assertRaises(TaskLoopAtCapacity):
            registry.track(TaskLoopHandle(task_id="a-different-task", trace_id="t2"))


class TestProviderSelectionReuse(_TaskLoopTestCase):
    """15/16 : `run_task_loop` réutilise `select_model` — un candidat sans
    endpoint réel est exclu, jamais silencieusement essayé."""

    def test_a_candidate_without_a_real_endpoint_is_never_attempted(self) -> None:
        unavailable = a_candidate(ref=a_ref(family="unavailable"), endpoint=None)
        available = a_candidate(ref=a_ref(family="available"))
        provider = _FakeModelProvider(mode="ok", text=_CORRECT_IS_PALINDROME)
        calls_by_family: list[str] = []

        def factory(candidate: ModelCandidate) -> ModelProvider:
            calls_by_family.append(candidate.ref.family)
            return provider

        result = self._run(
            self._request(), candidates=(unavailable, available), provider_factory=factory,
        )
        self.assertEqual(result.status, TaskLoopStatus.COMPLETED)
        self.assertNotIn("unavailable", calls_by_family)
        self.assertEqual(result.providers_tried, (available.ref.id,))

    def test_no_eligible_candidate_at_all_blocks_without_any_attempt(self) -> None:
        unavailable = a_candidate(ref=a_ref(family="unavailable"), endpoint=None)
        result = self._run(self._request(), candidates=(unavailable,))
        self.assertEqual(result.status, TaskLoopStatus.BLOCKED)
        self.assertEqual(len(result.attempts), 0)


class TestRecoveryIntegration(_TaskLoopTestCase):
    """16/16 : `run_task_loop` appelle RÉELLEMENT `apex.recovery.decide` —
    jamais une duplication locale de sa logique. Preuve : la décision
    observée dans la trace est BIT-À-BIT identique à ce que `decide()`
    produit directement pour les mêmes arguments."""

    def test_the_recovery_decision_event_matches_apex_recovery_decide_directly(self) -> None:
        from apex.config.loader import load_config

        provider = _FakeModelProvider(mode="ok", text="x = 1\n")
        result = self._run(
            self._request(limits=TaskLimits(max_attempts=1, max_retries=0, max_candidates=1)),
            provider_factory=lambda c: provider,
        )
        self.assertEqual(result.status, TaskLoopStatus.FAILED)

        decided_events = [
            e for e in _events(self.factory)
            if e.get("task_id") == self.task.id and e.get("kind") == "recovery.decided"
        ]
        self.assertEqual(len(decided_events), 1)

        from dataclasses import replace

        # Même politique EFFECTIVE que `_run` (`_effective_policy` —
        # `TaskLimits.max_attempts` surcharge `RecoveryPolicy.max_attempts`
        # pour CETTE requête), jamais la politique globale non surchargée.
        effective_policy = replace(load_config().recovery, max_attempts=1)
        expected = recovery_decide(
            FailureCategory.EXECUTION_FAILURE, attempt_number=1, policy=effective_policy,
            has_alternate_target=False, previous_failure=decided_events[0]["reason"],
        )
        self.assertEqual(decided_events[0]["action"], expected.action.value)
        self.assertEqual(decided_events[0]["category"], expected.category.value)


class TestPhysicalAvailabilityProbe(_TaskLoopTestCase):
    """Correction physique du routing (mission N+27 bis) : un candidat
    DÉCLARÉ (`endpoint` réel, `is_available` True — le seul signal
    qu'utilisait `select_model` avant cette correction) mais dont le
    `ModelProvider.detect()` réel constate l'ABSENCE physique (modèle non
    installé, backend injoignable) ne doit JAMAIS être tenté — ni compté
    dans `providers_tried`, ni appeler `generate()` — exactement le bug
    diagnostiqué sur la machine réelle de l'utilisateur
    (`devstral-2:22b-q4_K_M` déclaré en config, jamais installé sur son
    Ollama). `apex.models.registry`/`apex.routing.router` restent
    inchangés — la preuve, ici, est que `select_model` ne voit MÊME PAS le
    candidat physiquement mort (`apex.routing.availability.
    probe_physical_availability` l'exclut avant)."""

    def test_a_declared_but_physically_absent_candidate_is_never_attempted_when_an_alternative_exists(
        self,
    ) -> None:
        ghost = a_candidate(ref=a_ref(family="ghost-model"), endpoint=an_endpoint(served_model="ghost-model"))
        real = a_candidate(ref=a_ref(family="real-model"), endpoint=an_endpoint(served_model="real-model"))
        ghost_provider = _FakeModelProvider(detect_status=CapabilityStatus.UNAVAILABLE_ON_DEV)
        real_provider = _FakeModelProvider(mode="ok", text=_CORRECT_IS_PALINDROME)

        def factory(candidate: ModelCandidate) -> ModelProvider:
            return ghost_provider if candidate.ref.family == "ghost-model" else real_provider

        result = self._run(
            self._request(
                limits=TaskLimits(max_attempts=3, max_retries=3, max_candidates=2, max_wall_clock_s=60.0),
            ),
            candidates=(ghost, real), provider_factory=factory,
        )

        self.assertEqual(result.status, TaskLoopStatus.COMPLETED)
        self.assertEqual(result.providers_tried, (real.ref.id,))
        self.assertEqual(ghost_provider.calls, 0)  # generate() jamais appelé
        self.assertEqual(ghost_provider.detect_calls, 1)  # sondé une seule fois
        self.assertEqual(real_provider.detect_calls, 1)

        events = [e for e in _events(self.factory) if e.get("task_id") == self.task.id]
        probe_events = [e for e in events if e.get("kind") == "provider.availability_probe.completed"]
        self.assertEqual(len(probe_events), 1)
        self.assertEqual(probe_events[0]["survivor_count"], 1)
        self.assertEqual(probe_events[0]["probed_endpoints"], 2)
        self.assertEqual(len(probe_events[0]["physically_unavailable"]), 1)
        self.assertEqual(probe_events[0]["physically_unavailable"][0]["model_id"], ghost.ref.id)

    def test_all_candidates_physically_absent_blocks_without_any_attempt(self) -> None:
        ghost_a = a_candidate(ref=a_ref(family="ghost-a"), endpoint=an_endpoint(served_model="ghost-a"))
        ghost_b = a_candidate(ref=a_ref(family="ghost-b"), endpoint=an_endpoint(served_model="ghost-b"))
        provider = _FakeModelProvider(detect_status=CapabilityStatus.UNAVAILABLE_ON_DEV)

        result = self._run(
            self._request(), candidates=(ghost_a, ghost_b), provider_factory=lambda c: provider,
        )

        self.assertEqual(result.status, TaskLoopStatus.BLOCKED)
        self.assertEqual(len(result.attempts), 0)
        self.assertEqual(result.providers_tried, ())
        self.assertEqual(provider.calls, 0)
        self.assertEqual(provider.detect_calls, 2)  # deux endpoints distincts, chacun sondé une fois

    def test_a_shared_physical_endpoint_across_two_roles_is_probed_only_once(self) -> None:
        shared_endpoint = an_endpoint(served_model="shared-model")
        role_a = a_candidate(role="coder_main", ref=a_ref(family="shared-model"), endpoint=shared_endpoint)
        role_b = a_candidate(
            role="reasoner", ref=a_ref(family="shared-model", capability_class="reasoning"),
            endpoint=shared_endpoint,
        )
        provider = _FakeModelProvider(mode="ok", text=_CORRECT_IS_PALINDROME)

        result = self._run(
            self._request(), candidates=(role_a, role_b), provider_factory=lambda c: provider,
        )

        self.assertEqual(result.status, TaskLoopStatus.COMPLETED)
        self.assertEqual(provider.detect_calls, 1)  # même endpoint physique, une seule sonde

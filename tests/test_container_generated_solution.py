"""Chaîne complète d'une solution générée exécutée dans `ContainerSandbox`
(Docker réel) : TASK -> MODEL -> SOLUTION -> CONTAINER -> ORACLE ->
EXPERIENCE -> DÉCISION SUIVANTE.

Sautée quand aucun démon Docker ne satisfait empiriquement les garanties de
`detect_container_capabilities` — jamais simulée. Le modèle reste un double
scripté : ce fichier valide le sandbox conteneur, pas un modèle réel."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from sqlalchemy import create_engine

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, ModelEndpoint, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.evalrunner.model import EvalMethod
from apex.evalrunner.service import EvalRunnerService
from apex.metalearner.adaptive_solution_loop import AdaptiveLoopConfig, run_adaptive_learning_loop
from apex.metalearner.experience import ExperienceRepository, SuccessEvidence
from apex.metalearner.failure_taxonomy import FailureCategory, classify_failure
from apex.metalearner.generated_solution_cycle import CandidateStrategy
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement
from apex.oracles.model import OracleVerdict
from apex.policy.model import Subject
from apex.sandbox.container import ContainerSandbox, detect_container_capabilities, docker_cli_available
from apex.sandbox.model import ExecutionStatus, IsolationLevel, ResourceLimits
from apex.taskgen.solution_tasks import build_palindrome_task_spec
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService

REPO_ROOT = Path(__file__).resolve().parent.parent
_SENTINEL_ENV = "APEX_CONTAINER_TEST_SENTINEL_SECRET"

_CORRECT = "def is_palindrome(s):\n    c = s.lower()\n    return c == c[::-1]\n"
_WRONG = "def is_palindrome(s):\n    return False\n"
_EXIT_EARLY = "import sys\nsys.exit(0)\n"
_INFINITE = "while True:\n    pass\n"
_MEMORY_HOG = "_blocks = [bytearray(64 * 1024 * 1024) for _ in range(64)]\n" + _CORRECT
_HOST_PROBE = (
    "import json, os\n"
    "print('PROBE:' + json.dumps({\n"
    f"    'repo_visible': os.path.exists({str(REPO_ROOT)!r}),\n"
    f"    'pyproject_visible': os.path.exists({str(REPO_ROOT / 'pyproject.toml')!r}),\n"
    "    'env_keys': sorted(os.environ),\n"
    "    'uid': os.getuid(),\n"
    "}))\n" + _CORRECT
)


def _available() -> bool:
    return docker_cli_available() and detect_container_capabilities().is_available


class _Scripted(ModelProvider):
    def __init__(self, text_by_temperature: dict[float | None, str], default: str = _WRONG) -> None:
        self._texts = text_by_temperature
        self._default = default
        self.temperatures: list[float | None] = []

    def detect(self) -> ModelCapabilityReport:
        return ModelCapabilityReport(status=CapabilityStatus.AVAILABLE, backend="scripted", detail="double")

    def generate(self, request: GenerationRequest) -> GenerationResult:
        temperature = round(request.temperature, 6) if request.temperature is not None else None
        self.temperatures.append(temperature)
        return GenerationResult(
            text=self._texts.get(temperature, self._default),
            model_used="scripted",
            tokens_prompt=Measurement.real(1),
            tokens_completion=Measurement.real(1),
            finish_reason="stop",
            elapsed_s=0.0,
            facts={},
        )


@unittest.skipUnless(
    _available(), "aucun démon Docker réel satisfaisant les garanties de ContainerSandbox ici"
)
class TestGeneratedSolutionInContainer(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.factory = build_session_factory(engine)
        self.runtime = AgentRuntime(self.factory)
        self.sandbox = ContainerSandbox()
        self.evalrunner = EvalRunnerService(self.factory, agent_runtime=self.runtime, sandbox=self.sandbox)
        self.experiences = ExperienceRepository(self.factory)
        self.pool = ComputePool(
            pool_id="local",
            location=PoolLocation.LOCAL,
            trust_level=TrustLevel.TRUSTED,
            capabilities=(Capability.CPU_EVAL,),
        )
        self.endpoint = ModelEndpoint(
            pool_id="local",
            runtime="ollama",
            url="http://127.0.0.1:11434",
            served_model="scripted",
            max_context=8192,
        )
        spec = build_palindrome_task_spec()
        self.task = TaskBankService(self.factory).create(
            split=TaskSplit.TRAIN,
            specialty="research",
            title=spec.title,
            description=spec.description,
            command=spec.command,
            created_by=Subject.HUMAN,
            solution_harness=spec.solution_harness,
            expected_answer=spec.expected_answer,
        )
        self.limits = ResourceLimits(
            cpu_cores=1.0, memory_mb=256, pids_limit=64, timeout_s=20.0, disk_mb=64, network="none"
        )

    def _run(self, code: str, *, limits: ResourceLimits | None = None):
        clone = self.runtime.spawn(
            Subject.RESEARCH, pool=self.pool, genome_ref="sha256:c", spawned_by=Subject.CORE
        )
        return self.evalrunner.run_generated_solution(
            task_id=self.task.id,
            clone_id=clone.clone_id,
            endpoint=self.endpoint,
            method=EvalMethod.EXPERIMENTATION,
            provider=_Scripted({}, default=code),
            limits=limits or self.limits,
        )

    def _stdout(self, run) -> str:
        return (run.facts.get("candidate_execution") or {}).get("stdout") or ""

    def test_a_a_valid_solution_passes_with_containerized_isolation(self) -> None:
        run = self._run(_CORRECT)
        self.assertIs(run.oracle_verdict, OracleVerdict.PASS)
        self.assertIs(run.sandbox_status, ExecutionStatus.SUCCESS)
        self.assertEqual(run.facts["image"], "python:3.12-slim")
        self.assertTrue(run.facts["isolation_detail"].startswith(IsolationLevel.CONTAINERIZED.name))

    def test_b_a_wrong_solution_fails_as_incorrect(self) -> None:
        run = self._run(_WRONG)
        self.assertIs(run.oracle_verdict, OracleVerdict.FAIL)
        self.assertIs(classify_failure(run), FailureCategory.INCORRECT_SOLUTION)

    def test_c_an_early_exit_never_passes(self) -> None:
        run = self._run(_EXIT_EARLY)
        self.assertIs(run.oracle_verdict, OracleVerdict.FAIL)
        self.assertIs(classify_failure(run), FailureCategory.INVALID_CANDIDATE)

    def test_d_the_host_repository_and_host_secrets_are_not_visible(self) -> None:
        os.environ[_SENTINEL_ENV] = "must-never-reach-the-container"
        try:
            run = self._run(_HOST_PROBE)
        finally:
            os.environ.pop(_SENTINEL_ENV, None)
        self.assertIs(run.oracle_verdict, OracleVerdict.PASS)
        probe_lines = [line for line in self._stdout(run).splitlines() if line.startswith("PROBE:")]
        self.assertEqual(len(probe_lines), 1)
        probe = json.loads(probe_lines[0].removeprefix("PROBE:"))
        self.assertFalse(probe["repo_visible"])
        self.assertFalse(probe["pyproject_visible"])
        self.assertNotIn(_SENTINEL_ENV, probe["env_keys"])
        self.assertFalse([k for k in probe["env_keys"] if "PASSWORD" in k or "SECRET" in k or "TOKEN" in k])
        self.assertNotEqual(probe["uid"], 0)

    def test_e_an_infinite_loop_is_killed_and_judged_timeout(self) -> None:
        limits = ResourceLimits(
            cpu_cores=1.0, memory_mb=256, pids_limit=64, timeout_s=3.0, disk_mb=64, network="none"
        )
        run = self._run(_INFINITE, limits=limits)
        self.assertIs(run.oracle_verdict, OracleVerdict.TIMEOUT)
        self.assertIs(classify_failure(run), FailureCategory.TIMEOUT)

    def test_f_a_memory_hog_hits_the_limit_and_is_never_a_pass(self) -> None:
        run = self._run(_MEMORY_HOG)
        self.assertIsNot(run.oracle_verdict, OracleVerdict.PASS)
        self.assertIn(
            classify_failure(run),
            {
                FailureCategory.RESOURCE_FAILURE,
                FailureCategory.INVALID_CANDIDATE,
                FailureCategory.EXECUTION_FAILURE,
            },
        )

    def test_g_a_container_that_cannot_start_is_infrastructure_then_the_next_run_recovers(self) -> None:
        broken = ContainerSandbox(
            image="apex-image-that-does-not-exist:never", capability_report=self.sandbox.detect()
        )
        failing = EvalRunnerService(self.factory, agent_runtime=self.runtime, sandbox=broken)
        clone = self.runtime.spawn(
            Subject.RESEARCH, pool=self.pool, genome_ref="sha256:c", spawned_by=Subject.CORE
        )
        run = failing.run_generated_solution(
            task_id=self.task.id,
            clone_id=clone.clone_id,
            endpoint=self.endpoint,
            method=EvalMethod.EXPERIMENTATION,
            provider=_Scripted({}, default=_CORRECT),
            limits=self.limits,
        )
        self.assertIs(run.oracle_verdict, OracleVerdict.ERROR)
        self.assertIs(classify_failure(run), FailureCategory.INFRASTRUCTURE_FAILURE)
        self.assertIs(self._run(_CORRECT).oracle_verdict, OracleVerdict.PASS)

    def test_h_the_adaptive_loop_learns_and_confirms_inside_containers(self) -> None:
        provider = _Scripted({0.7: _WRONG, 1.1: _CORRECT})
        result = run_adaptive_learning_loop(
            self.evalrunner,
            self.runtime,
            self.experiences,
            task_id=self.task.id,
            endpoint=self.endpoint,
            pool=self.pool,
            initial_strategies=[
                CandidateStrategy(label="g0", genome_content={"generation": {"temperature": 0.7}})
            ],
            config=AdaptiveLoopConfig(max_generations=2, no_improvement_patience=None, confirmation_runs=1),
            provider=provider,
            limits=self.limits,
        )
        self.assertEqual(provider.temperatures, [0.7, 1.1])
        g1 = self.experiences.get(result.generations[1].best_experience.id)
        self.assertIs(g1.oracle_verdict, OracleVerdict.PASS)
        self.assertIs(g1.success_evidence, SuccessEvidence.CONFIRMED)


if __name__ == "__main__":
    unittest.main()

"""Le Runtime LIVRÉ exécute réellement la boucle principale d'APEX (décision produit du 06/10/2026).

Construit l'artefact Runtime depuis l'arbre de travail (`runtime_build`), puis, dans un processus Python dont le
répertoire courant est l'artefact (aucun accès au dépôt) : vérifie que les paquets de recherche sont ABSENTS,
que l'API se construit sans eux (routes produit présentes, routes de recherche absentes), et exécute une
TaskRun de bout en bout — tâche définie par l'utilisateur, génération (modèle doublure défini dans le script de
test, jamais livré), sandbox réelle, Judge réel, Experience, adaptation entre tentatives.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from runtime_build.build import build_runtime_from_worktree
from runtime_build.selection import RESEARCH_PACKAGES
from runtime_build.verify import verify_runtime_artifact

_PROBE = textwrap.dedent(
    """
    import functools, importlib, json, sys
    from pathlib import Path

    import apex
    artifact = Path.cwd().resolve()
    assert Path(apex.__file__).resolve().is_relative_to(artifact), apex.__file__
    absent = []
    for name in RESEARCH:
        try:
            importlib.import_module("apex." + name)
        except ModuleNotFoundError:
            absent.append(name)
    from apex.util.extensions import load_research_extension
    assert load_research_extension() is None

    from apex.api.app import create_app
    paths = set(create_app().openapi()["paths"])

    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from apex.agents.runtime import AgentRuntime
    from apex.config.schema import Capability, ComputePool, ModelEndpoint, ModelRef, PoolLocation, TrustLevel
    from apex.db.engine import build_session_factory
    from apex.db.models import Base
    from apex.experience.store import ExperienceRepository
    from apex.models.model import GenerationResult, ModelCapabilityReport
    from apex.models.provider import ModelProvider
    from apex.models.registry import ModelCandidate
    from apex.obs.capability import CapabilityStatus
    from apex.obs.status import Measurement
    from apex.policy.model import Subject
    from apex.taskloop.loop import run_task_loop
    from apex.taskloop.registry import TaskLoopRegistry
    from apex.taskloop.request import TaskLimits
    from apex.taskloop.runs import TaskRunService
    from apex.tasks.model import TaskSplit
    from apex.tasks.service import TaskBankService
    from apex.tasks.solution_tasks import build_user_function_task_spec

    CODES = ["def add(a, b):\\n    return a - b\\n", "def add(a, b):\\n    return a + b\\n"]
    prompts = []

    class Double(ModelProvider):  # doublure de TEST, définie ici, jamais livrée
        def detect(self):
            return ModelCapabilityReport(status=CapabilityStatus.AVAILABLE, backend="test", detail="doublure")

        def generate(self, request):
            prompts.append(request.prompt)
            text = CODES[min(len(prompts) - 1, len(CODES) - 1)]
            return GenerationResult(text=text, model_used=request.endpoint.served_model,
                                    tokens_prompt=Measurement.unknown(), tokens_completion=Measurement.unknown(),
                                    finish_reason="stop", elapsed_s=0.001, facts={})

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    spec = build_user_function_task_spec(function="add", description="Additionne a et b.",
                                         cases=[{"args": [1, 2], "expected": 3}, {"args": [2, 2], "expected": 4}])
    task = TaskBankService(factory).create(split=TaskSplit.TRAIN, specialty="research", title=spec.title,
                                           description=spec.description, command=spec.command,
                                           created_by=Subject.HUMAN, solution_harness=spec.solution_harness,
                                           expected_answer=spec.expected_answer)
    candidate = ModelCandidate(role="coder_main", resident=False,
                               ref=ModelRef(family="m", version="1", quantization="q4", capability_class="coder"),
                               endpoint=ModelEndpoint(pool_id="local", runtime="ollama", url="http://127.0.0.1:1",
                                                      served_model="m:1", max_context=4096))
    pool = ComputePool(pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
                       capabilities=(Capability.CPU_EVAL,))
    loop = functools.partial(run_task_loop, candidates=(candidate,), registry=TaskLoopRegistry(),
                             provider_factory=lambda _c: Double())
    run = TaskRunService(factory, AgentRuntime(factory), pool, run_loop=loop).start(
        task_id=task.id, objective="o", limits=TaskLimits(max_attempts=2, max_retries=1, max_candidates=1),
        wait=True)
    experiences = [e for e in ExperienceRepository(factory).list_by_task(task.id) if e.experiment_ref == run.id]
    print(json.dumps({
        "absent": absent, "paths": sorted(paths), "status": run.status.value,
        "judge": [a.get("judge_result") for a in run.attempts], "experiences": len(experiences),
        "second_prompt_has_facts": len(prompts) == 2 and "Observations factuelles" in prompts[1],
    }))
    """
)


class TestRuntimeArtifactRunsTheCoreLoop(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.artifact = Path(cls._tmp.name) / "apex-runtime"
        build_runtime_from_worktree(cls.artifact)
        probe = f"RESEARCH = {sorted(RESEARCH_PACKAGES)!r}\n" + _PROBE
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=cls.artifact, capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise AssertionError(f"sonde Runtime en échec :\n{result.stdout}\n{result.stderr}")
        cls.report = json.loads(result.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_the_artifact_passes_the_unmodified_anti_leak_check(self) -> None:
        report = verify_runtime_artifact(self.artifact)
        self.assertTrue(report.ok, report.render())

    def test_no_research_package_is_importable_from_the_artifact(self) -> None:
        self.assertEqual(sorted(self.report["absent"]), sorted(RESEARCH_PACKAGES))

    def test_the_research_routes_checked_below_really_exist_in_development(self) -> None:
        from apex.api.app import create_app

        paths = set(create_app().openapi()["paths"])
        for research in ("/api/v1/learning/status", "/api/v1/self-model/{specialty}", "/api/v1/brain"):
            self.assertIn(research, paths)

    def test_the_api_has_the_product_routes_and_not_the_research_ones(self) -> None:
        paths = set(self.report["paths"])
        for product in ("/api/v1/tasks/function", "/api/v1/tasks/{task_id}/runs", "/api/v1/task-runs/{run_id}",
                        "/api/v1/conversations", "/api/v1/missions", "/api/v1/tasks", "/api/v1/eval-runs"):
            self.assertIn(product, paths)
        for research in ("/api/v1/learning/status", "/api/v1/self-model/{specialty}", "/api/v1/brain"):
            self.assertNotIn(research, paths)

    def test_the_doctor_does_not_report_absent_research_as_a_lying_registry(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "apex.cli.main", "doctor", "--json"], cwd=self.artifact,
            capture_output=True, text=True, timeout=120,
        )
        checks = {c["name"]: c for c in json.loads(result.stdout)["results"]}
        integrity = checks["interfaces.integrity"]
        self.assertEqual(integrity["severity"], "pass", integrity)
        self.assertNotIn("TaskLoop", integrity["facts"]["not_in_distribution"])
        self.assertIn("MetaLearner", integrity["facts"]["not_in_distribution"])

    def test_a_task_run_completes_end_to_end_inside_the_runtime(self) -> None:
        self.assertEqual(self.report["status"], "completed")
        self.assertEqual(self.report["judge"], [None, "pass"])  # échec jugé, puis réussite
        self.assertEqual(self.report["experiences"], 2)
        self.assertTrue(self.report["second_prompt_has_facts"])  # adaptation entre tentatives, dans le Runtime


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""Real DB/TaskLoop/Docker/oracles. Generation fixtures are NOT neural evidence."""

import functools
import json
from pathlib import Path

import pytest

from apex.agents.runtime import AgentRuntime
from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
from apex.config.schema import ModelEndpoint
from apex.missions.service import MissionService
from apex.missions.states import MissionState
from apex.models.provider import get_default_provider
from apex.policy.model import Subject
from apex.project_orchestration import ProjectService, ProjectStep
from apex.project_orchestration.contract import validate_steps
from apex.project_orchestration.sandbox import AbsoluteDockerSandbox
from apex.scheduler.service import SchedulerService
from apex.taskloop.loop import run_task_loop
from apex.taskloop.registry import TaskLoopRegistry
from apex.taskloop.runs import TaskRunService
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService
from apex.tasks.solution_tasks import build_user_function_task_spec
from tests.test_taskloop_execution import _FakeModelProvider, a_candidate, a_pool, sqlite_session_factory

ASSET_SOURCE = 'def asset_dimensions():\n    return {"length_m": 4.5, "width_m": 1.9, "wheel_count": 4}\n'
ENGINE_SOURCE = "def to_cm(value):\n    return value * 100\n"


class FixtureController(_FakeModelProvider):
    def __init__(self, *, fail_first=False, always_wrong=False):
        super().__init__()
        self.fail_first, self.always_wrong = fail_first, always_wrong

    def generate(self, request):
        if self.always_wrong or (self.fail_first and self.calls == 0):
            self.text = "def asset_dimensions():\n    return {}\n"
        else:
            self.text = ENGINE_SOURCE if "to_cm" in request.prompt else ASSET_SOURCE
        return super().generate(request)


@pytest.fixture
def project(tmp_path):
    factory = sqlite_session_factory()
    runtime = AgentRuntime(factory)
    provider = FixtureController(fail_first=True)
    sandbox = AbsoluteDockerSandbox()
    if not sandbox.detect().is_available:
        pytest.skip("BLOCKED: actual container isolation unavailable")
    registry = TaskLoopRegistry()
    runs = TaskRunService(
        factory,
        runtime,
        a_pool(),
        registry=registry,
        run_loop=functools.partial(
            run_task_loop,
            sandbox=sandbox,
            registry=registry,
            candidates=(a_candidate(),),
            provider_factory=lambda c: provider,
        ),
    )
    missions = MissionService(factory)
    scheduler = SchedulerService(
        factory,
        mission_service=missions,
        agent_runtime=runtime,
        task_run_service=runs,
        max_concurrent_missions=1,
    )
    artifacts = LocalFilesystemArtifactStore(root=str(tmp_path / "artifacts"))
    service = ProjectService(
        missions=missions,
        runs=runs,
        scheduler=scheduler,
        artifacts=artifacts,
        receipt_root=tmp_path / "receipts",
        providers={"game_graphics": provider, "game_engineer": provider},
    )
    bank = TaskBankService(factory)
    ids = []
    for name, description, cases in (
        (
            "asset_dimensions",
            "Retourne les dimensions d'un véhicule : longueur 4.5 m, largeur 1.9 m et quatre roues.",
            [{"args": [], "expected": {"length_m": 4.5, "width_m": 1.9, "wheel_count": 4}}],
        ),
        (
            "to_cm",
            "Convertis une valeur en mètres en centimètres.",
            [
                {"args": [4.5], "expected": 450},
                {"args": [1.9], "expected": 190},
                {"args": [0], "expected": 0},
            ],
        ),
    ):
        spec = build_user_function_task_spec(function=name, description=description, cases=cases)
        task = bank.create(
            split=TaskSplit.TRAIN,
            specialty="research",
            title=spec.title,
            description=spec.description,
            command=spec.command,
            created_by=Subject.HUMAN,
            solution_harness=spec.solution_harness,
            expected_answer=spec.expected_answer,
        )
        ids.append(task.id)
    steps = (
        ProjectStep("graphics", ids[0], "game_graphics"),
        ProjectStep("engineer", ids[1], "game_engineer", ("graphics",)),
    )
    yield service, steps, provider, artifacts
    scheduler.shutdown()


def test_two_real_tasks_correction_artifacts_resume_and_human_review(project, tmp_path):
    service, steps, provider, artifacts = project
    project_id = service.create(
        objective="Contrat véhicule puis conversion UE", steps=steps, requested_by=Subject.HUMAN
    )
    result = service.run(project_id)
    (tmp_path / "actual-result.json").write_text(json.dumps(result, indent=2))
    assert result["status"] == "completed", result
    assert result["mission_state"] == "review" and result["review_required"]
    assert provider.calls == 3  # Incorrect candidate rejected and corrected by canonical TaskLoop.
    assert len(result["steps"]["graphics"]["attempts"]) == 2
    for entry in result["steps"].values():
        assert artifacts.get(entry["artifact"])
        assert service.runs.repo.get(entry["run_id"]).eval_run_ids
    resumed = service.run(project_id)
    assert provider.calls == 3 and resumed["steps"] == result["steps"]
    assert service.missions.get(project_id).state is MissionState.REVIEW


def test_missing_model_blocks_without_running_a_task(project):
    service, steps, provider, _ = project
    service.providers["game_graphics"] = get_default_provider(
        ModelEndpoint(
            pool_id="local",
            runtime="openai_compatible",
            url="http://127.0.0.1:1/v1",
            served_model="absent",
            max_context=8192,
        )
    )
    project_id = service.create(objective="Model missing", steps=steps, requested_by=Subject.HUMAN)
    result = service.run(project_id)
    assert result["status"] == "blocked" and result["reason"] == "model_unavailable:game_graphics"
    assert provider.calls == 0 and result["mission_state"] == "queued"


def test_mission_dependency_uses_existing_scheduler_gate(project):
    service, steps, provider, _ = project
    prerequisite = service.missions.create(
        title="Prerequisite",
        description="pending",
        objective="review",
        specialty="research",
        created_by=Subject.HUMAN,
    )
    project_id = service.create(
        objective="Dependent", steps=steps, requested_by=Subject.HUMAN, depends_on=(prerequisite.id,)
    )
    assert service.run(project_id)["reason"] == "mission_dependency_not_completed"
    assert provider.calls == 0


def test_corrupted_artifact_prevents_resume_or_downstream_use(project):
    service, steps, provider, artifacts = project
    project_id = service.create(objective="Integrity", steps=steps, requested_by=Subject.HUMAN)
    result = service.run(project_id)
    assert result["status"] == "completed"
    digest = result["steps"]["graphics"]["artifact"].split(":")[1]
    path = Path(artifacts._content_path(digest))
    path.write_bytes(b"tampered")
    calls = provider.calls
    result = service.run(project_id)
    assert result["status"] == "blocked" and "artifact_unverified" in result["reason"]
    assert provider.calls == calls


def test_attempt_budget_exhaustion_does_not_run_dependent_step(project):
    service, steps, provider, _ = project
    provider.always_wrong = True
    project_id = service.create(
        objective="Bounded failure", steps=steps, requested_by=Subject.HUMAN, max_attempts=2
    )
    result = service.run(project_id)
    assert result["status"] in ("failed", "blocked") and provider.calls == 2
    assert "engineer" not in result["steps"]
    assert service.run(project_id)["status"] in ("failed", "blocked")
    assert provider.calls == 2


def test_invalid_dependency_order_is_rejected():
    with pytest.raises(ValueError):
        validate_steps(
            (ProjectStep("a", "1", "graphics", ("b",)), ProjectStep("b", "2", "engineer", ("a",)))
        )


def test_docker_adapter_preserves_security_flags():
    from apex.sandbox.model import ResourceLimits, SandboxSpec

    sandbox = AbsoluteDockerSandbox()
    limits = ResourceLimits(
        cpu_cores=1, memory_mb=128, pids_limit=16, timeout_s=10, disk_mb=32, network="none"
    )
    spec = SandboxSpec(
        mission_id="test", clone_id="test", limits=limits, command=("python3", "-c", "print(1)")
    )
    argv = sandbox._build_argv(spec, "test")
    assert Path(argv[0]).is_absolute()
    assert "--privileged" not in argv and "--mount" not in argv and "-v" not in argv
    assert "--read-only" in argv and argv[argv.index("--network") + 1] == "none"
    assert sandbox._child_env()["PATH"] == "/usr/bin:/bin"


def test_lost_receipt_recovers_completed_runs_without_a_model(project):
    service, steps, provider, _ = project
    project_id = service.create(objective="Lost receipt", steps=steps, requested_by=Subject.HUMAN)
    first = service.run(project_id)
    assert first["status"] == "completed"
    (service.receipt_root / f"{project_id}.json").unlink()
    service.providers = {}
    recovered = service.run(project_id)
    assert recovered["status"] == "completed" and provider.calls == 3
    assert [e["run_id"] for e in recovered["steps"].values()] == [
        e["run_id"] for e in first["steps"].values()
    ]


def test_missing_second_model_preserves_first_step_for_resume(project):
    service, steps, provider, _ = project
    service.providers.pop("game_engineer")
    project_id = service.create(objective="Partial resume", steps=steps, requested_by=Subject.HUMAN)
    first = service.run(project_id)
    assert first["status"] == "blocked" and provider.calls == 2
    service.providers["game_engineer"] = provider
    resumed = service.run(project_id)
    assert resumed["status"] == "completed" and provider.calls == 3
    assert first["steps"]["graphics"]["run_id"] == resumed["steps"]["graphics"]["run_id"]


def test_valid_hash_from_another_run_is_not_valid_provenance(project):
    service, steps, provider, _ = project
    project_id = service.create(objective="Wrong provenance", steps=steps, requested_by=Subject.HUMAN)
    receipt = service.run(project_id)
    receipt["steps"]["graphics"]["artifact"] = receipt["steps"]["engineer"]["artifact"]
    (service.receipt_root / f"{project_id}.json").write_text(json.dumps(receipt))
    assert service.run(project_id)["status"] == "blocked"
    assert provider.calls == 3

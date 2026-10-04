"""TaskRun : l'exécution persistée d'une tâche par la boucle existante `run_task_loop`.

Ce qui est réel ici : la base (SQLite, même schéma que PostgreSQL), `run_task_loop` tel quel, la sandbox
réelle de cette machine (backend choisi par `select_sandbox_provider`), le Judge Tier / Oracle,
`Experience`. **Seul le modèle est une doublure** (`_FakeModelProvider` des tests de la boucle) : aucun
Ollama n'est joignable ici. La génération par un VRAI modèle est prouvée à part, sur WSL2 réel (CI
windows-desktop, étape « first real loop »), jamais présumée par ces tests.
"""

from __future__ import annotations

import functools
import threading

import pytest
from fastapi.testclient import TestClient

from apex.agents.runtime import AgentRuntime
from apex.policy.model import Subject
from apex.taskgen.solution_tasks import build_palindrome_task_spec
from apex.taskloop.loop import run_task_loop
from apex.taskloop.registry import TaskLoopRegistry
from apex.taskloop.request import TaskLimits
from apex.taskloop.runs import TaskRun, TaskRunRepository, TaskRunService, TaskRunStatus, _utcnow
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService
from tests.api_test_support import TEST_API_TOKEN
from tests.test_taskloop_execution import (
    _CORRECT_IS_PALINDROME,
    _WRONG_IS_PALINDROME,
    _FakeModelProvider,
    a_candidate,
    a_pool,
    sqlite_session_factory,
)

BEARER = {"Authorization": f"Bearer {TEST_API_TOKEN}"}


def _task(factory):
    spec = build_palindrome_task_spec()
    return TaskBankService(factory).create(
        split=TaskSplit.TRAIN,
        specialty="research",
        title=spec.title,
        description=spec.description,
        command=spec.command,
        created_by=Subject.HUMAN,
        solution_harness=spec.solution_harness,
        expected_answer=spec.expected_answer,
    )


def _service(factory, *, code: str = _CORRECT_IS_PALINDROME, run_loop=None) -> TaskRunService:
    loop = run_loop or functools.partial(
        run_task_loop,
        candidates=(a_candidate(),),
        registry=TaskLoopRegistry(),
        provider_factory=lambda _c: _FakeModelProvider(mode="ok", text=code),
    )
    return TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop)


@pytest.fixture
def factory():
    return sqlite_session_factory()


# -- la chaîne complète, persistée ------------------------------------------------------------------


def test_a_run_goes_task_model_execution_judge_experience_and_is_persisted(factory) -> None:
    task = _task(factory)
    service = _service(factory)

    run = service.start(task_id=task.id, objective="résoudre is_palindrome", wait=True)

    assert run.status is TaskRunStatus.COMPLETED and run.stop_reason == "success"
    assert len(run.eval_run_ids) == 1 and run.providers_tried and run.final_output
    assert run.attempts and run.attempts[0]["judge_result"] == "pass"
    # Persisté : relu par un AUTRE repository (un autre processus verrait la même ligne).
    again = TaskRunRepository(factory).get(run.id)
    assert again is not None and again.to_dict() == run.to_dict()


def test_the_generated_code_and_its_fingerprint_are_kept_for_comparison(factory) -> None:
    """Défaut de l'audit : le TaskLoop ne persistait pas le code généré (empreinte toujours vide)."""
    import hashlib

    from apex.evalrunner.repository import EvalRunRepository
    from apex.metalearner.experience import ExperienceRepository

    task = _task(factory)
    run = _service(factory).start(task_id=task.id, objective="o", wait=True)

    eval_run = EvalRunRepository(factory).get(run.eval_run_ids[0])
    assert eval_run.experiment_ref == run.id  # même clé : TaskRun = trace_id
    assert eval_run.facts["model_response"]["text"] == _CORRECT_IS_PALINDROME
    (experience,) = [
        e for e in ExperienceRepository(factory).list_by_task(task.id) if e.eval_run_id == eval_run.id
    ]
    assert experience.solution_fingerprint == hashlib.sha256(_CORRECT_IS_PALINDROME.encode()).hexdigest()
    assert experience.oracle_verdict.value == "pass" and experience.fitness > 0


def test_generation_timings_reported_by_the_backend_are_kept_never_invented() -> None:
    """Chargement et vitesse de génération d'une exécution réelle : mesurables après coup (machine cible)."""
    from apex.evalrunner.service import EvalRunnerService
    from apex.models.model import GenerationResult
    from apex.obs.status import Measurement

    reported = GenerationResult(
        text="x", model_used="m", tokens_prompt=Measurement.real(35), tokens_completion=Measurement.real(32),
        finish_reason="stop", elapsed_s=11.1,
        facts={"load_duration": 9_400_000_000, "eval_duration": 1_300_000_000, "done": True},
    )
    silent = GenerationResult(
        text="x", model_used="m", tokens_prompt=Measurement.unknown(), tokens_completion=Measurement.unknown(),
        finish_reason=None, elapsed_s=0.5, facts={},
    )

    facts = EvalRunnerService._model_response_facts(reported)["model_response"]
    assert facts["elapsed_s"] == 11.1
    assert facts["backend_timings_s"] == {"load": 9.4, "eval": 1.3}
    assert EvalRunnerService._model_response_facts(silent)["model_response"]["backend_timings_s"] == {}


def test_a_wrong_solution_is_judged_failed_and_still_leaves_an_experience(factory) -> None:
    from apex.metalearner.experience import ExperienceRepository

    task = _task(factory)
    service = _service(factory, code=_WRONG_IS_PALINDROME)

    run = service.start(
        task_id=task.id, objective="o", limits=TaskLimits(max_attempts=2, max_retries=1), wait=True
    )

    assert run.status in (TaskRunStatus.FAILED, TaskRunStatus.BLOCKED)
    assert run.final_output is None and run.failure
    experiences = ExperienceRepository(factory).list_by_task(task.id)
    assert experiences and all(e.oracle_verdict.value != "pass" for e in experiences)


# -- redémarrage : jamais « en cours » pour toujours, jamais repris en douce ---------------------------


def test_a_run_left_running_by_a_dead_process_becomes_interrupted(factory) -> None:
    task = _task(factory)
    repo = TaskRunRepository(factory)
    orphan = TaskRun(
        id="orphan-1",
        task_id=task.id,
        status=TaskRunStatus.RUNNING,
        objective="o",
        execution_mode="bounded",
        requested_by="human",
        limits={},
        started_at=_utcnow(),
    )
    repo.create(orphan)

    assert _service(factory).reconcile_on_startup() == 1  # nouveau processus

    reread = repo.get("orphan-1")
    assert reread.status is TaskRunStatus.INTERRUPTED and reread.stop_reason == "process_restarted"
    assert reread.completed_at is not None


def test_a_late_result_never_overwrites_a_terminal_status(factory) -> None:
    from apex.taskloop.result import TaskLoopStatus, TaskResult

    task = _task(factory)
    repo = TaskRunRepository(factory)
    repo.create(
        TaskRun(
            id="r1",
            task_id=task.id,
            status=TaskRunStatus.RUNNING,
            objective="o",
            execution_mode="bounded",
            requested_by="human",
            limits={},
            started_at=_utcnow(),
        )
    )
    repo.mark_interrupted_running()
    late = TaskResult(
        task_id=task.id,
        trace_id="r1",
        status=TaskLoopStatus.COMPLETED,
        final_output="x",
        attempts=(),
        providers_tried=(),
        eval_run_ids=(),
        failure=None,
        stop_reason="success",
    )

    assert repo.finish_with_result("r1", late) is False
    assert repo.get("r1").status is TaskRunStatus.INTERRUPTED


def test_a_loop_error_is_recorded_never_swallowed(factory) -> None:
    task = _task(factory)

    def broken(*_a, **_k):
        raise RuntimeError("panne inattendue")

    run = _service(factory, run_loop=broken).start(task_id=task.id, objective="o", wait=True)

    assert run.status is TaskRunStatus.ERROR and "panne inattendue" in run.failure


def test_a_task_without_harness_or_already_running_is_refused(factory) -> None:
    from apex.taskloop.registry import TaskLoopHandle, default_registry

    plain = TaskBankService(factory).create(
        split=TaskSplit.TRAIN,
        specialty="research",
        title="t",
        description="d",
        command=("python3", "-c", "pass"),
        created_by=Subject.HUMAN,
    )
    with pytest.raises(ValueError):
        _service(factory).start(task_id=plain.id, objective="o")
    with pytest.raises(LookupError):
        _service(factory).start(task_id="absente", objective="o")
    task = _task(factory)
    default_registry().track(TaskLoopHandle(task_id=task.id, trace_id="x"))
    try:
        with pytest.raises(RuntimeError):
            _service(factory).start(task_id=task.id, objective="o")
    finally:
        default_registry().discard(task.id)


def test_the_loop_only_routes_to_models_that_generate_code() -> None:
    """Défaut de l'audit : la réassignation pouvait tomber sur `bge-m3` (embedding) ou le juge."""
    from apex.config.loader import load_config
    from apex.taskloop.loop import CODE_GENERATION_CLASSES, code_generation_candidates

    candidates = code_generation_candidates(load_config())

    assert candidates and {c.ref.capability_class for c in candidates} <= CODE_GENERATION_CLASSES
    assert not {"embed", "judge"} & {c.role for c in candidates}
    assert any(c.ref.family == "qwen2.5-coder" for c in candidates)  # modèle présent sur la machine cible


# -- API -------------------------------------------------------------------------------------------


@pytest.fixture
def api(factory, monkeypatch):
    from apex.api import dependencies
    from apex.api.app import create_app

    service = _service(factory)
    monkeypatch.setattr(dependencies, "_task_run_service", service)
    monkeypatch.setattr(dependencies, "get_session_factory", lambda: factory)
    return TestClient(create_app(), headers=BEARER), service


def test_api_creates_the_reference_task_runs_it_and_returns_the_whole_chain(api) -> None:
    client, service = api
    task = client.post("/api/v1/tasks/reference", json={"kind": "is_palindrome"}).json()["data"]
    assert task["title"].endswith("is_palindrome")

    started = client.post(f"/api/v1/tasks/{task['id']}/runs", json={"objective": "résoudre"})
    assert started.status_code == 202 and started.json()["data"]["status"] == "running"
    run_id = started.json()["data"]["id"]
    service.wait(run_id, timeout_s=120)

    detail = client.get(f"/api/v1/task-runs/{run_id}").json()["data"]
    assert detail["status"] == "completed"
    (attempt,) = detail["eval_runs"]
    assert attempt["generated_code"] == _CORRECT_IS_PALINDROME
    assert attempt["oracle_verdict"] == "pass" and attempt["sandbox_status"] == "success"
    assert attempt["isolation"] == detail["attempts"][0]["sandbox_level"]  # niveau réellement appliqué
    (experience,) = detail["experiences"]
    assert experience["eval_run_id"] == attempt["id"] and experience["solution_fingerprint"]
    assert [r["id"] for r in client.get(f"/api/v1/tasks/{task['id']}/runs").json()["data"]] == [run_id]


def test_api_errors_are_explicit(api) -> None:
    client, _ = api
    assert client.post("/api/v1/tasks/absente/runs", json={}).status_code == 404
    assert client.post("/api/v1/tasks/reference", json={"kind": "inconnue"}).status_code == 422
    assert client.get("/api/v1/task-runs/absente").status_code == 404
    assert TestClient(client.app).post("/api/v1/tasks/x/runs", json={}).status_code == 401


# -- /status : la vérité sur la base et la sandbox --------------------------------------------------


def test_status_database_requires_the_schema_not_only_a_connection(monkeypatch) -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool

    from apex.api import dependencies
    from apex.db.engine import build_session_factory
    from apex.db.schema import head_revision
    from apex.obs import component_checks as cc

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    monkeypatch.setattr(dependencies, "get_session_factory", lambda: build_session_factory(engine))

    missing = cc.check_database()
    assert missing.state == cc.DEGRADED and "schéma absent" in missing.detail

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": head_revision()})
    assert cc.check_database().state == cc.READY


def test_status_sandbox_reports_the_backend_the_runtime_really_selects(monkeypatch) -> None:
    import types

    from apex.obs import component_checks as cc
    from apex.sandbox import provider
    from apex.sandbox.model import IsolationLevel, SandboxCapabilityReport

    report = SandboxCapabilityReport(
        level=IsolationLevel.CONTAINERIZED,
        namespaces_available=True,
        cgroups_available=True,
        privilege_drop_available=True,
        network_isolation_available=True,
        docker_daemon_reachable=True,
        seccomp_available=False,
        detail="vérifié sur un conteneur réel",
        facts={},
    )
    monkeypatch.setattr(
        provider,
        "select_sandbox_provider",
        lambda: types.SimpleNamespace(report=report, provider_name="ContainerSandbox"),
    )
    monkeypatch.setattr(cc, "_sandbox_cache", None)

    status = cc.check_sandbox()

    assert status.state == cc.READY and status.detail.startswith(
        "ContainerSandbox : isolation containerized"
    )
    monkeypatch.setattr(cc, "_sandbox_cache", None)


def test_the_migration_matches_the_model() -> None:
    """La table créée par la migration porte exactement les colonnes du modèle SQLAlchemy."""
    import re
    from pathlib import Path

    from apex.db.models import TaskRunRow

    source = (
        Path(__file__).resolve().parents[1] / "migrations" / "versions" / "c8e2f4a6b9d1_task_runs.py"
    ).read_text()
    migrated = set(re.findall(r'sa\.Column\("(\w+)"', source))
    assert migrated == {c.name for c in TaskRunRow.__table__.columns}
    _ = threading  # le service exécute dans un fil (voir TaskRunService.start)

"""Real model-only entry point. No fixture or simulated generation mode."""

import argparse
import functools
import json
from pathlib import Path

from sqlalchemy import create_engine

from apex.agents.runtime import AgentRuntime
from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
from apex.config.schema import Capability, ComputePool, ModelEndpoint, ModelRef, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.missions.service import MissionService
from apex.models.provider import get_default_provider
from apex.models.registry import ModelCandidate
from apex.policy.model import Subject
from apex.project_orchestration import ProjectService, ProjectStep
from apex.project_orchestration.sandbox import AbsoluteDockerSandbox
from apex.scheduler.service import SchedulerService
from apex.taskloop.loop import run_task_loop
from apex.taskloop.registry import TaskLoopRegistry
from apex.taskloop.runs import TaskRunService
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService
from apex.tasks.solution_tasks import build_user_function_task_spec


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".apex/game-project"))
    parser.add_argument("--endpoint", default="http://127.0.0.1:1/v1")
    parser.add_argument("--model-name", default="qwen2.5-coder-7b-instruct-q5")
    parser.add_argument("--project-id")
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{root / 'project.sqlite'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    runtime = AgentRuntime(factory)
    pool = ComputePool(
        pool_id="local",
        location=PoolLocation.LOCAL,
        trust_level=TrustLevel.TRUSTED,
        capabilities=(Capability.CPU_EVAL,),
    )
    endpoint = ModelEndpoint(
        pool_id="local",
        runtime="openai_compatible",
        url=args.endpoint,
        served_model=args.model_name,
        max_context=8192,
    )
    provider = get_default_provider(endpoint)
    candidate = ModelCandidate(
        role="coder_main",
        resident=False,
        endpoint=endpoint,
        ref=ModelRef(
            family="qwen2.5-coder", version="7b-instruct", quantization="q5_k_m", capability_class="coder"
        ),
    )
    registry = TaskLoopRegistry()
    runs = TaskRunService(
        factory,
        runtime,
        pool,
        registry=registry,
        run_loop=functools.partial(
            run_task_loop,
            candidates=(candidate,),
            sandbox=AbsoluteDockerSandbox(),
            registry=registry,
            workspace_root=str(root / "workspaces"),
        ),
    )
    # This CLI owns a private DB; do not call global reconciliation against another runtime's DB.
    runs.reconcile_on_startup()
    missions = MissionService(factory)
    scheduler = SchedulerService(
        factory,
        mission_service=missions,
        agent_runtime=runtime,
        task_run_service=runs,
        max_concurrent_missions=1,
    )
    service = ProjectService(
        missions=missions,
        runs=runs,
        scheduler=scheduler,
        artifacts=LocalFilesystemArtifactStore(root=str(root / "artifacts")),
        receipt_root=root / "receipts",
        providers={"game_graphics": provider, "game_engineer": provider},
    )
    try:
        project_id = args.project_id
        if project_id is None:
            bank = TaskBankService(factory)
            ids = []
            tasks = (
                (
                    "asset_dimensions",
                    "Retourne un dict length_m=4.5, width_m=1.9, wheel_count=4.",
                    [{"args": [], "expected": {"length_m": 4.5, "width_m": 1.9, "wheel_count": 4}}],
                ),
                (
                    "to_cm",
                    "Convertis des mètres en centimètres.",
                    [{"args": [4.5], "expected": 450}, {"args": [1.9], "expected": 190}],
                ),
            )
            for name, description, cases in tasks:
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
            project_id = service.create(
                objective="Contrat véhicule puis conversion d'unités",
                steps=steps,
                requested_by=Subject.HUMAN,
            )
        result = service.run(project_id)
        (root / "last-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "completed" else 2
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())

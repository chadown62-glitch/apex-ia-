"""Compose Mission/TaskRun/ArtifactStore. The canonical TaskLoop owns retries/judging."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from apex.artifacts.provider import ArtifactStoreProvider
from apex.missions.service import MissionService
from apex.missions.states import MissionState
from apex.models.provider import ModelProvider
from apex.policy.model import Subject
from apex.project_orchestration.contract import ProjectStep, validate_steps
from apex.scheduler.service import SchedulerService
from apex.taskloop.request import TaskLimits
from apex.taskloop.runs import TaskRunService, TaskRunStatus
from apex.tasks.repository import TaskRepository


@contextmanager
def _project_lock(path: Path) -> Iterator[None]:
    # Serialize receipt updates and starts across local processes; never unlink held lock inodes.
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


class ProjectService:
    def __init__(
        self,
        *,
        missions: MissionService,
        runs: TaskRunService,
        scheduler: SchedulerService,
        artifacts: ArtifactStoreProvider,
        receipt_root: Path,
        providers: dict[str, ModelProvider],
    ) -> None:
        self.missions, self.runs, self.scheduler = missions, runs, scheduler
        self.artifacts, self.receipt_root, self.providers = artifacts, receipt_root, providers
        receipt_root.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        objective: str,
        steps: tuple[ProjectStep, ...],
        requested_by: Subject,
        depends_on: tuple[str, ...] = (),
        max_attempts: int = 3,
    ) -> str:
        validate_steps(steps)
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be 1-10")
        tasks = TaskRepository(self.runs.session_factory)
        for step in steps:
            task = tasks.get(step.task_id)
            if task is None or task.solution_harness is None:
                raise ValueError("every step requires an existing verifiable Task")
        recipe = {"schema_version": 1, "steps": [s.to_dict() for s in steps], "max_attempts": max_attempts}
        # Legacy specialty is an envelope, not a claim that new domain roles are globally registered.
        mission = self.missions.create(
            title=objective,
            description=json.dumps(recipe),
            objective=objective,
            specialty="research",
            created_by=requested_by,
            depends_on=depends_on,
        )
        return mission.id

    def _save(self, path: Path, receipt: dict[str, Any]) -> None:
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".receipt-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(receipt, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def run(self, project_id: str) -> dict[str, Any]:
        UUID(project_id)  # No caller-controlled filesystem paths.
        path = self.receipt_root / f"{project_id}.json"
        try:
            with _project_lock(path.with_suffix(".lock")):
                return self._run(project_id, path)
        except (BlockingIOError, PermissionError):
            return {"status": TaskRunStatus.BLOCKED.value, "reason": "project_already_running"}

    def _run(self, project_id: str, path: Path) -> dict[str, Any]:
        mission = self.missions.get(project_id)
        if mission is None:
            raise LookupError("project mission absent")
        recipe = json.loads(mission.description)
        steps = tuple(ProjectStep(**{**s, "depends_on": tuple(s["depends_on"])}) for s in recipe["steps"])
        validate_steps(steps)
        receipt: dict[str, Any] = (
            json.loads(path.read_text()) if path.exists() else {"project_id": project_id, "steps": {}}
        )

        def result(status: TaskRunStatus, reason: str | None = None) -> dict[str, Any]:
            current = self.missions.get(project_id)
            if current is None:
                raise LookupError("project mission removed")
            return {
                **receipt,
                "status": status.value,
                "reason": reason,
                "mission_state": current.state.value,
                "review_required": True,
            }

        if self.scheduler._unmet_dependencies(mission):
            return result(TaskRunStatus.BLOCKED, "mission_dependency_not_completed")
        if mission.state in (MissionState.FAILED, MissionState.CANCELLED):
            return result(TaskRunStatus.FAILED, "mission_terminal")
        for step in steps:
            entry = receipt["steps"].get(step.key)
            prior = (
                self.runs.repo.get(entry["run_id"])
                if entry
                else next(
                    (
                        run
                        for run in self.runs.repo.list_by_task(step.task_id)
                        if run.mission_id == project_id
                    ),
                    None,
                )
            )
            if prior and (prior.task_id != step.task_id or prior.mission_id != project_id):
                return result(TaskRunStatus.BLOCKED, "run_provenance_mismatch")
            if entry and entry.get("artifact"):
                try:
                    content = self.artifacts.get(entry["artifact"])  # Recompute SHA on every resume.
                    if prior is None or prior.status is not TaskRunStatus.COMPLETED:
                        raise ValueError("receipt run is not completed")
                    if content != (prior.final_output or "").encode():
                        raise ValueError("artifact is not the recorded TaskRun output")
                except Exception as exc:
                    return result(TaskRunStatus.BLOCKED, f"artifact_unverified:{type(exc).__name__}")
                continue
            if prior and prior.status is TaskRunStatus.COMPLETED:
                if not prior.final_output:
                    return result(TaskRunStatus.BLOCKED, "completed_run_without_artifact")
                artifact = self.artifacts.put(prior.final_output.encode(), content_type="text/x-python")
                receipt["steps"][step.key] = {
                    "run_id": prior.id,
                    "status": prior.status.value,
                    "attempts": list(prior.attempts),
                    "artifact": artifact.content_hash,
                    "feedback": prior.stop_reason,
                }
                self._save(path, receipt)
                continue
            if any(
                dep not in receipt["steps"] or not receipt["steps"][dep].get("artifact")
                for dep in step.depends_on
            ):
                return result(TaskRunStatus.BLOCKED, "step_dependency_missing")
            provider = self.providers.get(step.role)
            if provider is None or not provider.detect().is_available:
                return result(TaskRunStatus.BLOCKED, f"model_unavailable:{step.role}")
            if mission.state is MissionState.QUEUED:
                mission = self.missions.mark_running(project_id)
            if prior and prior.status is TaskRunStatus.RUNNING:
                return result(TaskRunStatus.BLOCKED, "task_run_still_running")
            if prior and prior.status is TaskRunStatus.INTERRUPTED:
                run = self.runs.resume(prior.id, wait=True)
            elif (
                prior
                and prior.status
                in (
                    TaskRunStatus.FAILED,
                    TaskRunStatus.ERROR,
                    TaskRunStatus.CANCELLED,
                )
            ) or (prior and prior.status is TaskRunStatus.BLOCKED and prior.attempts):
                return result(prior.status, prior.failure)
            else:
                inputs = {
                    dep: self.artifacts.get(receipt["steps"][dep]["artifact"]).decode("utf-8")
                    for dep in step.depends_on
                }
                budget = recipe["max_attempts"]
                run = self.runs.start(
                    task_id=step.task_id,
                    objective=f"{mission.objective} / {step.role}",
                    mission_id=project_id,
                    requested_by=Subject.CORE,
                    wait=True,
                    limits=TaskLimits(
                        max_attempts=budget, max_retries=budget - 1, max_candidates=1, max_wall_clock_s=900
                    ),
                    context=json.dumps({"role": step.role, "verified_upstream_sources": inputs}),
                )
            entry = {
                "run_id": run.id,
                "status": run.status.value,
                "attempts": list(run.attempts),
                "feedback": run.failure or run.stop_reason,
            }
            receipt["steps"][step.key] = entry
            self._save(path, receipt)
            if run.status is not TaskRunStatus.COMPLETED:
                if run.status in (TaskRunStatus.FAILED, TaskRunStatus.ERROR):
                    self.missions.mark_failed(
                        project_id, error=f"TaskRun {run.id}: {run.failure or run.stop_reason}"
                    )
                return result(run.status, run.failure or run.stop_reason)
            if not run.final_output:
                return result(TaskRunStatus.BLOCKED, "completed_run_without_artifact")
            artifact = self.artifacts.put(run.final_output.encode(), content_type="text/x-python")
            self.artifacts.get(artifact.content_hash)
            entry["artifact"] = artifact.content_hash
            self._save(path, receipt)
        current = self.missions.get(project_id)
        if current is None:
            raise LookupError("project mission removed")
        if current.state is MissionState.RUNNING:
            self.missions.mark_review(project_id, result=json.dumps(receipt))
        return result(TaskRunStatus.COMPLETED)

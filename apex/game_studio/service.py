"""APEX Mission adapter around the existing fixed Blender specialist.

MissionService owns governance and events; ArtifactStore owns bytes.  This
module only sequences domain-specific, versioned artistic operations.  It
does not execute code supplied by the model or claim TaskLoop code-harness
execution for a native Blender job.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from apex.artifacts.provider import ArtifactStoreProvider, get_default_provider
from apex.config.schema import RecoveryPolicy
from apex.game_graphics.contract import QualityCriteria
from apex.game_graphics.models import CoderSession
from apex.game_graphics.resources import resource_lease
from apex.game_graphics.studio.benchmark import compact_hierarchy, compare_studio_defects, judge_revision
from apex.game_graphics.studio.controller import propose_plan
from apex.game_graphics.studio.execution import ROOT, create_revision, refuse_personal_project
from apex.missions.service import MissionService
from apex.policy.model import Subject
from apex.recovery.model import FailureCategory, RecoveryAction
from apex.recovery.policy import decide
from apex.sandbox.model import ExecutionStatus

from .capabilities import coder_manifest, native_coder_endpoint, probe

BENCHMARK = ROOT / "docs/game_graphics/studio/benchmark-v1.json"
FAMILY = {"vehicule": "vehicle", "batiment": "building", "objet_3d": "chair"}
CASE = {"vehicle": "sports_car", "building": "street_building", "chair": "lounge_chair"}
MIME = {
    ".blend": "application/octet-stream",
    ".glb": "model/gltf-binary",
    ".png": "image/png",
    ".json": "application/json",
    ".log": "text/plain",
}


def _utc() -> str:
    return datetime.now(UTC).isoformat()


class GameStudioService:
    def __init__(
        self,
        session_factory: Any,
        *,
        root: Path | None = None,
        artifacts: ArtifactStoreProvider | None = None,
    ) -> None:
        home = Path(os.environ.get("APEX_HOME", str(Path.cwd()))).resolve()
        self.root = (root or home / ".apex/game-studio/missions").resolve()
        refuse_personal_project(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.missions = MissionService(session_factory)
        self.artifacts = artifacts or get_default_provider()
        self._lock = threading.RLock()
        self._active: dict[str, tuple[threading.Thread, threading.Event, CoderSession | None]] = {}
        # A prior API process cannot still own its daemon worker. Preserve
        # completed revisions and make the stable job id resumable.
        for path in self.root.glob("*/receipt.json"):
            with suppress(OSError, ValueError, KeyError):
                stale = json.loads(path.read_text())
                if stale.get("status") in ("running", "queued"):
                    stale["status"] = "interrupted"
                    stale["current_task"] = "interrupted_on_restart"
                    stale.setdefault("events", []).append({"at": _utc(), "kind": "interrupted_on_restart"})
                    self._save(stale)
                    mission = self.missions.get(stale["mission_id"])
                    if mission and mission.state.value == "running":
                        with suppress(Exception):
                            self.missions.mark_failed(
                                stale["mission_id"], error="game_studio_process_interrupted"
                            )

    def _folder(self, job_id: str) -> Path:
        UUID(job_id)
        return self.root / job_id

    def _read(self, job_id: str) -> dict[str, Any]:
        path = self._folder(job_id) / "receipt.json"
        if not path.is_file():
            raise LookupError("game_studio_mission_missing")
        return json.loads(path.read_text())

    def _save(self, record: dict[str, Any]) -> None:
        folder = self._folder(record["id"])
        folder.mkdir(parents=True, exist_ok=True)
        record["updated_at"] = _utc()
        target = folder / "receipt.json"
        tmp = folder / "receipt.tmp"
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str))
        os.replace(tmp, target)

    def _view(self, record: dict[str, Any]) -> dict[str, Any]:
        mission = self.missions.get(record["mission_id"])
        record = {**record, "mission_state": mission.state.value if mission else "missing"}
        record.pop("references_private", None)
        return record

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            records = []
            for path in sorted(
                self.root.glob("*/receipt.json"), key=lambda p: p.stat().st_mtime, reverse=True
            )[:50]:
                with suppress(ValueError, OSError, LookupError):
                    records.append(self._view(self._read(path.parent.name)))
            return records

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return self._view(self._read(job_id))

    def create(self, request: dict[str, Any], references: list[tuple[bytes, str, str]]) -> dict[str, Any]:
        family = FAMILY.get(request["creation_type"])
        tool = request["tool"]
        if family is None or request["style"] != "realiste" or tool not in ("auto", "blender"):
            raise ValueError("requested_creation_style_or_tool_not_implemented")
        if not BENCHMARK.is_file():
            raise RuntimeError("studio_benchmark_missing_from_package")
        capabilities = probe()
        capability_key = {
            "vehicle": "vehicle_blender",
            "building": "building_blender",
            "chair": "chair_blender",
        }[family]
        if capabilities[capability_key]["status"] == "blocked_dependency":
            raise RuntimeError("blender_worker_or_local_coder_missing")
        if (request.get("compute_backend", "auto") in ("auto", "cuda")
                and not capabilities["cuda"]["available"]):
            raise RuntimeError("cuda_unavailable_choose_cpu_explicitly")
        desired = request.get("controller_model", "auto")
        native = native_coder_endpoint()
        pinned = coder_manifest().is_file() and find_spec("llama_cpp") is not None
        if desired == "auto":
            desired = "apex_native" if capabilities["cuda"]["available"] and native else (
                "pinned_7b" if pinned else "apex_native"
            )
        if desired == "pinned_7b" and not pinned:
            raise RuntimeError("pinned_7b_model_missing_no_download_performed")
        if desired == "apex_native" and native is None:
            raise RuntimeError("apex_native_coder_missing_no_download_performed")
        if (desired == "apex_native" and request.get("compute_backend") == "cpu"
                and capabilities["cuda"]["available"]):
            raise RuntimeError("native_cpu_override_not_supported_choose_pinned_7b_or_cuda")
        request = {**request, "resolved_controller_model": desired}
        overrides = request.get("criteria") or {}
        QualityCriteria.for_level(request["quality"], **overrides)
        job_id = str(uuid4())
        mission = self.missions.create(
            title="Game Studio — " + request["creation_type"],
            description=request["description"],
            objective=request["description"],
            specialty="game-graphics",
            created_by=Subject.HUMAN,
            constraints=json.dumps({k: v for k, v in request.items() if k != "description"}),
        )
        ref_records = []
        for data, mime, provenance in references:
            artifact = self.artifacts.put(data, content_type=mime)
            ref_records.append(
                {
                    "hash": artifact.content_hash,
                    "content_type": mime,
                    "provenance": provenance,
                    "used_as_visual_judge": False,
                }
            )
        record = {
            "id": job_id,
            "mission_id": mission.id,
            "mission_history": [mission.id],
            "created_at": _utc(),
            "updated_at": _utc(),
            "status": "queued",
            "current_task": "queued",
            "specialist": "Game Graphics",
            "model": (
                "Qwen2.5-Coder-7B-Instruct Q5_K_M (pinned local)"
                if desired == "pinned_7b" else str(native.served_model)
            ),
            "request": request,
            "references": ref_records,
            "revisions": [],
            "controller_attempts": [],
            "events": [],
            "error": None,
            "resource_measurements": [],
            "limitations": [
                "Image references are archived with provenance; "
                "the text-only controller does not interpret their pixels."
            ],
        }
        with self._lock:
            self._save(record)
            self._start(job_id)
            return self._view(record)

    def _start(self, job_id: str) -> None:
        cancel = threading.Event()
        thread = threading.Thread(
            target=self._execute, args=(job_id, cancel), name="apex-game-studio-" + job_id[:8], daemon=True
        )
        self._active[job_id] = (thread, cancel, None)
        thread.start()

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._read(job_id)
            if record["status"] in ("review", "failed", "cancelled"):
                return self._view(record)
            active = self._active.get(job_id)
            if active:
                active[1].set()
                session = active[2]
                if session is not None:
                    session.__exit__(None, None, None)
            record["events"].append({"at": _utc(), "kind": "cancel_requested"})
            self._save(record)
            return self._view(record)

    def resume(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._read(job_id)
            if record["status"] not in ("failed", "interrupted", "cancelled"):
                raise ValueError("only_interrupted_or_failed_jobs_can_resume")
            if job_id in self._active:
                raise ValueError("game_studio_job_already_running")
            mission = self.missions.create(
                title="Game Studio — reprise",
                description=record["request"]["description"],
                objective=record["request"]["description"],
                specialty="game-graphics",
                created_by=Subject.HUMAN,
                constraints=json.dumps({"game_studio_job_id": job_id, "resumes": record["mission_id"]}),
            )
            record["mission_id"] = mission.id
            record["mission_history"].append(mission.id)
            record["status"] = "queued"
            record["error"] = None
            record["events"].append({"at": _utc(), "kind": "resume", "mission_id": mission.id})
            self._save(record)
            self._start(job_id)
            return self._view(record)

    def artifact(self, job_id: str, digest: str) -> tuple[bytes, str]:
        record = self.get(job_id)
        for revision in record["revisions"]:
            for item in revision["artifacts"]:
                if item["hash"] == digest:
                    return self.artifacts.get(digest), item["content_type"]
        for item in record["references"]:
            if item["hash"] == digest:
                return self.artifacts.get(digest), item["content_type"]
        for item in record.get("controller_attempts", []):
            if item["hash"] == digest:
                return self.artifacts.get(digest), item["content_type"]
        raise LookupError("artifact_not_owned_by_game_studio_mission")

    def _event(self, record: dict[str, Any], kind: str, **facts: Any) -> None:
        record["events"].append({"at": _utc(), "kind": kind, **facts})
        record["current_task"] = kind
        self._save(record)

    def _archive(self, folder: Path, record: dict[str, Any], judge: dict[str, Any]) -> None:
        from apex.game_graphics.studio.judge import verified_manifest

        manifest = verified_manifest(folder)
        files = []
        for entry in manifest["files"]:
            path = folder / entry["path"]
            artifact = self.artifacts.put(
                path.read_bytes(), content_type=MIME.get(path.suffix, "application/octet-stream")
            )
            files.append(
                {
                    "name": entry["path"],
                    "hash": artifact.content_hash,
                    "content_type": MIME.get(path.suffix, "application/octet-stream"),
                    "role": entry["role"],
                    "bytes": entry["bytes"],
                }
            )
        for name in ("manifest.json", "judge-report.json", "verdict.json"):
            path = folder / name
            if path.is_file():
                item = self.artifacts.put(path.read_bytes(), content_type="application/json")
                files.append(
                    {
                        "name": name,
                        "hash": item.content_hash,
                        "content_type": "application/json",
                        "role": "report",
                        "bytes": path.stat().st_size,
                    }
                )
        controller_dir = folder.parent / f"controller-{len(record['revisions']) + 1}"
        if controller_dir.is_dir():
            for path in sorted(controller_dir.glob("*.json")):
                item = self.artifacts.put(path.read_bytes(), content_type="application/json")
                files.append({"name": "controller/" + path.name, "hash": item.content_hash,
                              "content_type": "application/json", "role": "controller",
                              "bytes": path.stat().st_size})
        execution = json.loads((folder / "execution.json").read_text())
        record["resource_measurements"].append({
            "revision": len(record["revisions"]) + 1,
            "elapsed_s": execution.get("elapsed_s"),
            "sampled_memory_peak_mb": execution.get("memory_peak_mb"),
            "cpu_time_s": execution.get("cpu_time_s"),
            "process_stopped_verified": execution.get("facts", {}).get("process_stopped_verified"),
        })
        record["revisions"].append(
            {
                "number": len(record["revisions"]) + 1,
                "artifacts": files,
                "verdict": judge["verdict"],
                "defects": judge["defects"],
                "unavailable": judge["unavailable"],
                "created_at": _utc(),
            }
        )
        self._save(record)

    def _execute(self, job_id: str, cancel: threading.Event) -> None:
        started = time.monotonic()
        with self._lock:
            record = self._read(job_id)
        mission_id = record["mission_id"]
        try:
            self.missions.mark_running(mission_id)
            record["status"] = "running"
            self._event(record, "dependency_check")
            request = record["request"]
            family = FAMILY[request["creation_type"]]
            criteria = QualityCriteria.for_level(request["quality"], **(request.get("criteria") or {}))
            budget = min(request["max_attempts"], criteria.max_distinct_defect_attempts)
            recovery_policy = RecoveryPolicy(max_attempts=budget, resource_exhausted_max_retries=1)
            benchmark = json.loads(BENCHMARK.read_text())
            folder = self._folder(job_id)
            with resource_lease(folder / "resource.lock"):
                if cancel.is_set():
                    raise InterruptedError("user_cancelled")
                completed = record["revisions"]
                if not completed:
                    root = folder / "revision-01"
                    if root.exists():
                        root.rename(folder / f"incomplete-revision-01-{uuid4().hex[:8]}")
                    self._event(record, "blender_baseline")
                    remaining = max(1, request["max_time_s"] - (time.monotonic() - started))
                    execution = create_revision(
                        root,
                        family,
                        cancel_event=cancel,
                        timeout_s=min(remaining, 600),
                        rss_limit_mib=request["max_rss_mib"],
                    )
                    if execution.status is not ExecutionStatus.SUCCESS:
                        raise RuntimeError("blender_baseline_failed:" + str(execution.error_detail))
                    self._event(record, "mandatory_integrity_judge")
                    judge = judge_revision(root, family, criteria, benchmark, cancel_event=cancel)
                    self._archive(root, record, judge)
                else:
                    from apex.game_graphics.studio.judge import verified_manifest

                    for previous in completed:
                        verified_manifest(folder / f"revision-{previous['number']:02d}")
                    passing = [item for item in completed if item["verdict"] == "pass"]
                    if not passing:
                        raise RuntimeError("no_verified_parent_revision_to_resume")
                    root = folder / f"revision-{passing[-1]['number']:02d}"
                    latest = folder / f"revision-{completed[-1]['number']:02d}"
                    judge = json.loads((latest / "judge-report.json").read_text())
                for number in range(len(record["revisions"]) + 1, budget + 1):
                    if cancel.is_set():
                        raise InterruptedError("user_cancelled")
                    if judge["verdict"] == "pass" and number > 2:
                        break
                    if number > 2 and not request["automatic_corrections"]:
                        break
                    remaining = request["max_time_s"] - (time.monotonic() - started)
                    if remaining < 120:
                        raise TimeoutError("game_studio_time_budget_exhausted")
                    next_root = folder / f"revision-{number:02d}"
                    if next_root.exists():
                        next_root.rename(folder / f"incomplete-revision-{number:02d}-{uuid4().hex[:8]}")
                    controller_root = folder / f"controller-{number}"
                    if controller_root.exists():
                        controller_root.rename(folder / f"incomplete-controller-{number}-{uuid4().hex[:8]}")
                    self._event(record, "qwen_artistic_plan", iteration=number)
                    instruction = request["description"] + ". Style réaliste, matériaux PBR. "
                    instruction += "Niveau demandé: " + request["quality"] + ". "
                    if judge["defects"] and request["automatic_corrections"]:
                        instruction += "Corrige localement ces défauts mesurés: " + json.dumps(
                            judge["defects"][:3]
                        )
                    plan_args = {
                        "family": family, "instruction": instruction,
                        "hierarchy": compact_hierarchy(root / "hierarchy.json"),
                        "feedback": judge if request["judges_all_tasks"] else None,
                        "output": folder / f"controller-{number}/plan.json",
                        "protected_topology": ("chassis",) if family == "vehicle" else (),
                        "timeout_s": min(300, max(1, int(remaining - 60))),
                    }
                    try:
                        if request.get("resolved_controller_model", "pinned_7b") == "apex_native":
                            endpoint = native_coder_endpoint()
                            if endpoint is None:
                                raise RuntimeError("apex_native_coder_became_unavailable")
                            plan = propose_plan(endpoint, **plan_args)
                            lifecycle = {
                                "model": endpoint.served_model,
                                "runtime": endpoint.runtime,
                                "provider": "apex.models.provider",
                                "device": "cuda_or_cpu_measured_by_provider",
                            }
                        else:
                            session = CoderSession(
                                coder_manifest(), folder / f"controller-{number}",
                                compute_backend=request.get("compute_backend", "cpu"),
                            )
                            with self._lock:
                                active = self._active.get(job_id)
                                if active:
                                    self._active[job_id] = (active[0], active[1], session)
                            try:
                                with session:
                                    plan = propose_plan(session.endpoint, **plan_args)
                                    session.sample_memory()
                            finally:
                                with self._lock:
                                    active = self._active.get(job_id)
                                    if active:
                                        self._active[job_id] = (active[0], active[1], None)
                            lifecycle = session.facts
                    except (ValueError, TimeoutError) as exc:
                        for path in sorted(controller_root.glob("*.json")):
                            item = self.artifacts.put(
                                path.read_bytes(), content_type="application/json"
                            )
                            record.setdefault("controller_attempts", []).append({
                                "name": f"controller-{number}/{path.name}",
                                "hash": item.content_hash,
                                "content_type": "application/json",
                                "role": "rejected_controller_plan",
                                "bytes": path.stat().st_size,
                            })
                        record["limitations"].append(
                            "The local controller could not produce a permitted artistic plan. "
                            "A verified earlier revision remains selected."
                        )
                        self._event(record, "controller_plan_rejected", iteration=number,
                                    reason=f"{type(exc).__name__}: {exc}")
                        break
                    (folder / f"controller-{number}/lifecycle.json").write_text(
                        json.dumps(lifecycle, indent=2)
                    )
                    if cancel.is_set():
                        raise InterruptedError("user_cancelled")
                    self._event(record, "blender_artistic_revision", iteration=number)
                    remaining = max(1, request["max_time_s"] - (time.monotonic() - started))
                    execution = create_revision(
                        next_root,
                        family,
                        parent=root / "asset.blend",
                        plan=plan,
                        cancel_event=cancel,
                        timeout_s=min(remaining, 600),
                        rss_limit_mib=request["max_rss_mib"],
                    )
                    if execution.status is not ExecutionStatus.SUCCESS:
                        raise RuntimeError("blender_revision_failed:" + str(execution.error_detail))
                    self._event(record, "mandatory_integrity_judge", iteration=number)
                    next_judge = judge_revision(next_root, family, criteria, benchmark, cancel_event=cancel)
                    self._archive(next_root, record, next_judge)
                    if number > 2 and compare_studio_defects(judge["defects"], next_judge["defects"]):
                        self._event(record, "correction_stagnated")
                        break
                    if next_judge["verdict"] == "pass":
                        root, judge = next_root, next_judge
                    else:
                        # Keep the last verified parent. The failed version
                        # remains immutable and visible for the next judge.
                        self._event(record, "revision_rejected", iteration=number,
                                    defects=next_judge["defects"])
                        decision = decide(
                            FailureCategory.INCORRECT_SOLUTION,
                            attempt_number=number,
                            policy=recovery_policy,
                            previous_failure=",".join(str(d.get("id")) for d in next_judge["defects"]),
                        )
                        self._event(record, "recovery_decided", **decision.to_dict())
                        if decision.action is not RecoveryAction.RETRY:
                            break
                        judge = next_judge
                if cancel.is_set():
                    raise InterruptedError("user_cancelled")
            passing = [r for r in record["revisions"] if r["verdict"] == "pass"]
            if not passing:
                raise RuntimeError("no_revision_passed_mandatory_technical_judge")
            record["selected_revision_number"] = passing[-1]["number"]
            if record["revisions"][-1]["verdict"] != "pass":
                record["limitations"].append(
                    "The latest artistic revision failed technical checks; an earlier PASS is selected."
                )
            record["status"] = "review"
            record["current_task"] = "human_review"
            self.missions.mark_review(
                mission_id, result=json.dumps({"job_id": job_id, "revisions": len(record["revisions"])})
            )
            self._event(record, "completed_for_review", elapsed_s=round(time.monotonic() - started, 2))
        except BaseException as exc:
            record["status"] = "cancelled" if cancel.is_set() else "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"[:500]
            self._event(record, "execution_stopped", reason=record["error"])
            current = self.missions.get(mission_id)
            if current and current.state.value == "running":
                with suppress(Exception):
                    if cancel.is_set():
                        self.missions.cancel(mission_id, requested_by=Subject.HUMAN)
                    else:
                        self.missions.mark_failed(mission_id, error=record["error"])
        finally:
            with self._lock:
                self._active.pop(job_id, None)

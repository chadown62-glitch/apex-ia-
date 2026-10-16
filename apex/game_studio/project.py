"""A small Game Studio recipe over real MissionService and Blender missions.

The receipt is a durable dependency/ownership map, not a second task state
machine. Each asset has its own canonical APEX Mission and judged revisions.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from PIL import Image

from apex.game_engineer.assets import decode_glb
from apex.game_engineer.contract import WHEELS
from apex.game_graphics.models import CoderSession
from apex.game_graphics.resources import resource_lease
from apex.game_graphics.studio.execution import refuse_personal_project, run_blender
from apex.missions.service import MissionService
from apex.policy.model import Subject
from apex.sandbox.model import ExecutionStatus

from .capabilities import coder_manifest, probe
from .planning import ASSETS, generate_plan
from .service import GameStudioService
from .vehicle_handoff import prepare_bundle

GARAGE_STEPS = (
    (
        "vehicle",
        "vehicule",
        (),
        "Voiture sportive réaliste pour un petit garage, carrosserie détaillée et matériaux PBR.",
    ),
    (
        "building",
        "batiment",
        ("vehicle",),
        "Bâtiment de garage urbain réaliste avec portes, façade et matériaux PBR.",
    ),
    (
        "furniture",
        "objet_3d",
        ("building",),
        "Fauteuil d'atelier réaliste servant de décor au garage, structure et matériaux PBR.",
    ),
)


class GameStudioProjectService:
    def __init__(self, graphics: GameStudioService) -> None:
        self.graphics = graphics
        self.missions: MissionService = graphics.missions
        self.root = graphics.root / "projects"
        refuse_personal_project(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._active: dict[str, tuple[threading.Thread, threading.Event]] = {}

    def _path(self, project_id: str) -> Path:
        UUID(project_id)
        return self.root / f"{project_id}.json"

    def _read(self, project_id: str) -> dict[str, Any]:
        path = self._path(project_id)
        if not path.is_file():
            raise LookupError("game_studio_project_missing")
        return json.loads(path.read_text())

    def _save(self, receipt: dict[str, Any]) -> None:
        path = self._path(receipt["id"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        os.replace(temporary, path)

    @staticmethod
    def _event(kind: str, **facts: Any) -> dict[str, Any]:
        return {"at": datetime.now(UTC).isoformat(), "kind": kind, **facts}

    def _view(self, receipt: dict[str, Any]) -> dict[str, Any]:
        mission = self.missions.get(receipt["mission_id"])
        steps = []
        for step in receipt["steps"]:
            child = self.graphics.get(step["job_id"]) if step.get("job_id") else None
            steps.append(
                {
                    **step,
                    "status": child["status"] if child else step.get("status", "queued"),
                    "mission": child,
                }
            )
        state = mission.state.value if mission else "missing"
        status = {
            "queued": "queued",
            "running": "running",
            "review": "review",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(state, state)
        if state == "running" and receipt["id"] not in self._active:
            status = "interrupted"
        return {**receipt, "mission_state": state, "status": status, "steps": steps}

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self._view(json.loads(path.read_text()))
                for path in sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[
                    :50
                ]
            ]

    def get(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            return self._view(self._read(project_id))

    def _verify_selected_artifacts(self, child: dict[str, Any]) -> None:
        selected = child["revisions"][child["selected_revision_number"] - 1]
        for artifact in selected["artifacts"]:
            self.graphics.artifact(child["id"], artifact["hash"])

    def create(self, request: dict[str, Any], references: list[tuple[bytes, str, str]]) -> dict[str, Any]:
        if (
            request["creation_type"] != "environnement"
            or request["style"] != "realiste"
            or request["tool"] not in ("auto", "blender")
        ):
            raise ValueError("garage_recipe_requires_realistic_blender_environment")
        if references:
            raise ValueError("garage_visual_references_not_consumed_by_controller")
        # Validate capabilities and quality before reserving a parent mission.
        from apex.game_graphics.contract import QualityCriteria

        from .capabilities import probe

        QualityCriteria.for_level(request["quality"], **(request.get("criteria") or {}))
        capabilities = probe()
        if any(
            capabilities[key]["status"] == "blocked_dependency"
            for key in ("vehicle_blender", "building_blender", "chair_blender")
        ):
            raise RuntimeError("blender_worker_or_local_coder_missing")
        if request.get("compute_backend", "auto") != "cpu" and not capabilities["cuda"]["available"]:
            raise RuntimeError("cuda_unavailable_choose_cpu_explicitly")
        mission = self.missions.create(
            title="Game Studio — garage multi-assets",
            description=request["description"],
            objective=request["description"],
            specialty="game-graphics",
            created_by=Subject.HUMAN,
            constraints=json.dumps({"recipe": "garage-v1", "request": request}),
        )
        receipt = {
            "id": str(uuid4()),
            "mission_id": mission.id,
            "mission_history": [mission.id],
            "created_at": datetime.now(UTC).isoformat(),
            "recipe": "garage-v1",
            "request": request,
            "steps": [
                {
                    "key": key,
                    "creation_type": family,
                    "depends_on": list(deps),
                    "description": description,
                    "specialist": "Game Graphics",
                    "job_id": None,
                }
                for key, family, deps, description in GARAGE_STEPS
            ],
            "limitations": [
                "The garage recipe delivers three individually judged Blender assets and an "
                "assembled Blender scene; Unreal gameplay is not yet implemented.",
                "The task decomposition is a fixed, disclosed recipe; it is not generated by an LLM.",
            ],
            "events": [],
            "assembly": None,
            "assembly_history": [],
        }
        with self._lock:
            self._save(receipt)
            self._start(receipt["id"])
        return self.get(receipt["id"])

    def create_dynamic(
        self,
        request: dict[str, Any],
        references: list[tuple[bytes, str, str]],
        *,
        owner_workspace_id: str | None = None,
    ) -> dict[str, Any]:
        from apex.game_graphics.contract import QualityCriteria

        QualityCriteria.for_level(request["quality"], **(request.get("criteria") or {}))
        available = probe()
        if request.get("compute_backend", "auto") != "cpu" and not available["cuda"]["available"]:
            raise RuntimeError("cuda_unavailable_choose_cpu_explicitly")
        if not coder_manifest().is_file():
            raise RuntimeError("local_planner_model_missing")
        if not 1 <= len(references) <= 4 and references:
            raise ValueError("reference_count_invalid")
        mission = self.missions.create(
            title="Game Studio — projet planifié",
            description=request["description"],
            objective=request["description"],
            specialty="game-graphics",
            created_by=Subject.HUMAN,
            constraints=json.dumps({"recipe": "dynamic-v1", "request": request}),
        )
        stored = []
        for data, mime, provenance in references:
            item = self.graphics.artifacts.put(data, content_type=mime)
            stored.append(
                {
                    "hash": item.content_hash,
                    "content_type": mime,
                    "provenance": provenance,
                    "used_as_visual_judge": False,
                }
            )
        receipt = {
            "id": str(uuid4()),
            "mission_id": mission.id,
            "mission_history": [mission.id],
            "created_at": datetime.now(UTC).isoformat(),
            "recipe": "dynamic-v1",
            "owner_workspace_id": owner_workspace_id,
            "request": request,
            "references": stored,
            "plan": None,
            "steps": [],
            "events": [],
            "assembly": None,
            "assembly_history": [],
            "delivery_status": "planning",
            "limitations": [
                "Visual references are preserved but not interpreted by this planner.",
                "Unreal and CUDA remain unqualified on this host.",
            ],
        }
        with self._lock:
            self._save(receipt)
            self._start(receipt["id"])
        return self.get(receipt["id"])

    def _start(self, project_id: str) -> None:
        cancel = threading.Event()
        worker = threading.Thread(
            target=self._run,
            args=(project_id, cancel),
            daemon=True,
            name="apex-game-project-" + project_id[:8],
        )
        self._active[project_id] = (worker, cancel)
        worker.start()

    def _run(self, project_id: str, cancel: threading.Event) -> None:
        started = time.monotonic()
        with self._lock:
            initial = self._read(project_id)
        if initial["recipe"] == "dynamic-v1":
            self._run_dynamic(project_id, cancel, started)
            return
        try:
            with self._lock:
                receipt = self._read(project_id)
                mission_id = receipt["mission_id"]
            self.missions.mark_running(mission_id)
            request = receipt["request"]
            for index, step in enumerate(receipt["steps"]):
                if cancel.is_set():
                    return
                for dependency in step["depends_on"]:
                    parent = next(item for item in receipt["steps"] if item["key"] == dependency)
                    if not parent["job_id"] or self.graphics.get(parent["job_id"])["status"] != "review":
                        raise RuntimeError("step_dependency_not_verified:" + dependency)
                if step["job_id"]:
                    child = self.graphics.get(step["job_id"])
                    if child["status"] == "review":
                        self._verify_selected_artifacts(child)
                        continue
                    if child["status"] in ("interrupted", "failed", "cancelled"):
                        child = self.graphics.resume(child["id"])
                else:
                    remaining = request["max_time_s"] - (time.monotonic() - started)
                    if remaining < 120:
                        raise TimeoutError("project_time_budget_exhausted")
                    child_request = {
                        **request,
                        "creation_type": step["creation_type"],
                        "description": request["description"] + " " + step["description"],
                        "max_time_s": min(3600, max(120, int(remaining / (len(receipt["steps"]) - index)))),
                    }
                    child = self.graphics.create(child_request, [])
                    with self._lock:
                        receipt = self._read(project_id)
                        receipt["steps"][index]["job_id"] = child["id"]
                        receipt["events"].append(
                            self._event("child_started", key=step["key"], job_id=child["id"])
                        )
                        self._save(receipt)
                while child["status"] in ("queued", "running"):
                    if cancel.is_set():
                        self.graphics.cancel(child["id"])
                        return
                    if time.monotonic() - started > request["max_time_s"]:
                        self.graphics.cancel(child["id"])
                        raise TimeoutError("project_time_budget_exhausted")
                    time.sleep(0.5)
                    child = self.graphics.get(child["id"])
                if child["status"] != "review":
                    raise RuntimeError(f"child_{step['key']}_{child['status']}:{child.get('error')}")
                self._verify_selected_artifacts(child)
                with self._lock:
                    receipt = self._read(project_id)
                    receipt["events"].append(
                        self._event(
                            "child_verified",
                            key=step["key"],
                            job_id=child["id"],
                            revision=child["selected_revision_number"],
                        )
                    )
                    self._save(receipt)
            self._assemble(project_id, receipt, cancel, started)
            if cancel.is_set():
                return
            self.missions.mark_review(
                mission_id,
                result=json.dumps(
                    {
                        "project_id": project_id,
                        "child_missions": [s["job_id"] for s in receipt["steps"]],
                        "assembly": True,
                    }
                ),
            )
            with self._lock:
                receipt = self._read(project_id)
                receipt["elapsed_s"] = round(time.monotonic() - started, 2)
                receipt["finished_at"] = datetime.now(UTC).isoformat()
                receipt["events"].append(self._event("completed_for_review"))
                self._save(receipt)
        except Exception as exc:
            current = self.missions.get(mission_id)
            if current and current.state.value == "running":
                self.missions.mark_failed(mission_id, error=f"{type(exc).__name__}: {exc}"[:500])
            with self._lock:
                receipt = self._read(project_id)
                receipt["events"].append(
                    self._event("project_failed", reason=f"{type(exc).__name__}: {exc}"[:500])
                )
                self._save(receipt)
        finally:
            with self._lock:
                self._active.pop(project_id, None)

    def _run_dynamic(self, project_id: str, cancel: threading.Event, started: float) -> None:
        receipt = self._read(project_id)
        mission_id = receipt["mission_id"]
        folder = self.root / project_id
        try:
            self.missions.mark_running(mission_id)
            request = receipt["request"]
            if receipt["plan"] is None:
                trace_file = folder / "planner-trace.json"
                previous = json.loads(trace_file.read_text()) if trace_file.is_file() else {}
                if previous.get("status") == "validated" and previous.get("accepted"):
                    from .planning import _json_text, validate_plan

                    plan = validate_plan(
                        _json_text(previous["attempts"][-1]["raw_response"]),
                        request={**request, "reference_count": len(receipt["references"])},
                        capabilities=probe(),
                    )
                    model_facts = {
                        "model": previous["attempts"][-1]["generation"]["model_used"],
                        "validated_trace_reused_on_resume": True,
                    }
                else:
                    if trace_file.exists():
                        trace_file.rename(folder / f"planner-trace-rejected-{uuid4().hex[:8]}.json")
                    with resource_lease(folder / "planner-resource.lock"):
                        session = CoderSession(
                            coder_manifest(),
                            folder / "planner-model",
                            compute_backend=request["compute_backend"],
                        )
                        with session:
                            plan = generate_plan(
                                session.endpoint,
                                request={**request, "reference_count": len(receipt["references"])},
                                capabilities=probe(),
                                output=trace_file,
                                timeout_s=min(240, request["max_time_s"] // 3),
                            )
                            session.sample_memory()
                        model_facts = session.facts
                trace = (folder / "planner-trace.json").read_bytes()
                artifact = self.graphics.artifacts.put(trace, content_type="application/json")
                receipt["planning_artifact"] = {
                    "name": "planner-trace.json",
                    "hash": artifact.content_hash,
                    "content_type": "application/json",
                    "role": "report",
                    "bytes": len(trace),
                }
                receipt["planner_model"] = model_facts
                receipt["plan"] = plan
                receipt["steps"] = [
                    {
                        **step,
                        "job_id": None,
                        "specialist": "Game Graphics"
                        if step["kind"] in set(ASSETS) | {"assemble"}
                        else "Game Engineer"
                        if step["kind"] == "unreal_handoff"
                        else "unavailable",
                    }
                    for step in plan["steps"]
                ]
                receipt["events"].append(
                    self._event(
                        "model_plan_validated",
                        steps=len(plan["steps"]),
                        planner_model=model_facts.get("model"),
                    )
                )
                with self._lock:
                    self._save(receipt)
            if request.get("plan_only"):
                receipt["delivery_status"] = "plan_only"
            else:
                for index, step in enumerate(receipt["steps"]):
                    if cancel.is_set():
                        return
                    if step["kind"] not in ASSETS or not step["status"].startswith("available"):
                        continue
                    if step["job_id"]:
                        child = self.graphics.get(step["job_id"])
                        if child["status"] == "review":
                            self._verify_selected_artifacts(child)
                        elif child["status"] in ("interrupted", "failed", "cancelled"):
                            child = self.graphics.resume(child["id"])
                    else:
                        remaining = request["max_time_s"] - (time.monotonic() - started)
                        if remaining < 120:
                            raise TimeoutError("project_time_budget_exhausted")
                        child_request = {
                            **request,
                            "tool": "blender" if request["tool"] == "hybride" else request["tool"],
                            "creation_type": ASSETS[step["kind"]],
                            "description": step["description"],
                            "max_time_s": min(
                                1200, max(120, int(remaining / max(1, len(receipt["steps"]) - index)))
                            ),
                        }
                        child = self.graphics.create(child_request, [])
                        step["job_id"] = child["id"]
                        receipt["events"].append(
                            self._event("child_started", key=step["key"], job_id=child["id"])
                        )
                        with self._lock:
                            self._save(receipt)
                    while child["status"] in ("queued", "running"):
                        if cancel.is_set():
                            self.graphics.cancel(child["id"])
                            return
                        if time.monotonic() - started > request["max_time_s"]:
                            self.graphics.cancel(child["id"])
                            raise TimeoutError("project_time_budget_exhausted")
                        time.sleep(0.5)
                        child = self.graphics.get(child["id"])
                    if child["status"] != "review":
                        raise RuntimeError(f"child_{step['key']}_{child['status']}:{child.get('error')}")
                    self._verify_selected_artifacts(child)
                    if step["kind"] == "vehicle" and (
                        not step.get("handoff")
                        or (
                            request.get("automatic_corrections")
                            and step["handoff"].get("status") != "pass_preflight"
                        )
                    ):
                        self._vehicle_handoff(project_id, step, child, cancel, started)
                        receipt["events"] = self._read(project_id)["events"]
                    step["status"] = "review"
                    receipt["events"].append(
                        self._event("child_verified", key=step["key"], job_id=child["id"])
                    )
                    with self._lock:
                        self._save(receipt)
                assemblies = [s for s in receipt["steps"] if s["kind"] == "assemble"]
                if assemblies and all(
                    s["status"] == "review" for s in receipt["steps"] if s["kind"] in ASSETS
                ):
                    self._assemble(project_id, receipt, cancel, started)
                    assemblies[0]["status"] = "review"
                    with self._lock:
                        receipt = self._read(project_id)
                        next(s for s in receipt["steps"] if s["kind"] == "assemble")["status"] = "review"
                        self._save(receipt)
                receipt["delivery_status"] = (
                    "partial"
                    if any(
                        s["status"] in ("blocked_dependency", "not_implemented")
                        or (
                            s.get("kind") == "vehicle"
                            and s.get("handoff", {}).get("status") != "pass_preflight"
                        )
                        for s in receipt["steps"]
                    )
                    else "technical_review"
                )
            self.missions.mark_review(
                mission_id,
                result=json.dumps(
                    {
                        "project_id": project_id,
                        "delivery_status": receipt["delivery_status"],
                        "plan_validated": True,
                    }
                ),
            )
            receipt["elapsed_s"] = round(time.monotonic() - started, 2)
            receipt["finished_at"] = datetime.now(UTC).isoformat()
            receipt["events"].append(
                self._event("completed_for_review", delivery=receipt["delivery_status"])
            )
            with self._lock:
                self._save(receipt)
        except Exception as exc:
            current = self.missions.get(mission_id)
            if current and current.state.value == "running":
                self.missions.mark_failed(mission_id, error=f"{type(exc).__name__}: {exc}"[:500])
            with self._lock:
                receipt = self._read(project_id)
                trace_file = folder / "planner-trace.json"
                if trace_file.is_file() and not receipt.get("planning_artifact"):
                    stored = self.graphics.artifacts.put(
                        trace_file.read_bytes(), content_type="application/json"
                    )
                    receipt["planning_artifact"] = {
                        "name": "planner-trace.json",
                        "hash": stored.content_hash,
                        "content_type": "application/json",
                        "role": "report",
                        "bytes": trace_file.stat().st_size,
                    }
                receipt["events"].append(
                    self._event("project_failed", reason=f"{type(exc).__name__}: {exc}"[:500])
                )
                self._save(receipt)
        finally:
            with self._lock:
                self._active.pop(project_id, None)

    def _vehicle_handoff(
        self,
        project_id: str,
        step: dict[str, Any],
        child: dict[str, Any],
        cancel: threading.Event,
        started: float,
    ) -> None:
        folder = self.root / project_id / "handoff" / step["key"]
        folder.mkdir(parents=True, exist_ok=True)
        for name in ("before", "after", "corrected"):
            previous = folder / name
            if previous.exists():
                previous.rename(folder / f"interrupted-{name}-{uuid4().hex[:8]}")
        selected = child["revisions"][child["selected_revision_number"] - 1]
        revision = self.graphics._folder(child["id"]) / f"revision-{child['selected_revision_number']:02d}"
        glb_item = next(a for a in selected["artifacts"] if a["name"] == "asset.glb")
        before = prepare_bundle(revision, folder / "before", source_hash=glb_item["hash"])

        def archived(bundle: Path) -> list[dict[str, Any]]:
            items = []
            for path in sorted(bundle.iterdir()):
                if path.suffix not in (".json", ".glb", ".blend"):
                    continue
                mime = (
                    "application/json"
                    if path.suffix == ".json"
                    else "model/gltf-binary"
                    if path.suffix == ".glb"
                    else "application/octet-stream"
                )
                object_ = self.graphics.artifacts.put(path.read_bytes(), content_type=mime)
                items.append(
                    {
                        "name": bundle.name + "/" + path.name,
                        "hash": object_.content_hash,
                        "content_type": mime,
                        "role": "report"
                        if path.suffix == ".json"
                        else "exchange"
                        if path.suffix == ".glb"
                        else "blender_project",
                        "bytes": path.stat().st_size,
                    }
                )
            return items

        before_files = archived(folder / "before")

        def persist_handoff() -> None:
            with self._lock:
                current = self._read(project_id)
                next(s for s in current["steps"] if s["key"] == step["key"])["handoff"] = step["handoff"]
                current["events"].append(
                    self._event("engineer_preflight", key=step["key"], verdict=step["handoff"]["status"])
                )
                self._save(current)

        step["handoff"] = {
            "before": before,
            "after": None,
            "status": before["verdict"],
            "artifacts": before_files,
        }
        if before["verdict"] != "pass_preflight":
            persist_handoff()
        if before["verdict"] == "pass_preflight":
            step["handoff"] = {
                "before": before,
                "after": before,
                "selected_hash": glb_item["hash"],
                "status": "pass_preflight",
                "artifacts": before_files,
            }
            persist_handoff()
            return
        if cancel.is_set() or not self._read(project_id)["request"].get("automatic_corrections"):
            step["handoff"] = {
                "before": before,
                "after": None,
                "status": "blocked",
                "artifacts": before_files,
            }
            persist_handoff()
            return
        repaired = folder / "corrected"
        remaining = self._read(project_id)["request"]["max_time_s"] - (time.monotonic() - started)
        with resource_lease(folder / "repair-resource.lock"):
            result = run_blender(
                "render_asset.py",
                [
                    "--family",
                    "vehicle",
                    "--input",
                    str(revision / "asset.blend"),
                    "--output",
                    str(repaired),
                    "--contract-wheels",
                    "--parent-sha",
                    hashlib.sha256((revision / "asset.blend").read_bytes()).hexdigest(),
                ],
                folder / "repair.log",
                timeout_s=min(400, remaining),
                rss_limit_mib=self._read(project_id)["request"]["max_rss_mib"],
                cancel_event=cancel,
            )
        if result.status is not ExecutionStatus.SUCCESS:
            raise RuntimeError("vehicle_contract_repair_failed:" + str(result.error_detail))
        corrected_glb = repaired / "asset.glb"
        digest = "sha256:" + hashlib.sha256(corrected_glb.read_bytes()).hexdigest()
        after = prepare_bundle(repaired, folder / "after", source_hash=digest)
        step["handoff"] = {
            "before": before,
            "after": after,
            "selected_hash": digest,
            "repair_execution": result.to_dict(),
            "status": after["verdict"],
            "artifacts": [*before_files, *archived(folder / "after")],
        }
        persist_handoff()
        if after["verdict"] != "pass_preflight":
            raise RuntimeError("vehicle_contract_repair_did_not_pass:" + str(after.get("error")))

    def _assemble(
        self, project_id: str, receipt: dict[str, Any], cancel: threading.Event, started: float
    ) -> None:
        if receipt.get("assembly"):
            for item in receipt["assembly"]["artifacts"]:
                self.artifact(project_id, item["hash"])
            return
        folder = self.root / project_id
        refuse_personal_project(folder)
        folder.mkdir(parents=True, exist_ok=True)
        inputs = folder / "inputs"
        inputs.mkdir(exist_ok=True)
        dynamic = receipt["recipe"] == "dynamic-v1"
        asset_steps = (
            [s for s in receipt["steps"] if s.get("kind") in ASSETS] if dynamic else receipt["steps"]
        )
        source_artifacts = {}
        vehicle_gltf: dict[str, Any] | None = None
        manifest_assets = []
        for offset_index, step in enumerate(asset_steps):
            child = self.graphics.get(step["job_id"])
            if child["status"] != "review":
                raise RuntimeError("assembly_dependency_not_reviewed:" + step["key"])
            selected = child["revisions"][child["selected_revision_number"] - 1]
            glb = next((item for item in selected["artifacts"] if item["name"] == "asset.glb"), None)
            if glb is None:
                raise RuntimeError("assembly_dependency_glb_missing:" + step["key"])
            data, _mime = self.graphics.artifact(child["id"], glb["hash"])
            if (
                dynamic
                and step.get("handoff", {}).get("selected_hash") != glb["hash"]
                and step.get("handoff", {}).get("status") == "pass_preflight"
            ):
                selected_path = folder / "handoff" / step["key"] / "corrected" / "asset.glb"
                data = selected_path.read_bytes()
                glb = {**glb, "hash": "sha256:" + hashlib.sha256(data).hexdigest()}
            gltf = decode_glb(data)
            if step.get("kind") == "vehicle" or step["key"] == "vehicle":
                vehicle_gltf = gltf
            (inputs / (step["key"] + ".glb")).write_bytes(data)
            if dynamic:
                family_index = sum(s.get("kind") == step["kind"] for s in asset_steps[:offset_index])
                position = (
                    {
                        "vehicle": [family_index * 4.2 - 2.1, -2.8, 0],
                        "building": [family_index * 7.0, 3.2, 0],
                        "chair": [family_index * 2.0 + 3.0, 1.0, 0],
                    }
                )[step["kind"]]
                manifest_assets.append(
                    {"key": step["key"], "path": str(inputs / (step["key"] + ".glb")), "offset_m": position}
                )
            source_artifacts[step["key"]] = {
                "mission_id": child["mission_id"],
                "job_id": child["id"],
                "hash": glb["hash"],
                "selected_from": "project_vehicle_handoff"
                if dynamic
                and step.get("handoff", {}).get("selected_hash") == glb["hash"]
                and step.get("handoff", {}).get("repair_execution")
                else "child_selected_revision",
            }
        output = folder / "assembly"
        if output.exists():
            output.rename(folder / f"incomplete-assembly-{uuid4().hex[:8]}")
        remaining = receipt["request"]["max_time_s"] - (time.monotonic() - started)
        if remaining < 120:
            raise TimeoutError("project_time_budget_exhausted_before_assembly")
        with resource_lease(folder / "assembly-resource.lock"):
            arguments = (
                ["--output", str(output), "--assets-manifest", str(inputs / "assets.json")]
                if dynamic
                else [
                    "--output",
                    str(output),
                    "--vehicle",
                    str(inputs / "vehicle.glb"),
                    "--building",
                    str(inputs / "building.glb"),
                    "--furniture",
                    str(inputs / "furniture.glb"),
                ]
            )
            if dynamic:
                (inputs / "assets.json").write_text(json.dumps(manifest_assets))
            result = run_blender(
                "assemble_garage.py",
                arguments,
                folder / "assembly-build.log",
                timeout_s=min(600, remaining),
                rss_limit_mib=receipt["request"]["max_rss_mib"],
                cancel_event=cancel,
            )
            if result.status is not ExecutionStatus.SUCCESS:
                raise RuntimeError(
                    "garage_blender_assembly_failed:" + str(result.error_detail or result.stdout[-1500:])
                )
            if cancel.is_set():
                return
            remaining = receipt["request"]["max_time_s"] - (time.monotonic() - started)
            if remaining < 5:
                raise TimeoutError("project_time_budget_exhausted_before_reopen")
            reopened = run_blender(
                "assemble_garage.py",
                ["--verify", "--output", str(output)],
                folder / "assembly-reopen.log",
                timeout_s=min(120, remaining),
                rss_limit_mib=receipt["request"]["max_rss_mib"],
                cancel_event=cancel,
            )
        if reopened.status is not ExecutionStatus.SUCCESS:
            raise RuntimeError(
                "garage_blend_reopen_failed:" + str(reopened.error_detail or reopened.stdout[-1500:])
            )
        report = json.loads((output / "reopen-report.json").read_text())
        framing = json.loads((output / "framing-report.json").read_text())
        if any(view.get("result") != "pass_geometric_framing" for view in framing.values()):
            raise RuntimeError("assembly_geometric_framing_failed")
        counts = report["source_mesh_counts"]
        if not report["reopened_in_second_blender_process"] or any(
            type(counts.get(key)) is not int or counts[key] < 1
            for key in (
                [s["key"] for s in asset_steps] if dynamic else ("vehicle", "building", "furniture")
            )
        ):
            raise RuntimeError("garage_reopen_source_objects_missing")
        if (
            len(report.get("named_lights", [])) != 3
            or not report.get("camera")
            or not report.get("street_and_sidewalk_present")
        ):
            raise RuntimeError("garage_reopen_environment_missing")
        decode_glb((output / "garage.glb").read_bytes())
        vehicle_nodes = {node.get("name") for node in (vehicle_gltf or {}).get("nodes", [])}
        missing_wheels = sorted(set(WHEELS) - vehicle_nodes) if vehicle_gltf else []
        handoff = {
            "specialist": "Game Engineer preflight",
            "status": "pass_preflight"
            if dynamic
            and any(s.get("kind") == "vehicle" for s in asset_steps)
            and all(
                s.get("handoff", {}).get("after", {}).get("verdict") == "pass_preflight"
                for s in asset_steps
                if s.get("kind") == "vehicle"
            )
            else "blocked",
            "source_vehicle_artifact": source_artifacts.get("vehicle"),
            "glb_structure_verified": vehicle_gltf is not None,
            "missing_vehicle_contract_wheel_nodes": missing_wheels,
            "unreal_editor_executed": False,
            "vehicle_physics_executed": False,
            "reason": "studio_vehicle_export_not_yet_VehicleAssetContract_v1_compatible"
            if missing_wheels
            else "real_unreal_editor_validation_unavailable",
        }
        if dynamic:
            handoff["vehicle_reports"] = {
                s["key"]: s.get("handoff") for s in asset_steps if s.get("kind") == "vehicle"
            }
            handoff["reason"] = (
                "real_unreal_editor_validation_unavailable"
                if handoff["vehicle_reports"]
                else "no_vehicle_requested"
            )
        (output / "engineer-handoff.json").write_text(json.dumps(handoff, indent=2))
        for name in ("front_three_quarter", "rear_three_quarter", "side", "top"):
            with Image.open(output / (name + ".png")) as image:
                image.verify()
            with Image.open(output / (name + ".png")) as image:
                if image.size != (960, 720):
                    raise RuntimeError("garage_native_render_dimensions_invalid")
        (output / "judge-report.json").write_text(
            json.dumps(
                {
                    "verdict": "pass_technical",
                    "scope": "file_integrity_native_reopen_and_source_presence",
                    "source_mesh_counts": counts,
                    "source_artifacts": source_artifacts,
                    "native_renders": 4,
                    "geometric_framing": framing,
                    "unavailable": [
                        "aesthetic_quality_calibration",
                        "unreal_import",
                        "vehicle_physics",
                        "target_gpu_performance",
                    ],
                },
                indent=2,
            )
        )
        (output / "manifest.json").write_text(
            json.dumps(
                {
                    "method": "trusted_blender_assembly",
                    "source_artifacts": source_artifacts,
                    "files": {
                        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in sorted(output.iterdir())
                        if path.is_file()
                    },
                },
                indent=2,
            )
        )
        files = []
        for path in [
            *sorted(output.iterdir()),
            folder / "assembly-build.log",
            folder / "assembly-reopen.log",
            folder / "assembly-build.resources.json",
            folder / "assembly-reopen.resources.json",
        ]:
            if not path.is_file():
                continue
            role = (
                "view"
                if path.suffix == ".png"
                else "blender_project"
                if path.suffix == ".blend"
                else "exchange"
                if path.suffix == ".glb"
                else "report"
            )
            mime = (
                "image/png"
                if path.suffix == ".png"
                else "model/gltf-binary"
                if path.suffix == ".glb"
                else "application/json"
                if path.suffix == ".json"
                else "text/plain"
                if path.suffix == ".log"
                else "application/octet-stream"
            )
            item = self.graphics.artifacts.put(path.read_bytes(), content_type=mime)
            files.append(
                {
                    "name": path.name,
                    "hash": item.content_hash,
                    "content_type": mime,
                    "role": role,
                    "bytes": path.stat().st_size,
                }
            )
        with self._lock:
            current = self._read(project_id)
            current["assembly"] = {
                "version": len(current.get("assembly_history", [])) + 1,
                "verdict": "pass_technical",
                "artifacts": files,
                "source_mesh_counts": counts,
                "source_artifacts": source_artifacts,
                "engineer_handoff": handoff,
                "geometric_framing": framing,
                "blender_execution": result.to_dict(),
                "reopen_execution": reopened.to_dict(),
                "visual_quality_certified": False,
            }
            current["events"].append(self._event("garage_assembly_verified", artifacts=len(files)))
            self._save(current)

    def artifact(self, project_id: str, digest: str) -> tuple[bytes, str]:
        receipt = self._read(project_id)
        planning = receipt.get("planning_artifact")
        if planning and planning["hash"] == digest:
            return self.graphics.artifacts.get(digest), planning["content_type"]
        for step in receipt["steps"]:
            for item in (step.get("handoff") or {}).get("artifacts", []):
                if item["hash"] == digest:
                    return self.graphics.artifacts.get(digest), item["content_type"]
        for assembly in [receipt.get("assembly"), *receipt.get("assembly_history", [])]:
            for item in (assembly or {}).get("artifacts", []):
                if item["hash"] == digest:
                    return self.graphics.artifacts.get(digest), item["content_type"]
        raise LookupError("artifact_not_owned_by_game_studio_project")

    def improve_framing(self, project_id: str) -> dict[str, Any]:
        """One bounded scene rebuild, retaining the first native scene and artifacts."""
        with self._lock:
            receipt = self._read(project_id)
            if receipt["recipe"] == "dynamic-v1":
                raise ValueError("dynamic_scene_uses_automatic_bounded_framing")
            if project_id in self._active:
                raise ValueError("project_already_running")
            mission = self.missions.get(receipt["mission_id"])
            if not mission or mission.state.value != "review" or not receipt.get("assembly"):
                raise ValueError("project_scene_not_ready_for_framing_revision")
            if receipt.get("assembly_history"):
                raise ValueError("framing_revision_budget_exhausted")
            folder = self.root / project_id
            refuse_personal_project(folder)
            output = folder / "assembly"
            if not output.is_dir():
                raise ValueError("project_assembly_files_missing")
            output.rename(folder / "assembly-version-01")
            receipt.setdefault("assembly_history", []).append(receipt["assembly"])
            receipt["assembly"] = None
            fresh = self.missions.create(
                title="Game Studio — correction cadrage garage",
                description=receipt["request"]["description"],
                objective="Rebuild the verified Blender scene with complete building framing",
                specialty="game-graphics",
                created_by=Subject.HUMAN,
                constraints=json.dumps({"revises": mission.id, "project_id": project_id}),
            )
            receipt["mission_id"] = fresh.id
            receipt["mission_history"].append(fresh.id)
            receipt["events"].append(self._event("framing_revision_started", mission_id=fresh.id))
            self._save(receipt)
            self._start(project_id)
        return self.get(project_id)

    def cancel(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            receipt = self._read(project_id)
            active = self._active.get(project_id)
            if active:
                active[1].set()
            for step in receipt["steps"]:
                if step["job_id"]:
                    child = self.graphics.get(step["job_id"])
                    if child["status"] in ("queued", "running"):
                        self.graphics.cancel(child["id"])
            mission = self.missions.get(receipt["mission_id"])
            if mission and mission.state.value in ("queued", "running"):
                self.missions.cancel(mission.id, requested_by=Subject.HUMAN)
        return self.get(project_id)

    def resume(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            receipt = self._read(project_id)
            if project_id in self._active:
                raise ValueError("project_already_running")
            mission = self.missions.get(receipt["mission_id"])
            if not mission or mission.state.value not in ("failed", "cancelled", "running", "review"):
                raise ValueError("only_interrupted_failed_or_incomplete_projects_can_resume")
            if mission.state.value == "review" and (
                receipt.get("assembly")
                or receipt.get("delivery_status") in ("plan_only", "technical_review", "partial")
            ):
                raise ValueError("project_already_delivered_for_review")
            if mission.state.value == "running":
                self.missions.mark_failed(mission.id, error="game_studio_project_process_interrupted")
            fresh = self.missions.create(
                title="Game Studio — reprise garage",
                description=receipt["request"]["description"],
                objective=receipt["request"]["description"],
                specialty="game-graphics",
                created_by=Subject.HUMAN,
                constraints=json.dumps({"resumes": mission.id, "project_id": project_id}),
            )
            receipt["mission_id"] = fresh.id
            receipt["mission_history"].append(fresh.id)
            receipt["events"].append(self._event("resume", mission_id=fresh.id))
            self._save(receipt)
            self._start(project_id)
        return self.get(project_id)

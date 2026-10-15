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
from apex.game_graphics.resources import resource_lease
from apex.game_graphics.studio.execution import refuse_personal_project, run_blender
from apex.missions.service import MissionService
from apex.policy.model import Subject
from apex.sandbox.model import ExecutionStatus

from .service import GameStudioService

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
            steps.append({**step, "status": child["status"] if child else "queued", "mission": child})
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
        source_artifacts = {}
        vehicle_gltf: dict[str, Any] | None = None
        for step in receipt["steps"]:
            child = self.graphics.get(step["job_id"])
            if child["status"] != "review":
                raise RuntimeError("assembly_dependency_not_reviewed:" + step["key"])
            selected = child["revisions"][child["selected_revision_number"] - 1]
            glb = next((item for item in selected["artifacts"] if item["name"] == "asset.glb"), None)
            if glb is None:
                raise RuntimeError("assembly_dependency_glb_missing:" + step["key"])
            data, _mime = self.graphics.artifact(child["id"], glb["hash"])
            gltf = decode_glb(data)
            if step["key"] == "vehicle":
                vehicle_gltf = gltf
            (inputs / (step["key"] + ".glb")).write_bytes(data)
            source_artifacts[step["key"]] = {
                "mission_id": child["mission_id"],
                "job_id": child["id"],
                "hash": glb["hash"],
            }
        output = folder / "assembly"
        if output.exists():
            output.rename(folder / f"incomplete-assembly-{uuid4().hex[:8]}")
        remaining = receipt["request"]["max_time_s"] - (time.monotonic() - started)
        if remaining < 120:
            raise TimeoutError("project_time_budget_exhausted_before_assembly")
        with resource_lease(folder / "assembly-resource.lock"):
            result = run_blender(
                "assemble_garage.py",
                [
                    "--output",
                    str(output),
                    "--vehicle",
                    str(inputs / "vehicle.glb"),
                    "--building",
                    str(inputs / "building.glb"),
                    "--furniture",
                    str(inputs / "furniture.glb"),
                ],
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
        counts = report["source_mesh_counts"]
        if not report["reopened_in_second_blender_process"] or any(
            type(counts.get(key)) is not int or counts[key] < 1
            for key in ("vehicle", "building", "furniture")
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
        missing_wheels = sorted(set(WHEELS) - vehicle_nodes)
        handoff = {
            "specialist": "Game Engineer preflight",
            "status": "blocked",
            "source_vehicle_artifact": source_artifacts["vehicle"],
            "glb_structure_verified": vehicle_gltf is not None,
            "missing_vehicle_contract_wheel_nodes": missing_wheels,
            "unreal_editor_executed": False,
            "vehicle_physics_executed": False,
            "reason": "studio_vehicle_export_not_yet_VehicleAssetContract_v1_compatible"
            if missing_wheels
            else "real_unreal_editor_validation_unavailable",
        }
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
                "blender_execution": result.to_dict(),
                "reopen_execution": reopened.to_dict(),
                "visual_quality_certified": False,
            }
            current["events"].append(self._event("garage_assembly_verified", artifacts=len(files)))
            self._save(current)

    def artifact(self, project_id: str, digest: str) -> tuple[bytes, str]:
        receipt = self._read(project_id)
        for assembly in [receipt.get("assembly"), *receipt.get("assembly_history", [])]:
            for item in (assembly or {}).get("artifacts", []):
                if item["hash"] == digest:
                    return self.graphics.artifacts.get(digest), item["content_type"]
        raise LookupError("artifact_not_owned_by_game_studio_project")

    def improve_framing(self, project_id: str) -> dict[str, Any]:
        """One bounded scene rebuild, retaining the first native scene and artifacts."""
        with self._lock:
            receipt = self._read(project_id)
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
            if mission.state.value == "review" and receipt.get("assembly"):
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

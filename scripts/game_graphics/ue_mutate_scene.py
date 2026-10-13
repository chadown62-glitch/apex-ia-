"""Native qualification fault/repair: one graphics actor in a new copy of a private map."""

import hashlib
import json
import sys
from pathlib import Path

import unreal

PROJECT = Path(unreal.SystemLibrary.get_project_directory()).resolve()
sys.path.insert(0, str(PROJECT / "Tools"))
from ue_build_scene import load_job  # noqa: E402 - host verifies both private scripts


def mutate(job, plan):
    operation = job.get("operation")
    if operation not in ("inject_road_pose", "repair_road_pose") or plan.revision < 2:
        raise RuntimeError("unsupported_native_qualification_mutation")
    previous_map = f"/Game/APEXGraphics/{plan.scene_id}/R{plan.revision - 1:03d}/Scene"
    if job.get("parent_map") != previous_map:
        raise RuntimeError("unexpected_parent_map")
    parent_file = PROJECT / "Content" / (previous_map.removeprefix("/Game/") + ".umap")
    if hashlib.sha256(parent_file.read_bytes()).hexdigest() != job["parent_map_sha256"]:
        raise RuntimeError("parent_native_map_changed")
    target_file = PROJECT / "Content" / (plan.map_path.removeprefix("/Game/") + ".umap")
    if target_file.exists():
        raise RuntimeError("preserve_existing_native_revision")
    editor = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not editor.load_level(previous_map):
        raise RuntimeError("parent_native_map_load_failed")
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = {}
    for actor in subsystem.get_all_level_actors():
        tags = [str(t) for t in actor.tags]
        if "APEX_OWNER=engineer" in tags:
            raise RuntimeError("engineer_owned_actor_refused")
        ids = [t.removeprefix("APEX_ID=") for t in tags if t.startswith("APEX_ID=")]
        if not ids:
            continue
        if len(ids) != 1 or ids[0] in actors or "APEX_OWNER=graphics" not in tags:
            raise RuntimeError("unowned_or_duplicate_actor")
        actors[ids[0]] = actor
    if set(actors) != {spec.id for spec in plan.actors} or "road" not in actors:
        raise RuntimeError("unexpected_actor_set")
    road_spec = next(spec for spec in plan.actors if spec.id == "road")
    if road_spec.kind != "mesh":
        raise RuntimeError("road_must_be_graphic_mesh")
    road = actors["road"]
    before = road.get_actor_location()
    expected_before = list(road_spec.location_cm)
    if operation == "repair_road_pose":
        expected_before[0] += 150
    if max(abs(v - e) for v, e in zip((before.x, before.y, before.z), expected_before, strict=True)) > 0.2:
        raise RuntimeError("native_fault_precondition_mismatch")
    # The old map and project stay preserved; no rebuild substitutes for this mutation.
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if not unreal.EditorLoadingAndSavingUtils.save_map(world, plan.map_path):
        raise RuntimeError("native_save_as_failed")
    target = list(road_spec.location_cm)
    if operation == "inject_road_pose":
        target[0] += 150
    road.set_actor_location(unreal.Vector(*target), False, True)
    for actor in actors.values():
        actor.set_editor_property(
            "tags",
            [str(t) for t in actor.tags if not str(t).startswith("APEX_REV=")]
            + ["APEX_REV=" + str(plan.revision)],
        )
    if not editor.save_current_level():
        raise RuntimeError("native_mutation_save_failed")
    after = road.get_actor_location()
    return {
        "status": "created",
        "operation": operation,
        "nonce": job["nonce"],
        "plan_sha256": job["plan_sha256"],
        "scene_id": plan.scene_id,
        "revision": plan.revision,
        "map_path": plan.map_path,
        "parent_map": previous_map,
        "engine_version": unreal.SystemLibrary.get_engine_version(),
        "modified_actor": "road",
        "before_location_cm": [before.x, before.y, before.z],
        "after_location_cm": [after.x, after.y, after.z],
        "graphics_operations_only": True,
        "aesthetic_qualified": False,
    }


if __name__ == "__main__":
    try:
        job, plan = load_job("mutate")
        Path(job["report"]).write_text(json.dumps(mutate(job, plan), indent=2))
    except Exception as exc:
        if "job" in globals():
            Path(job["report"]).write_text(
                json.dumps(
                    {
                        "status": "error",
                        "nonce": job["nonce"],
                        "plan_sha256": job["plan_sha256"],
                        "engine_version": unreal.SystemLibrary.get_engine_version(),
                        "error": type(exc).__name__ + ": " + str(exc),
                    },
                    indent=2,
                )
            )
        raise
    finally:
        unreal.SystemLibrary.quit_editor()

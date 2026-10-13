"""Trusted UE editor artistic operations. Requires a real private project, never arbitrary model code."""

import hashlib
import json
import os
import sys
from pathlib import Path

import unreal

PROJECT = Path(unreal.SystemLibrary.get_project_directory()).resolve()
sys.path.insert(0, str(PROJECT / "Tools"))
from scene_contract import SHAPES, ScenePlan  # noqa: E402 - trusted private copy verified by host


def load_job(expected_mode="build"):
    normal = str(PROJECT).replace("\\", "/").lower().replace("//?/", "").rstrip("/")
    if any(normal == p or normal.startswith(p + "/") for p in ("c:/gameproject", "/mnt/c/gameproject")):
        raise RuntimeError("protected_personal_project")
    path = Path(os.environ["APEX_GRAPHICS_UE_JOB"]).resolve()
    if path.parent != PROJECT / "Requests":
        raise RuntimeError("job_outside_private_project")
    job = json.loads(path.read_text())
    marker = json.loads((PROJECT / ".apex-graphics-project.json").read_text())
    plan_path = Path(job["plan"]).resolve()
    if (
        job["nonce"] != marker["nonce"]
        or job["kind"] != "APEX-isolated-graphics-scene"
        or job["mode"] != expected_mode
    ):
        raise RuntimeError("invalid_graphics_project_marker")
    if (
        plan_path != PROJECT / "Requests/scene-plan.json"
        or hashlib.sha256(plan_path.read_bytes()).hexdigest() != marker["plan_sha256"]
    ):
        raise RuntimeError("plan_changed_after_validation")
    plan = ScenePlan.parse(json.loads(plan_path.read_text()))
    if (
        plan.scene_id != marker["scene_id"]
        or plan.revision != marker["revision"]
        or job["map_path"] != plan.map_path
    ):
        raise RuntimeError("scene_version_mismatch")
    if plan.protected_engineer_ids:
        raise RuntimeError("integrated_gameplay_level_requires_separate_adapter")
    if Path(job["report"]).resolve().parent != PROJECT.parent:
        raise RuntimeError("report_outside_private_workspace")
    return job, plan


def create_material(spec, folder):
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    material = tools.create_asset(
        "M_" + spec.id, folder + "/Materials", unreal.Material, unreal.MaterialFactoryNew()
    )
    if material is None:
        raise RuntimeError("material_version_already_exists")
    library = unreal.MaterialEditingLibrary
    color = library.create_material_expression(
        material, unreal.MaterialExpressionVectorParameter, -300, -80
    )
    color.set_editor_property("parameter_name", "APEX_BaseColor")
    color.set_editor_property("default_value", unreal.LinearColor(*spec.base_color, 1))
    library.connect_material_property(color, "", unreal.MaterialProperty.MP_BASE_COLOR)
    for name, value, prop in (
        ("APEX_Roughness", spec.roughness, unreal.MaterialProperty.MP_ROUGHNESS),
        ("APEX_Metallic", spec.metallic, unreal.MaterialProperty.MP_METALLIC),
    ):
        node = library.create_material_expression(
            material, unreal.MaterialExpressionScalarParameter, -300, 100
        )
        node.set_editor_property("parameter_name", name)
        node.set_editor_property("default_value", value)
        library.connect_material_property(node, "", prop)
    library.recompile_material(material)
    unreal.EditorAssetLibrary.set_metadata_tag(material, "APEX_MATERIAL_ID", spec.id)
    unreal.EditorAssetLibrary.save_loaded_asset(material)
    return material


def build(job, plan):
    editor = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not editor.new_level(plan.map_path):
        raise RuntimeError("level_creation_failed")
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for actor in subsystem.get_all_level_actors():
        if "APEX_OWNER=engineer" in [str(t) for t in actor.tags]:
            raise RuntimeError("unexpected_engineer_actor_refuse_modification")
    folder = plan.map_path.rsplit("/", 1)[0]
    materials = {spec.id: create_material(spec, folder) for spec in plan.materials}
    results = []
    for spec in plan.actors:
        location, rotation = unreal.Vector(*spec.location_cm), unreal.Rotator(*spec.rotation_deg)
        cls = {
            "mesh": unreal.StaticMeshActor,
            "directional_light": unreal.DirectionalLight,
            "point_light": unreal.PointLight,
        }[spec.kind]
        actor = subsystem.spawn_actor_from_class(cls, location, rotation)
        if actor is None:
            raise RuntimeError("actor_spawn_failed:" + spec.id)
        actor.set_actor_label("APEX_G_" + spec.id)
        actor.set_editor_property(
            "tags",
            [
                "APEX_OWNER=graphics",
                "APEX_ID=" + spec.id,
                "APEX_SCENE=" + plan.scene_id,
                "APEX_REV=" + str(plan.revision),
            ],
        )
        if spec.kind == "mesh":
            component = actor.get_component_by_class(unreal.StaticMeshComponent)
            mesh = unreal.load_asset(SHAPES[spec.shape])
            if mesh is None:
                raise RuntimeError("engine_basic_shape_missing")
            component.set_static_mesh(mesh)
            minimum, maximum = component.get_local_bounds()
            size = (maximum.x - minimum.x, maximum.y - minimum.y, maximum.z - minimum.z)
            if any(s <= 0 for s in size):
                raise RuntimeError("invalid_native_mesh_bounds")
            actor.set_actor_scale3d(
                unreal.Vector(*(d / s for d, s in zip(spec.dimensions_cm, size, strict=True)))
            )
            component.set_material(0, materials[spec.material_id])
            component.set_mobility(unreal.ComponentMobility.MOVABLE)
            component.set_collision_profile_name("BlockAll")
            component.set_simulate_physics(False)
        else:
            light = actor.get_component_by_class(unreal.LightComponent)
            light.set_intensity(spec.intensity)
            light.set_mobility(unreal.ComponentMobility.MOVABLE)
        results.append(
            {"id": spec.id, "path": actor.get_path_name(), "class": actor.get_class().get_name()}
        )
    if not editor.save_current_level():
        raise RuntimeError("level_save_failed")
    return {
        "status": "created",
        "nonce": job["nonce"],
        "plan_sha256": job["plan_sha256"],
        "scene_id": plan.scene_id,
        "revision": plan.revision,
        "map_path": plan.map_path,
        "engine_version": unreal.SystemLibrary.get_engine_version(),
        "actors": results,
        "graphics_operations_only": True,
        "aesthetic_qualified": False,
    }


if __name__ == "__main__":
    try:
        job, plan = load_job()
        Path(job["report"]).write_text(json.dumps(build(job, plan), indent=2))
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

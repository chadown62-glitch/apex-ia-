"""Fixed Blender worker: assemble three verified GLBs into one garage delivery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def material(name: str, color: tuple[float, float, float], roughness: float) -> bpy.types.Material:
    result = bpy.data.materials.new(name)
    result.diffuse_color = (*color, 1)
    result.use_nodes = True
    principled = result.node_tree.nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = (*color, 1)
    principled.inputs["Roughness"].default_value = roughness
    return result


def cube(name: str, location: tuple[float, float, float], scale: tuple[float, float, float], mat):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    bevel = obj.modifiers.new("Soft concrete edges", "BEVEL")
    bevel.width = 0.04
    bevel.segments = 2
    obj.modifiers.new("Weighted normals", "WEIGHTED_NORMAL")
    return obj


def import_asset(name: str, path: Path, offset: tuple[float, float, float]) -> int:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    imported = set(bpy.data.objects) - before
    meshes = [obj for obj in imported if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError("garage_import_empty:" + name)
    anchor = bpy.data.objects.new("asset_" + name, None)
    bpy.context.scene.collection.objects.link(anchor)
    anchor.location = offset
    for obj in imported:
        if obj.parent is None:
            obj.parent = anchor
        obj["apex_source_asset"] = name
    return len(meshes)


def lighting() -> None:
    world = bpy.data.worlds.new("Garage daylight")
    bpy.context.scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.58, 0.68, 0.82, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.5
    for name, location, energy, size in (
        ("Key daylight", (3, -5, 9), 1800, 7),
        ("Garage fill", (-5, 2, 7), 1100, 6),
        ("Street bounce", (5, 4, 5), 650, 5),
    ):
        lamp = bpy.data.lights.new(name, "AREA")
        lamp.energy = energy
        lamp.shape = "DISK"
        lamp.size = size
        obj = bpy.data.objects.new(name, lamp)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = location
        direction = Vector((0, 0, 0.6)) - obj.location
        obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def render_views(output: Path) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = False
    scene.render.resolution_x = 960
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "AgX"
    scene.camera = bpy.data.objects.new("Garage camera", bpy.data.cameras.new("Garage lens"))
    scene.collection.objects.link(scene.camera)
    camera = scene.camera
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 19
    target = Vector((0, 0, 2.5))
    for name, position in (
        ("front_three_quarter", (11, -14, 11)),
        ("rear_three_quarter", (-11, 13, 11)),
        ("side", (0, -17, 10)),
        ("top", (0, 0, 17)),
    ):
        camera.location = position
        camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(output / (name + ".png"))
        bpy.ops.render.render(write_still=True)


def build(args: argparse.Namespace) -> None:
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    counts = {
        "vehicle": import_asset("vehicle", args.vehicle, (0, -2.8, 0)),
        "building": import_asset("building", args.building, (0, 3.2, 0)),
        "furniture": import_asset("furniture", args.furniture, (3.3, -0.5, 0)),
    }
    asphalt = material("PBR asphalt", (0.075, 0.08, 0.09), 0.9)
    concrete = material("PBR concrete", (0.32, 0.33, 0.32), 0.85)
    paint = material("PBR road marking", (0.85, 0.78, 0.5), 0.65)
    cube("street", (0, -3.3, -0.09), (15, 7, 0.18), asphalt)
    cube("sidewalk", (0, 2.4, -0.11), (15, 4.3, 0.22), concrete)
    for x in (-5, 0, 5):
        cube("street_marking", (x, -5.5, 0.004), (2.5, 0.08, 0.01), paint)
    lighting()
    bpy.ops.object.select_all(action="DESELECT")
    for obj in bpy.data.objects:
        if obj.type == "MESH" or obj.name.startswith("asset_"):
            obj.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(output / "garage.glb"), export_format="GLB", use_selection=True)
    render_views(output)
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "garage.blend"))
    (output / "assembly.json").write_text(
        json.dumps(
            {
                "source_mesh_counts": counts,
                "native_blender_render_views": ["front_three_quarter", "rear_three_quarter", "side", "top"],
                "unreal_import_executed": False,
                "visual_quality_certified": False,
            },
            indent=2,
        )
    )


def verify(args: argparse.Namespace) -> None:
    bpy.ops.wm.open_mainfile(filepath=str(args.output / "garage.blend"))
    counts = {
        name: sum(obj.type == "MESH" and obj.get("apex_source_asset") == name for obj in bpy.data.objects)
        for name in ("vehicle", "building", "furniture")
    }
    if any(count <= 0 for count in counts.values()):
        raise RuntimeError("garage_source_collection_missing")
    named_lights = [
        name
        for name in ("Key daylight", "Garage fill", "Street bounce")
        if name in bpy.data.objects and bpy.data.objects[name].type == "LIGHT"
    ]
    if (
        bpy.context.scene.camera is None
        or len(named_lights) != 3
        or "street" not in bpy.data.objects
        or "sidewalk" not in bpy.data.objects
    ):
        raise RuntimeError("garage_camera_lighting_or_environment_missing")
    (args.output / "reopen-report.json").write_text(
        json.dumps(
            {
                "reopened_in_second_blender_process": True,
                "source_mesh_counts": counts,
                "camera": bpy.context.scene.camera.name,
                "named_lights": named_lights,
                "street_and_sidewalk_present": True,
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--vehicle", type=Path)
    parser.add_argument("--building", type=Path)
    parser.add_argument("--furniture", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None)
    if args.verify:
        verify(args)
    else:
        if not all((args.vehicle, args.building, args.furniture)):
            parser.error("three verified GLB inputs required")
        build(args)


if __name__ == "__main__":
    main()

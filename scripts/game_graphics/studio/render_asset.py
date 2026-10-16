"""Fixed, trusted Blender worker. The model supplies catalog JSON, never Python."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_ops import apply_operations, cube, finalize_meshes, hierarchy, material
from build_assets import add_component, build_building, build_chair, build_vehicle

VIEWS = ("front_three_quarter", "rear_three_quarter", "side", "front")


def aim(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def studio(family):
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 64
    scene.cycles.seed = 14
    scene.cycles.use_denoising = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.render.resolution_x, scene.render.resolution_y = 960, 720
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "AgX"
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.17, 0.19, 0.23, 1)
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = 0.4
    scale = {"vehicle": 5, "building": 7, "chair": 1.4}[family]
    center = Vector((0, 0, {"vehicle": 0.65, "building": 3.5, "chair": 0.60}[family]))
    ground = material("studio_ground", (0.07, 0.09, 0.11), roughness=0.82)
    cube("studio_floor", (0, 0, -0.13), (scale * 200, scale * 200, 0.2), ground, 0)
    for name, position, power, size in (
        ("key", (1, -1, 1.6), 170, 1),
        ("fill", (-1, -0.6, 0.9), 90, 0.9),
        ("rim", (0.6, 1.1, 1.3), 220, 0.8),
    ):
        bpy.ops.object.light_add(type="AREA", location=Vector(position) * scale)
        light = bpy.context.object
        light.name = "studio_" + name
        light.data.energy, light.data.shape, light.data.size = power * scale * scale, "DISK", size * scale
        aim(light, center)
    positions = {
        "front_three_quarter": (1.4, -1.8, 1.1),
        "rear_three_quarter": (-1.5, 1.8, 1.0),
        "side": (1, 0, 0.35),
        "front": (0, -1, 0.15),
        "rear": (0, 1, 0.15),
    }
    if family == "vehicle":
        positions = {
            "front_three_quarter": (1.7, -1.6, 0.95),
            "rear_three_quarter": (-1.8, 1.7, 0.9),
            "side": (0, -1, 0.18),
            "front": (1, 0, 0.15),
            "rear": (-1, 0, 0.15),
        }
    for name, position in positions.items():
        bpy.ops.object.camera_add(location=center + Vector(position) * scale * 2)
        cam = bpy.context.object
        cam.name = "camera_" + name
        cam.data.type = "ORTHO"
        cam.data.ortho_scale = {"vehicle": 6.8, "building": 13.0, "chair": 2.36}[family]
        aim(cam, center)
    # Orthographic level cameras remove perspective as a confounder of pair alignment.
    for view in ("front", "rear"):
        cam = bpy.data.objects["camera_" + view]
        cam.location.z = center.z
        aim(cam, center)
    return scene


def emission(name, color):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    node = nodes.new("ShaderNodeEmission")
    node.inputs["Color"].default_value = (*color, 1)
    mat.node_tree.links.new(node.outputs[0], output.inputs["Surface"])
    return mat


def diagnostic(output, family):
    """Native silhouettes and isolated component IDs, explicitly distinct from beauty."""
    scene = bpy.context.scene
    original = {o.name: (list(o.data.materials), o.hide_render) for o in scene.objects if o.type == "MESH"}
    black, white, red, green = [
        emission("diagnostic_" + n, c)
        for n, c in (("black", (0, 0, 0)), ("white", (1, 1, 1)), ("red", (1, 0, 0)), ("green", (0, 1, 0)))
    ]
    world_strength = scene.world.node_tree.nodes["Background"].inputs[1]
    old_strength = world_strength.default_value
    world_strength.default_value = 0
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.cycles.samples = 1
    for o in scene.objects:
        if o.type == "MESH":
            o.data.materials.clear()
            o.data.materials.append(black if o.name == "studio_floor" else white)
    for view in VIEWS:
        scene.camera = bpy.data.objects["camera_" + view]
        scene.render.filepath = str(output / ("silhouette_" + view + ".png"))
        bpy.ops.render.render(write_still=True)
    if family == "chair":
        # Other objects hidden: these ID renders measure alignment, not occlusion/beauty.
        for o in scene.objects:
            if o.type == "MESH":
                o.hide_render = o.name not in ("arm_left", "arm_right")
                o.data.materials.clear()
                o.data.materials.append(red if o.name == "arm_left" else green)
        for view in ("front", "rear"):
            scene.camera = bpy.data.objects["camera_" + view]
            scene.render.filepath = str(output / ("id_" + view + ".png"))
            bpy.ops.render.render(write_still=True)
    for name, (mats, hidden) in original.items():
        o = bpy.data.objects[name]
        o.data.materials.clear()
        for mat in mats:
            o.data.materials.append(mat)
        o.hide_render = hidden
    world_strength.default_value = old_strength
    scene.cycles.samples = 64
    scene.view_settings.view_transform = "AgX"


def calibrate(output):
    """Real Blender pairs, fixed offsets and fixed cameras, independent of the test chair."""
    from blender_ops import tube

    output.mkdir(parents=True, exist_ok=True)
    cases = [
        {"id": "aligned", "offset_m": 0.0, "expected_misaligned": False},
        {"id": "subthreshold", "offset_m": 0.004, "expected_misaligned": False},
        {"id": "raised", "offset_m": 0.03, "expected_misaligned": True},
        {"id": "lowered", "offset_m": -0.12, "expected_misaligned": True},
    ]
    (output / "design.json").write_text(
        json.dumps(
            {
                "cases": cases,
                "threshold_pixels": 4,
                "min_pixels": 12,
                "family": "calibration_parallel_curved_beams",
            },
            indent=2,
        )
    )
    for case in cases:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        mat = material("fixture", (0.2, 0.2, 0.2))
        for name, x, z in (("arm_left", -0.485, case["offset_m"]), ("arm_right", 0.485, 0)):
            tube(name, [(x, -0.36, 0.72 + z), (x, 0, 0.755 + z), (x, 0.46, 0.73 + z)], 0.03, mat)
        finalize_meshes()
        studio("chair")
        folder = output / case["id"]
        folder.mkdir()
        diagnostic(folder, "chair")
        bpy.ops.wm.save_as_mainfile(filepath=str(folder / "fixture.blend"))


def run(args):
    output = args.output.resolve()
    if (output / "asset.blend").exists():
        raise ValueError("immutable_revision_already_exists")
    output.mkdir(parents=True, exist_ok=True)
    if args.input:
        if hashlib.sha256(args.input.read_bytes()).hexdigest() != args.parent_sha:
            raise ValueError("parent_changed")
        bpy.ops.wm.open_mainfile(filepath=str(args.input), load_ui=False, use_scripts=False)
        if bpy.context.scene.get("apex_studio_family") != args.family:
            raise ValueError("family_mismatch")
    else:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        {"vehicle": build_vehicle, "building": build_building, "chair": build_chair}[args.family]()
        finalize_meshes()
        bpy.context.scene["apex_studio_family"] = args.family
        studio(args.family)
    if any(
        o.type == "MESH"
        and o.name != "studio_floor"
        and (o.get("apex_owner") != "graphics" or not o.get("apex_studio_part"))
        for o in bpy.context.scene.objects
    ):
        raise ValueError("scene_contains_unowned_meshes")
    before = hierarchy()
    plan = json.loads(args.plan.read_text()) if args.plan else None
    if plan and plan["family"] != args.family:
        raise ValueError("plan_family_mismatch")
    trace = apply_operations(plan, add_component) if plan else []
    if args.contract_wheels:
        if args.family != "vehicle" or not args.input:
            raise ValueError("vehicle_contract_repair_requires_existing_vehicle")
        slots = {
            "front_left": "wheel_front_left",
            "front_right": "wheel_front_right",
            "rear_left": "wheel_rear_left",
            "rear_right": "wheel_rear_right",
        }
        wheels = []
        for slot, name in slots.items():
            obj = bpy.data.objects.get("tyre_" + slot)
            if obj is None or obj.type != "MESH" or obj.parent is not None:
                raise ValueError("vehicle_wheel_source_missing:" + slot)
            obj.name = name
            obj.rotation_euler.x = -3.141592653589793 / 2
            obj["wheel_slot"] = name
            for component in list(bpy.data.objects):
                if component.type == "MESH" and component.name.startswith(
                    ("rim_" + slot, "hub_" + slot, "spoke_" + slot + "_")
                ):
                    world = component.matrix_world.copy()
                    component.parent = obj
                    component.matrix_world = world
            wheels.append(
                {
                    "object": name,
                    "center_m": list(obj.location),
                    "radius_m": 0.355,
                    "spin_axis_local": [0, 0, 1],
                    "spin_axis_blender_world": [0, 1, 0],
                }
            )
        bpy.context.view_layer.update()
        points = [
            obj.matrix_world @ Vector(corner)
            for obj in bpy.data.objects
            if obj.type == "MESH" and obj.name != "studio_floor"
            for corner in obj.bound_box
        ]
        lower = [min(v[i] for v in points) for i in range(3)]
        upper = [max(v[i] for v in points) for i in range(3)]
        paint = bpy.data.materials["petrol_paint"].node_tree.nodes["Principled BSDF"].inputs
        color = list(paint["Base Color"].default_value[:3])
        front = next(w for w in wheels if w["object"] == "wheel_front_left")
        rear = next(w for w in wheels if w["object"] == "wheel_rear_left")
        first_wheel = bpy.data.objects["wheel_front_left"]
        wheel_width = (
            max(v.co.z for v in first_wheel.data.vertices) - min(v.co.z for v in first_wheel.data.vertices)
        ) * first_wheel.scale.z
        (output / "vehicle-contract.json").write_text(
            json.dumps(
                {
                    "wheels": wheels,
                    "spec": {
                        "length_m": round(upper[0] - lower[0], 4),
                        "width_m": round(upper[1] - lower[1], 4),
                        "height_m": round(upper[2] - lower[2], 4),
                        "wheelbase_m": round(front["center_m"][0] - rear["center_m"][0], 4),
                        "wheel_radius_m": 0.355,
                        "wheel_width_m": round(wheel_width, 4),
                        "mass_kg": 1200,
                        "body_color": color,
                        "paint_metallic": float(paint["Metallic"].default_value),
                        "paint_roughness": float(paint["Roughness"].default_value),
                    },
                    "measurement_scope": (
                        "bounds, wheel centers and material from Blender; mass is a design estimate"
                    ),
                },
                indent=2,
            )
        )
    bpy.context.view_layer.update()
    (output / "hierarchy-before.json").write_text(json.dumps(before, indent=2))
    (output / "hierarchy.json").write_text(json.dumps(hierarchy(), indent=2))
    (output / "operation-trace.json").write_text(json.dumps(trace, indent=2))
    scene = bpy.context.scene
    bpy.ops.object.select_all(action="DESELECT")
    for o in scene.objects:
        if o.type == "MESH" and o.name != "studio_floor" and o.get("apex_studio_part"):
            o.select_set(True)
    bpy.ops.export_scene.gltf(
        filepath=str(output / "asset.glb"), export_format="GLB", use_selection=True,
        export_extras=args.contract_wheels,
    )
    settings = {
        "device": scene.cycles.device,
        "denoising": scene.cycles.use_denoising,
        "engine": scene.render.engine,
        "samples": scene.cycles.samples,
        "seed": scene.cycles.seed,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "cameras": {
            o.name: {"matrix": [list(row) for row in o.matrix_world], "scale": o.data.ortho_scale}
            for o in scene.objects
            if o.type == "CAMERA"
        },
        "lights": {
            o.name: {"matrix": [list(row) for row in o.matrix_world], "energy": o.data.energy}
            for o in scene.objects
            if o.type == "LIGHT"
        },
        "diagnostic_scope": "native isolated component IDs; no claim about scene occlusion",
    }
    (output / "render-settings.json").write_text(json.dumps(settings, indent=2))
    for view in VIEWS:
        scene.camera = bpy.data.objects["camera_" + view]
        scene.render.filepath = str(output / (view + ".png"))
        bpy.ops.render.render(write_still=True)
    diagnostic(output, args.family)
    scene.camera = bpy.data.objects["camera_front_three_quarter"]
    scene.render.filepath = "//front_three_quarter.png"
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "asset.blend"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration", action="store_true")
    parser.add_argument("--family", choices=("vehicle", "building", "chair"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--parent-sha")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--contract-wheels", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    if args.calibration:
        calibrate(args.output)
    else:
        run(args)

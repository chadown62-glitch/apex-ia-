"""Trusted procedural Blender builder. Inputs are validated parameters, never executable model text."""

import argparse
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector


def material(name, color, metallic=0, roughness=0.35, transmission=0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Transmission Weight"].default_value = transmission
    bsdf.inputs["Coat Weight"].default_value = 0.5 if metallic else 0
    return mat


def finish(obj, name, mat, closed=True):
    obj.name = name
    obj["require_closed"] = closed
    obj.data.materials.append(mat)
    for p in obj.data.polygons:
        p.use_smooth = True
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if closed and bm.calc_volume(signed=True) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    bm.to_mesh(obj.data)
    bm.free()
    return obj


def cube(name, location, dimensions, mat, bevel=0.04):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        mod = obj.modifiers.new("edge_radius", "BEVEL")
        mod.width, mod.segments = bevel, 3
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return finish(obj, name, mat)


def loft(name, sections, mat):
    vertices, faces = [], []
    for x, width, lower, upper in sections:
        profile = (
            (-width * 0.8, lower),
            (-width, lower + (upper - lower) * 0.20),
            (-width, upper - (upper - lower) * 0.20),
            (-width * 0.78, upper),
            (width * 0.78, upper),
            (width, upper - (upper - lower) * 0.20),
            (width, lower + (upper - lower) * 0.20),
            (width * 0.8, lower),
        )
        vertices.extend((x, y, z) for y, z in profile)
    n = 8
    faces.append(tuple(range(n - 1, -1, -1)))
    for ring in range(len(sections) - 1):
        for i in range(n):
            faces.append(
                (ring * n + i, ring * n + (i + 1) % n, (ring + 1) * n + (i + 1) % n, (ring + 1) * n + i)
            )
    faces.append(tuple((len(sections) - 1) * n + i for i in range(n)))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return finish(obj, name, mat)


def parent_preserving(obj, parent):
    obj.parent = parent
    obj.matrix_parent_inverse = parent.matrix_world.inverted()


def make_car(spec, output, defect):
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    length, w, h, wb, r, tw = (
        spec[k]
        for k in ("length_m", "width_m", "height_m", "wheelbase_m", "wheel_radius_m", "wheel_width_m")
    )
    paint = material("paint", spec["body_color"], spec["paint_metallic"], spec["paint_roughness"])
    rubber = material("rubber", (0.018, 0.018, 0.019), 0, 0.7)
    alloy = material("alloy", (0.4, 0.43, 0.47), 0.85, 0.19)
    glass = material("glass", (0.015, 0.035, 0.055), 0.08, 0.12, 0.35)
    black = material("trim", (0.008, 0.01, 0.012), 0.2, 0.35)
    lamps = material("headlights", (0.83, 0.87, 1), 0.15, 0.12)
    rear = material("taillights", (0.55, 0.008, 0.004), 0.1, 0.15)
    sections = []
    for i in range(17):
        t = i / 16
        x = length * (t - 0.5)
        width = w / 2 * 0.91 * (0.80 + 0.20 * math.sin(math.pi * t))
        upper = h * (0.55 + 0.09 * math.sin(math.pi * t))
        sections.append((x, width, 0.20, upper))
    body = loft("chassis", sections, paint)
    bevel = body.modifiers.new("body_curves", "BEVEL")
    bevel.width, bevel.segments = 0.055, 3
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    wheels = []
    for front, side, name in (
        (1, -1, "wheel_front_left"),
        (1, 1, "wheel_front_right"),
        (-1, -1, "wheel_rear_left"),
        (-1, 1, "wheel_rear_right"),
    ):
        center = (front * wb / 2, side * (w / 2 - tw / 2), r)
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=48,
            radius=r * 1.14,
            depth=tw * 2.5,
            location=(center[0], side * w / 2, center[2]),
            rotation=(math.pi / 2, 0, 0),
        )
        cutter = bpy.context.object
        bpy.context.view_layer.objects.active = body
        mod = body.modifiers.new("wheel_arch", "BOOLEAN")
        mod.operation, mod.solver, mod.object = "DIFFERENCE", "EXACT", cutter
        bpy.ops.object.modifier_apply(modifier=mod.name)
        bpy.data.objects.remove(cutter, do_unlink=True)
        bpy.ops.mesh.primitive_torus_add(
            major_segments=48,
            minor_segments=16,
            major_radius=r * 0.8,
            minor_radius=r * 0.2,
            location=center,
            rotation=(-math.pi / 2, 0, 0),
        )
        wheel = finish(bpy.context.object, name, rubber)
        wheel.scale.z = tw / (r * 0.4)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        wheel["wheel_slot"] = name
        wheels.append(wheel)
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=48, radius=r * 0.70, depth=tw * 0.7, location=center, rotation=(-math.pi / 2, 0, 0)
        )
        rim = finish(bpy.context.object, name + "_rim", alloy)
        parent_preserving(rim, wheel)
        for spoke in range(5):
            angle = 2 * math.pi * spoke / 5
            loc = (
                center[0] + math.cos(angle) * r * 0.35,
                center[1] + side * tw * 0.39,
                center[2] + math.sin(angle) * r * 0.35,
            )
            bar = cube(name + f"_spoke_{spoke}", loc, (r * 0.58, 0.035, 0.045), alloy, 0.012)
            bar.rotation_euler.y = -angle
            parent_preserving(bar, wheel)
    loft(
        "cabin_glass",
        [
            (-length * 0.24, w * 0.33, h * 0.62, h * 0.7),
            (-length * 0.13, w * 0.29, h * 0.65, h),
            (length * 0.045, w * 0.29, h * 0.65, h),
            (length * 0.24, w * 0.34, h * 0.61, h * 0.7),
        ],
        glass,
    )
    cube("roof", (-length * 0.035, 0, h - 0.008), (length * 0.20, w * 0.60, 0.024), paint, 0.012)
    cube("front_grille", (length * 0.499, 0, 0.43), (0.022, w * 0.47, 0.19), black, 0.01)
    for side in (-1, 1):
        cube(
            f"front_lamp_{side}",
            (length * 0.47, side * w * 0.31, h * 0.56),
            (0.12, w * 0.25, 0.075),
            lamps,
            0.025,
        )
        cube(
            f"rear_lamp_{side}",
            (-length * 0.475, side * w * 0.31, h * 0.58),
            (0.1, w * 0.26, 0.06),
            rear,
            0.022,
        )
        cube(
            f"side_sill_{side}",
            (-length * 0.01, side * w * 0.45, 0.25),
            (wb * 0.69, 0.05, 0.1),
            black,
            0.025,
        )
    if defect == "pivot":
        # Actual fault fixture: shift the mesh in its local frame while retaining the object pivot.
        for v in wheels[0].data.vertices:
            v.co.x += 0.12
    elif defect == "degenerate":
        mesh = bpy.data.meshes.new("broken_geometry")
        mesh.from_pydata([(0, 0, 0), (0.1, 0, 0), (0.2, 0, 0)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new("broken_geometry", mesh)
        bpy.context.collection.objects.link(obj)
        obj.data.materials.append(paint)
    car_parts = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    for obj in car_parts:
        # Explicit BEAUTY triangulation avoids the GLB exporter's T junctions
        # on boolean wheel-arch n-gons (independently reimported and measured).
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method="BEAUTY", ngon_method="BEAUTY")
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(obj.data)
        bm.free()
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(island_margin=0.02)
        bpy.ops.object.mode_set(mode="OBJECT")
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1
    scene["apex_vehicle_spec"] = json.dumps(spec)
    for obj in car_parts:
        obj["apex_vehicle_part"] = True
    export_car(car_parts, output)
    floor = material("studio_floor", (0.075, 0.085, 0.10), 0.15, 0.42)
    cube("studio_floor", (0, 0, -0.07), (20, 20, 0.10), floor, 0.02)
    world = bpy.data.worlds.new("studio")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.17, 0.20, 0.27, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.5
    scene.world = world
    for idx, (loc, energy, size) in enumerate(
        (((2, -3, 6), 1600, 5), ((-3, 2, 5), 2000, 4), ((4, 3, 3), 1000, 3))
    ):
        data = bpy.data.lights.new(f"softbox{idx}", "AREA")
        data.energy, data.shape, data.size = energy, "DISK", size
        obj = bpy.data.objects.new(data.name, data)
        scene.collection.objects.link(obj)
        obj.location = loc
        obj.rotation_euler = (Vector((0, 0, 0.5)) - obj.location).to_track_quat("-Z", "Y").to_euler()
    data = bpy.data.cameras.new("camera")
    camera = bpy.data.objects.new("camera", data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    data.type, data.ortho_scale = "ORTHO", 6.6
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 32
    scene.cycles.use_denoising = False  # This cloud Blender build lacks OpenImageDenoise.
    scene.render.threads_mode, scene.render.threads = "FIXED", 4
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 640, 420, 100
    scene.render.image_settings.file_format = "PNG"
    render_views(scene, camera, output)
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "vehicle.blend"))
    print(
        "APEX_BUILD="
        + json.dumps(
            {
                "blender": bpy.app.version_string,
                "method": "procedural_blender",
                "mesh_parts": len(car_parts),
                "device": "CPU",
            }
        )
    )


def export_car(car_parts, output):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in car_parts:
        obj.select_set(True)
    bpy.ops.export_scene.gltf(
        filepath=str(output / "vehicle.glb"),
        export_format="GLB",
        use_selection=True,
        export_yup=True,
        export_cameras=False,
        export_lights=False,
    )
    copies = []
    for obj in car_parts:
        clone = obj.copy()
        clone.data = obj.data.copy()
        clone.parent = None
        clone.matrix_world = obj.matrix_world.copy()
        clone.name = obj.name + "_LOD1"
        bpy.context.collection.objects.link(clone)
        copies.append(clone)
        bpy.context.view_layer.objects.active = clone
        # Preserve each wheel's symmetric bounds and its precise center.
        if obj.name not in ("wheel_front_left", "wheel_front_right", "wheel_rear_left", "wheel_rear_right"):
            modifier = clone.modifiers.new("lod_decimation", "DECIMATE")
            modifier.ratio = 0.5
            bpy.ops.object.modifier_apply(modifier=modifier.name)
    bpy.ops.object.select_all(action="DESELECT")
    for clone in copies:
        clone.select_set(True)
    bpy.ops.export_scene.gltf(
        filepath=str(output / "vehicle-lod1.glb"),
        export_format="GLB",
        use_selection=True,
        export_yup=True,
        export_cameras=False,
        export_lights=False,
    )
    for clone in copies:
        bpy.data.objects.remove(clone, do_unlink=True)


def render_views(scene, camera, output):
    views = {
        "front_three_quarter": (7, -8, 4.5),
        "rear_three_quarter": (-7, 8, 4),
        "side": (0, -10, 2.8),
        "front": (9, 0, 2.5),
    }
    for name, location in views.items():
        camera.location = location
        camera.rotation_euler = (Vector((0, 0, 0.65)) - camera.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(output / (name + ".png"))
        bpy.ops.render.render(write_still=True)


def repair_pivots(source, output):
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    fixed = []
    for obj in bpy.context.scene.objects:
        if obj.name not in ("wheel_front_left", "wheel_front_right", "wheel_rear_left", "wheel_rear_right"):
            continue
        center = (
            Vector(tuple(min(v.co[i] for v in obj.data.vertices) for i in range(3)))
            + Vector(tuple(max(v.co[i] for v in obj.data.vertices) for i in range(3)))
        ) / 2
        if center.length > 0.00001:
            for vertex in obj.data.vertices:
                vertex.co -= center
            fixed.append(obj.name)
    parts = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.name != "studio_floor"]
    export_car(parts, output)
    render_views(bpy.context.scene, bpy.context.scene.camera, output)
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "vehicle.blend"))
    print("APEX_CORRECTION=" + json.dumps({"method": "local_mesh_recentering", "objects": fixed}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--defect", choices=("none", "pivot", "degenerate"), default="none")
    parser.add_argument("--repair-pivots", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.output.mkdir(parents=True, exist_ok=True)
    if args.repair_pivots:
        repair_pivots(args.repair_pivots, args.output)
    else:
        make_car(json.loads(args.spec.read_text()), args.output, args.defect)

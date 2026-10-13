"""Original APEX lounge chair. Trusted procedural Blender geometry, no neural mesh.

Integration dependencies: bpy, bmesh, mathutils, stdlib math plus PR12
material/cube/finish helpers. build_chair() only adds asset objects and materials;
it does not clear the scene, create lights, render or write files.
"""

import importlib.util
import json
import math
import sys
import time
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

_helpers = importlib.util.spec_from_file_location(
    "apex_vehicle_helpers", "/workspace/apex-game-graphics/scripts/game_graphics/build_vehicle.py"
)
_module = importlib.util.module_from_spec(_helpers)
_helpers.loader.exec_module(_module)
material, cube, finish = _module.material, _module.cube, _module.finish


def chair_mesh(name, vertices, faces, mat):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return finish(obj, name, mat)


def chair_beam(name, start, end, radius, mat, radius_end=None, vertices=20):
    delta = Vector(end) - Vector(start)
    bpy.ops.mesh.primitive_cone_add(
        vertices=vertices, radius1=radius, radius2=radius if radius_end is None else radius_end,
        depth=delta.length, location=(Vector(start) + Vector(end)) / 2,
    )
    obj = bpy.context.object
    obj.rotation_euler = delta.to_track_quat("Z", "Y").to_euler()
    bevel = obj.modifiers.new("turned_edges", "BEVEL")
    bevel.width, bevel.segments = min(radius * 0.22, 0.004), 2
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    return finish(obj, name, mat)


def chair_tube(name, points, radius, mat, closed=False, sides=8):
    """Parallel local frames; capped pipe, with deterministic actual mesh topology."""
    vertices, faces = [], []
    for i, point in enumerate(points):
        previous = Vector(points[(i - 1) % len(points)] if closed or i else points[0])
        following = Vector(points[(i + 1) % len(points)] if closed or i < len(points) - 1 else points[-1])
        tangent = (following - previous).normalized()
        auxiliary = Vector((0, 0, 1)) if abs(tangent.z) < 0.9 else Vector((0, 1, 0))
        a = tangent.cross(auxiliary).normalized()
        b = tangent.cross(a).normalized()
        for j in range(sides):
            angle = 2 * math.pi * j / sides
            vertices.append(Vector(point) + radius * (math.cos(angle) * a + math.sin(angle) * b))
    for i in range(len(points) if closed else len(points) - 1):
        nxt = (i + 1) % len(points)
        for j in range(sides):
            faces.append((i * sides + j, i * sides + (j + 1) % sides,
                          nxt * sides + (j + 1) % sides, nxt * sides + j))
    if not closed:
        faces.extend([tuple(reversed(range(sides))),
                      tuple((len(points) - 1) * sides + j for j in range(sides))])
    return chair_mesh(name, vertices, faces, mat)


def chair_pillow(name, dimensions, location, mat, exponent=0.34):
    """Rounded superellipsoid pillow, no intersecting stacked cubes or open skins."""
    a, b, c = (value / 2 for value in dimensions)
    rings, count = 12, 48
    vertices = [(0, 0, -c)]
    for i in range(1, rings):
        latitude = -math.pi / 2 + math.pi * i / rings
        radius = abs(math.cos(latitude)) ** exponent
        height = c * math.copysign(abs(math.sin(latitude)) ** 0.60, math.sin(latitude))
        for j in range(count):
            theta = 2 * math.pi * j / count
            vertices.append((a * radius * math.copysign(abs(math.cos(theta)) ** exponent, math.cos(theta)),
                             b * radius * math.copysign(abs(math.sin(theta)) ** exponent, math.sin(theta)), height))
    vertices.append((0, 0, c))
    faces = [(0, 1 + (j + 1) % count, 1 + j) for j in range(count)]
    for i in range(rings - 2):
        for j in range(count):
            faces.append((1 + i * count + j, 1 + i * count + (j + 1) % count,
                          1 + (i + 1) * count + (j + 1) % count, 1 + (i + 1) * count + j))
    faces.extend((len(vertices) - 1, 1 + (rings - 2) * count + j,
                  1 + (rings - 2) * count + (j + 1) % count) for j in range(count))
    obj = chair_mesh(name, vertices, faces, mat)
    obj.location = location
    return obj


def chair_shell(name, mat, *, seat=False):
    columns, rows = 24, 16
    vertices, faces = [], []
    thickness = 0.036
    for back in (0, 1):
        for j in range(rows + 1):
            t = j / rows
            width = 0.43 - 0.03 * (abs(t - 0.5) * 2) ** 8
            for i in range(columns + 1):
                u = 2 * i / columns - 1
                if seat:
                    vertex = (u * width, -0.45 + 0.77 * t,
                              0.40 - 0.065 * t + 0.075 * u * u - back * thickness)
                else:
                    vertex = (u * width, 0.27 + 0.32 * t - 0.09 * u * u + back * thickness,
                              0.40 + 0.79 * t + 0.012 * u * u)
                vertices.append(vertex)
    layer = (columns + 1) * (rows + 1)
    for side in (0, 1):
        for j in range(rows):
            for i in range(columns):
                k = side * layer + j * (columns + 1) + i
                faces.append((k, k + 1, k + columns + 2, k + columns + 1))
    rim = list(range(columns + 1))
    rim += [j * (columns + 1) + columns for j in range(1, rows + 1)]
    rim += [rows * (columns + 1) + i for i in range(columns - 1, -1, -1)]
    rim += [j * (columns + 1) for j in range(rows - 1, 0, -1)]
    faces.extend((rim[i], rim[(i + 1) % len(rim)], rim[(i + 1) % len(rim)] + layer,
                  rim[i] + layer) for i in range(len(rim)))
    obj = chair_mesh(name, vertices, faces, mat)
    bevel = obj.modifiers.new("laminated_edge_radius", "BEVEL")
    bevel.width, bevel.segments = 0.006, 3
    bevel.limit_method = "ANGLE"
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    return finish(obj, name, mat) if not obj.data.materials else obj


def chair_grain_material():
    mat = material("chair_walnut", (0.16, 0.054, 0.023), roughness=0.33)
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Coat Weight"].default_value = 0.23
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    coordinates = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeVectorMath")
    mapping.operation = "MULTIPLY"
    mapping.inputs[1].default_value = (38, 3, 2)
    links.new(coordinates.outputs["Generated"], mapping.inputs[0])
    grain = nodes.new("ShaderNodeTexNoise")
    grain.inputs["Scale"].default_value = 2.8
    grain.inputs["Detail"].default_value = 3
    grain.inputs["Roughness"].default_value = 0.7
    links.new(mapping.outputs["Vector"], grain.inputs["Vector"])
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.16
    ramp.color_ramp.elements[0].color = (0.032, 0.009, 0.003, 1)
    ramp.color_ramp.elements[1].position = 0.84
    ramp.color_ramp.elements[1].color = (0.24, 0.10, 0.031, 1)
    links.new(grain.outputs["Fac"], ramp.inputs[0])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.10
    bump.inputs["Distance"].default_value = 0.0006
    links.new(grain.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def build_chair():
    """Add a complete lounge chair facing -Y; return its actual mesh objects."""
    before = set(bpy.context.scene.objects)
    walnut = chair_grain_material()
    leather = material("chair_saddle_leather", (0.21, 0.066, 0.027), roughness=0.56)
    bsdf = leather.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Coat Weight"].default_value = 0.05
    noise = leather.node_tree.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 185
    noise.inputs["Detail"].default_value = 2
    bump = leather.node_tree.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.16
    bump.inputs["Distance"].default_value = 0.00055
    leather.node_tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    leather.node_tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    piping = material("chair_stitched_edges", (0.095, 0.028, 0.012), roughness=0.57)
    brass = material("chair_brushed_brass", (0.43, 0.255, 0.085), metallic=0.83, roughness=0.28)
    feet = material("chair_floor_glides", (0.012, 0.012, 0.011), roughness=0.65)
    chair_shell("timber_back", walnut)
    chair_shell("timber_seat", walnut, seat=True)
    seat = chair_pillow("seat_cushion", (0.80, 0.76, 0.17), (0, -0.065, 0.476), leather)
    seat.rotation_euler.x = math.radians(-5)
    back = chair_pillow("back_cushion", (0.765, 0.715, 0.155), (0, 0.37, 0.835), leather)
    back.rotation_euler.x = math.radians(68)
    # Cushions are independent components; the piping follows their local perimeter.
    for obj, dims in ((seat, (0.80, 0.76, 0.17)), (back, (0.765, 0.715, 0.155))):
        points = []
        a, b, c = (n / 2 for n in dims)
        for j in range(96):
            theta = 2 * math.pi * j / 96
            points.append((0.996 * a * math.copysign(abs(math.cos(theta)) ** 0.34, math.cos(theta)),
                           0.996 * b * math.copysign(abs(math.sin(theta)) ** 0.34, math.sin(theta)), c * 0.43))
        edge = chair_tube(obj.name + "_piping", points, 0.0034, piping, closed=True)
        edge.location, edge.rotation_euler = obj.location.copy(), obj.rotation_euler.copy()
    for side, label in ((-1, "left"), (1, "right")):
        points = []
        for j in range(25):
            t = j / 24
            points.append((side * (0.485 + 0.013 * math.sin(math.pi * t)),
                           -0.36 + 0.82 * t, 0.718 + 0.035 * math.sin(math.pi * t) + 0.01 * t))
        # A vertically flattened rounded beam makes a usable broad open armrest.
        arm = chair_tube("arm_" + label, points, 0.046, walnut, sides=16)
        for vertex in arm.data.vertices:
            vertex.co.z = 0.735 + (vertex.co.z - 0.735) * 0.56
        for fore, y in (("front", -0.30), ("rear", 0.28)):
            chair_beam("arm_support_" + fore + "_" + label,
                       (side * 0.40, y, 0.40), (side * 0.485, y, 0.719), 0.013, brass)
            chair_beam("arm_joint_" + fore + "_" + label,
                       (side * 0.467, y, 0.705), (side * 0.467, y, 0.733), 0.023, brass)
        for fore, y in (("front", -0.30), ("rear", 0.30)):
            top = (side * 0.335, y, 0.405)
            bottom = (side * 0.465, y * 1.42, 0.046)
            chair_beam("leg_" + fore + "_" + label, bottom, top, 0.022, walnut, radius_end=0.038)
            delta = (Vector(top) - Vector(bottom)).normalized()
            chair_beam("leg_collar_" + fore + "_" + label, Vector(top) - delta * 0.042,
                       Vector(top) + delta * 0.012, 0.039, brass)
            chair_beam("foot_" + fore + "_" + label, (bottom[0], bottom[1], 0.008),
                       (bottom[0], bottom[1], 0.045), 0.024, feet)
        chair_beam("underframe_" + label, (side * 0.335, -0.325, 0.377),
                   (side * 0.335, 0.355, 0.377), 0.028, walnut)
    for y in (-0.27, 0.28):
        chair_beam("cross_brace_" + str(y), (-0.375, y, 0.356), (0.375, y, 0.356), 0.023, walnut)
    parts = [obj for obj in bpy.context.scene.objects if obj not in before and obj.type == "MESH"]
    for obj in parts:
        obj["apex_asset_part"] = True
        obj["apex_component_id"] = obj.name
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        # Repair orientation after bevel/application and provide finite UVs in the saved asset.
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        if bm.calc_volume(signed=True) < 0:
            bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
        bm.to_mesh(obj.data)
        bm.free()
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(island_margin=0.015)
        bpy.ops.object.mode_set(mode="OBJECT")
    return parts


def run_candidate(output):
    began = time.monotonic()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    parts = build_chair()
    geometry_s = time.monotonic() - began
    measurements, corners = {}, []
    for obj in parts:
        obj.data.calc_loop_triangles()
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        measurements[obj.name] = {"triangles": len(obj.data.loop_triangles),
            "nonmanifold": sum(not e.is_manifold for e in bm.edges),
            "signed_volume": bm.calc_volume(signed=True), "uv_layers": len(obj.data.uv_layers)}
        bm.free()
        corners.extend(obj.matrix_world @ Vector(c) for c in obj.bound_box)
    bounds = [max(c[i] for c in corners) - min(c[i] for c in corners) for i in range(3)]
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1
    scene["method"] = "original_authorized_procedural_blender"
    scene["front_axis"] = "-Y"
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for obj in parts:
        obj.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(output / "chair.glb"), export_format="GLB",
                             use_selection=True, export_cameras=False, export_lights=False)
    floor = cube("studio_floor", (0, 0, -0.038), (200, 200, 0.075),
                 material("chair_studio_ground", (0.17, 0.19, 0.19), roughness=0.65), bevel=0)
    world = bpy.data.worlds.new("chair_studio")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.48, 0.57, 0.68, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.35
    scene.world = world
    for i, (location, energy, size) in enumerate((((-3, -4, 5), 650, 3), ((3, -1, 3), 300, 2.5), ((1, 3, 4), 800, 2.8))):
        light = bpy.data.lights.new("chair_softbox_" + str(i), "AREA")
        light.energy, light.shape, light.size = energy, "DISK", size
        obj = bpy.data.objects.new(light.name, light)
        scene.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = (Vector((0, 0, 0.6)) - obj.location).to_track_quat("-Z", "Y").to_euler()
    camera_data = bpy.data.cameras.new("chair_camera")
    camera = bpy.data.objects.new("chair_camera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (2.4, -3.2, 2.1)
    camera.rotation_euler = (Vector((0, 0.03, 0.59)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.type, camera_data.ortho_scale = "ORTHO", 2.36
    scene.camera = camera
    scene.render.engine = "CYCLES"
    scene.cycles.device, scene.cycles.samples, scene.cycles.seed = "CPU", 64, 14
    scene.cycles.use_denoising = False
    scene.render.threads_mode, scene.render.threads = "FIXED", 4
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 960, 720, 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "AgX"
    scene.render.filepath = str(output / "chair-beauty.png")
    render_start = time.monotonic()
    bpy.ops.render.render(write_still=True)
    beauty_s = time.monotonic() - render_start
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "chair.blend"))
    floor.hide_render = True
    scene.render.film_transparent = True
    scene.render.resolution_x = scene.render.resolution_y = 512
    camera_data.ortho_scale = 1.80
    scene.render.filepath = str(output / "chair-reference-rgba.png")
    render_start = time.monotonic()
    bpy.ops.render.render(write_still=True)
    reference_s = time.monotonic() - render_start
    summary = {"method": "original_authorized_procedural_blender", "neural_mesh": False,
               "blender": bpy.app.version_string, "geometry_s": geometry_s, "beauty_s": beauty_s,
               "reference_s": reference_s, "elapsed_s": time.monotonic() - began,
               "bounds_m": bounds, "triangles": sum(row["triangles"] for row in measurements.values()),
               "objects": measurements, "render": {"device": "CPU", "threads": 4, "samples": 64, "seed": 14,
               "denoise": False, "beauty_resolution": [960, 720], "reference_resolution": [512, 512]}}
    (output / "candidate-report.json").write_text(json.dumps(summary, indent=2))
    print("APEX_CHAIR=" + json.dumps(summary))


if __name__ == "__main__":
    run_candidate(Path(__file__).resolve().parent)

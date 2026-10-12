"""Independent inspection of saved/reimported files; never trust builder's geometry report."""

import argparse
import json
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector


def inspect(path):
    if path.suffix == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
    else:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        bpy.ops.import_scene.gltf(filepath=str(path))
    objects, corners = {}, []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH" or obj.name == "studio_floor":
            continue
        mesh = obj.data
        mesh.calc_loop_triangles()
        world = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        corners.extend(world)
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6)
        bm.normal_update()
        degenerate = sum(f.calc_area() < 1e-10 for f in bm.faces)
        boundary = sum(not edge.is_manifold for edge in bm.edges)
        normals_bad = sum(f.normal.length < 0.99 for f in bm.faces)
        volume = bm.calc_volume(signed=True)
        bm.free()
        minimum = [min(c[i] for c in world) for i in range(3)]
        maximum = [max(c[i] for c in world) for i in range(3)]
        center = [(a + b) / 2 for a, b in zip(minimum, maximum, strict=True)]
        pivot = list(obj.matrix_world.translation)
        axis = obj.matrix_world.to_3x3() @ Vector((0, 0, 1))
        objects[obj.name] = {
            "vertices": len(mesh.vertices),
            "triangles": len(mesh.loop_triangles),
            "degenerate_faces": degenerate,
            "nonmanifold_edges": boundary,
            "invalid_normals": normals_bad,
            "signed_volume": volume,
            "dimensions": [b - a for a, b in zip(minimum, maximum, strict=True)],
            "geometry_center": center,
            "pivot": pivot,
            "spin_axis_world": list(axis.normalized()),
            "materials": [m.name for m in mesh.materials if m],
            "uv_layers": len(mesh.uv_layers),
            "uv_finite": all(abs(v) < 1e6 for layer in mesh.uv_layers for uv in layer.data for v in uv.uv),
            "parent": obj.parent.name if obj.parent else None,
        }
    dimensions = (
        [max(c[i] for c in corners) - min(c[i] for c in corners) for i in range(3)] if corners else []
    )
    return {
        "blender": bpy.app.version_string,
        "source_format": path.suffix,
        "objects": objects,
        "dimensions": dimensions,
        "triangles": sum(o["triangles"] for o in objects.values()),
        "scene_unit_scale": bpy.context.scene.unit_settings.scale_length,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    report = inspect(args.input)
    args.report.write_text(json.dumps(report, indent=2))
    print(
        "APEX_INSPECTION="
        + json.dumps({"objects": len(report["objects"]), "triangles": report["triangles"]})
    )

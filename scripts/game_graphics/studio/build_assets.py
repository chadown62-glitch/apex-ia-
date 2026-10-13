"""Original detailed family assemblers; PR12 primitives remain the shared implementation."""

import math

import bmesh
import bpy
from blender_ops import cube, cylinder, ellipsoid, finish, loft, material, torus, tube
from mathutils import Vector


def build_vehicle():
    paint = material("petrol_paint", (0.018, 0.105, 0.145), 0.78, 0.24)
    black = material("black_trim", (0.012, 0.016, 0.019), 0.3, 0.34)
    rubber = material("tyre_rubber", (0.015, 0.017, 0.02), 0, 0.75)
    alloy = material("satin_alloy", (0.46, 0.51, 0.55), 0.92, 0.24)
    brake = material("brake_steel", (0.19, 0.22, 0.25), 0.8, 0.4)
    glass = material("smoked_glass", (0.009, 0.022, 0.03), 0.08, 0.20, 0.18)
    lamp = material("lamp_lens", (0.7, 0.83, 1), 0.2, 0.15)
    red = material("rear_lens", (0.45, 0.008, 0.012), 0.3, 0.22)
    leather = material("cockpit_leather", (0.08, 0.045, 0.025), 0, 0.54)
    # Continuous rounded profiles keep real fender bridges above the wheel cutouts.
    vertices = []
    faces = []
    count = 32
    for i in range(49):
        t = i / 48
        x = 4.8 * (t - 0.5)
        width = 0.87 + 0.055 * math.sin(math.pi * t) + 0.115 * math.exp(-(((abs(x) - 1.4) / 0.45) ** 2))
        upper = 0.78 + 0.07 * math.sin(math.pi * t) + 0.13 * math.exp(-(((abs(x) - 1.4) / 0.50) ** 2))
        lower = 0.19
        mid = (upper + lower) / 2
        half = (upper - lower) / 2
        for j in range(count):
            theta = 2 * math.pi * j / count
            vertices.append(
                (
                    x,
                    width * math.copysign(abs(math.sin(theta)) ** 0.40, math.sin(theta)),
                    mid + half * math.copysign(abs(math.cos(theta)) ** 0.55, math.cos(theta)),
                )
            )
    for i in range(48):
        for j in range(count):
            faces.append(
                (
                    i * count + j,
                    i * count + (j + 1) % count,
                    (i + 1) * count + (j + 1) % count,
                    (i + 1) * count + j,
                )
            )
    faces.extend([tuple(reversed(range(count))), tuple(48 * count + j for j in range(count))])
    mesh = bpy.data.meshes.new("curved_chassis")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    body = bpy.data.objects.new("chassis", mesh)
    bpy.context.collection.objects.link(body)
    finish(body, "chassis", paint)
    for front, x in (("front", 1.4), ("rear", -1.4)):
        for side, y in (("left", -0.90), ("right", 0.90)):
            z = 0.355
            bpy.ops.mesh.primitive_cylinder_add(
                vertices=64, radius=0.405, depth=0.8, location=(x, y, z), rotation=(math.pi / 2, 0, 0)
            )
            cutter = bpy.context.object
            bpy.context.view_layer.objects.active = body
            mod = body.modifiers.new("Arch_" + front + "_" + side, "BOOLEAN")
            mod.operation = "DIFFERENCE"
            mod.solver = "EXACT"
            mod.object = cutter
            bpy.ops.object.modifier_apply(modifier=mod.name)
            bpy.data.objects.remove(cutter, do_unlink=True)
            tyre = torus("tyre_" + front + "_" + side, (x, y, z), 0.275, 0.08, rubber, (math.pi / 2, 0, 0))
            tyre.scale.z = 1.45
            sign = -1 if y < 0 else 1
            torus(
                "rim_" + front + "_" + side,
                (x, y + sign * 0.093, z),
                0.244,
                0.015,
                alloy,
                (math.pi / 2, 0, 0),
            )
            cylinder(
                "brake_" + front + "_" + side,
                (x, y + sign * 0.068, z),
                0.21,
                0.018,
                brake,
                (math.pi / 2, 0, 0),
                48,
            )
            cylinder(
                "hub_" + front + "_" + side,
                (x, y + sign * 0.114, z),
                0.06,
                0.028,
                alloy,
                (math.pi / 2, 0, 0),
            )
            for n in range(10):
                angle = 2 * math.pi * n / 10
                a = (x + 0.052 * math.cos(angle), y + sign * 0.12, z + 0.052 * math.sin(angle))
                b = (
                    x + 0.226 * math.cos(angle + 0.11),
                    y + sign * 0.103,
                    z + 0.226 * math.sin(angle + 0.11),
                )
                tube("spoke_" + front + "_" + side + "_" + str(n), (a, b), 0.012, alloy)
            cube(
                "caliper_" + front + "_" + side,
                (x - 0.16, y + sign * 0.073, z + 0.055),
                (0.06, 0.035, 0.14),
                red,
                0.014,
            )
            # Wheel-arch lip, independent closed trim.
            pts = [
                (x + 0.411 * math.cos(a), y + sign * 0.077, z + 0.411 * math.sin(a))
                for a in [math.pi * j / 16 for j in range(17)]
            ]
            tube("arch_lip_" + front + "_" + side, pts, 0.012, black)
    # Separate glazing panels, pillars and roof (closed thin solids).
    loft(
        "windshield",
        [(0.42, 0.69, 0.78, 0.805), (0.28, 0.655, 0.91, 0.935), (0.03, 0.565, 1.19, 1.215)],
        glass,
    )
    loft(
        "rear_glass",
        [(-1.35, 0.66, 0.76, 0.785), (-1.12, 0.62, 0.94, 0.965), (-0.87, 0.555, 1.19, 1.215)],
        glass,
    )
    loft(
        "roof",
        [
            (-0.90, 0.57, 1.18, 1.225),
            (-0.64, 0.585, 1.21, 1.27),
            (-0.3, 0.585, 1.215, 1.275),
            (0.01, 0.565, 1.18, 1.22),
        ],
        paint,
    )
    for side, sgn in (("left", -1), ("right", 1)):
        # Sloping side glass as a closed hull; differing widths follow cabin taper.
        verts = [
            (0.39, sgn * 0.685, 0.81),
            (-1.29, sgn * 0.67, 0.81),
            (-0.87, sgn * 0.553, 1.195),
            (0.01, sgn * 0.56, 1.195),
        ]
        mesh = bpy.data.meshes.new("window_" + side)
        offset = 0.009 * sgn
        mesh.from_pydata(
            verts + [(x, y + offset, z) for x, y, z in verts],
            [],
            [(0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)],
        )
        obj = bpy.data.objects.new("side_glass_" + side, mesh)
        bpy.context.collection.objects.link(obj)
        finish(obj, obj.name, glass)
        tube(
            "a_pillar_" + side,
            [(0.45, sgn * 0.7, 0.795), (0.22, sgn * 0.64, 0.985), (0.0, sgn * 0.57, 1.22)],
            0.022,
            paint,
        )
        tube(
            "c_pillar_" + side,
            [(-1.36, sgn * 0.695, 0.79), (-1.1, sgn * 0.63, 1.03), (-0.87, sgn * 0.567, 1.22)],
            0.026,
            paint,
        )
        tube("window_seal_" + side, verts, 0.009, black, True)
        # Continuous door shut line, handle and side intake.
        tube(
            "door_seam_" + side,
            [
                (0.45, sgn * 0.945, 0.68),
                (0.35, sgn * 0.955, 0.33),
                (-0.88, sgn * 0.955, 0.32),
                (-0.96, sgn * 0.95, 0.7),
            ],
            0.006,
            black,
        )
        cube("door_handle_" + side, (-0.67, sgn * 0.965, 0.72), (0.16, 0.018, 0.022), alloy, 0.008)
        cube("sill_" + side, (-0.02, sgn * 0.915, 0.205), (2.25, 0.1, 0.065), black, 0.02)
        tube("mirror_stem_" + side, [(0.23, sgn * 0.71, 0.88), (0.26, sgn * 0.94, 0.88)], 0.018, black)
        ellipsoid("mirror_" + side, (0.27, sgn * 1.025, 0.9), (0.21, 0.19, 0.095), paint)
        ellipsoid("mirror_insert_" + side, (0.215, sgn * 1.03, 0.904), (0.018, 0.15, 0.065), alloy)
        cube("headlamp_" + side, (2.405, sgn * 0.56, 0.66), (0.14, 0.47, 0.054), lamp, 0.023)
        cube("headlamp_surround_" + side, (2.405, sgn * 0.56, 0.655), (0.13, 0.515, 0.093), black, 0.025)
        for stripe in range(3):
            cube(
                "led_" + side + "_" + str(stripe),
                (2.480, sgn * 0.56, 0.651 + stripe * 0.012),
                (0.005, 0.36, 0.004),
                lamp,
                0.001,
            )
        cube("tail_lamp_" + side, (-2.415, sgn * 0.54, 0.66), (0.055, 0.54, 0.05), red, 0.018)
    cube("front_grille", (2.415, 0, 0.38), (0.08, 1.14, 0.16), black, 0.035)
    for i in range(17):
        cube("grille_fin_" + str(i), (2.463, -0.51 + i * 0.064, 0.38), (0.015, 0.012, 0.12), alloy, 0.003)
    cube("front_splitter", (2.29, 0, 0.205), (0.2, 1.78, 0.038), black, 0.012)
    cube("rear_diffuser", (-2.26, 0, 0.215), (0.22, 1.6, 0.055), black, 0.015)
    for y in (-0.58, -0.39, 0, 0.39, 0.58):
        cube("diffuser_fin_" + str(int((y + 1) * 100)), (-2.27, y, 0.2), (0.28, 0.02, 0.09), black, 0.006)
    for y in (-0.54, 0.54):
        cylinder(
            "exhaust_" + str(int((y + 1) * 100)), (-2.39, y, 0.3), 0.065, 0.10, alloy, (0, math.pi / 2, 0)
        )
    for side, y in (("driver", -0.33), ("passenger", 0.33)):
        cube(side + "_seat", (-0.52, y, 0.64), (0.52, 0.46, 0.11), leather, 0.07)
        seat = cube(side + "_seat_back", (-0.84, y, 0.89), (0.105, 0.43, 0.39), leather, 0.07)
        seat.rotation_euler.y = -0.17
        cube(side + "_headrest", (-0.89, y, 1.08), (0.11, 0.22, 0.14), leather, 0.05)
    cube("dashboard", (0.16, 0, 0.865), (0.24, 1.12, 0.13), black, 0.045)
    torus("steering_wheel", (0.0, -0.33, 1.01), 0.117, 0.014, black, (0, math.pi / 2 - 0.22, 0))
    cube("console", (-0.35, 0, 0.85), (0.64, 0.13, 0.12), black, 0.028)


def build_building():
    brick = material("warm_masonry", (0.43, 0.18, 0.09), 0, 0.8)
    stone = material("limestone", (0.65, 0.59, 0.46), 0, 0.65)
    metal = material("ironwork", (0.025, 0.035, 0.04), 0.75, 0.35)
    glass = material("architectural_glass", (0.035, 0.095, 0.12), 0.28, 0.12, 0.18)
    wood = material("oak_door", (0.18, 0.07, 0.022), 0, 0.52)
    light = material("warm_lamp", (0.95, 0.62, 0.22), 0.08, 0.25)
    cube("foundation", (0, 0, 0.12), (5.0, 3.7, 0.24), stone, 0.04)
    facade = cube("facade", (0, 0.1, 3.38), (4.6, 3.2, 6.45), brick, 0.035)
    for x, z in [(-1.09, 3.05), (1.09, 3.05), (-1.09, 5.2), (1.09, 5.2), (-0.66, 1.17)]:
        cutter = cube(
            "recess_cutter",
            (x, -1.45, z),
            (2.56 if z == 1.17 else 1.12, 0.45, 1.45 if z == 1.17 else 1.31),
            brick,
            0,
        )
        bpy.context.view_layer.objects.active = facade
        mod = facade.modifiers.new("window_recess", "BOOLEAN")
        mod.operation = "DIFFERENCE"
        mod.solver = "EXACT"
        mod.object = cutter
        bpy.ops.object.modifier_apply(modifier=mod.name)
        bpy.data.objects.remove(cutter, do_unlink=True)
    # Original relief masonry courses, deliberately modest geometry.
    for row in range(18):
        z = 2.2 + row * 0.22
        for col in range(9):
            x = -2.15 + col * 0.51 + (0.24 if row % 2 else 0)
            if x > 2.25 or any(
                abs(x - wx) < 0.82 and abs(z - wz) < 0.82 for wx in (-1.09, 1.09) for wz in (3.05, 5.2)
            ):
                continue
            cube(f"brick_{row}_{col}", (x, -1.512, z), (0.47, 0.035, 0.185), brick, 0.008)
    for z, name in (
        (0.38, "base_course"),
        (2.05, "shop_header"),
        (4.2, "middle_course"),
        (6.65, "cornice"),
    ):
        cube(name, (0, -0.02, z), (4.82, 3.52, 0.14), stone, 0.025)
    cube("parapet", (0, 0.1, 6.84), (4.62, 3.25, 0.20), brick, 0.03)
    cube("roof_cap", (0, 0.1, 6.99), (4.9, 3.55, 0.12), stone, 0.028)
    cube("shop_window", (-0.66, -1.40, 1.17), (2.53, 0.045, 1.43), glass, 0.025)
    cube("entry_door", (1.41, -1.59, 1.14), (0.92, 0.11, 1.52), wood, 0.025)
    cube("door_glazing", (1.41, -1.655, 1.28), (0.65, 0.035, 1.02), glass, 0.012)
    for x in (-2.02, 0.66, 2.07):
        cube("shop_pier_" + str(int((x + 3) * 100)), (x, -1.58, 1.14), (0.17, 0.19, 1.7), stone, 0.015)
    for x in (-1.48, -0.7, 0.08):
        cube(
            "shop_mullion_" + str(int((x + 3) * 100)), (x, -1.602, 1.16), (0.045, 0.045, 1.40), metal, 0.005
        )
    cylinder("door_handle", (1.70, -1.71, 1.11), 0.025, 0.26, metal)
    for level, z in ((1, 3.05), (2, 5.2)):
        for side, x in (("left", -1.09), ("right", 1.09)):
            prefix = "window_" + side if level == 1 else "upper_window_" + side
            cube(prefix, (x, -1.40, z), (1.10, 0.055, 1.29), glass, 0.015)
            for s in (-1, 1):
                cube(
                    prefix + "_jamb_" + ("left" if s < 0 else "right"),
                    (x + s * 0.62, -1.62, z),
                    (0.14, 0.17, 1.54),
                    stone,
                    0.018,
                )
            for dz in (-0.73, 0.73):
                cube(
                    prefix + "_lintel_" + ("bottom" if dz < 0 else "top"),
                    (x, -1.62, z + dz),
                    (1.39, 0.20, 0.14),
                    stone,
                    0.018,
                )
            cube(prefix + "_mullion", (x, -1.612, z), (0.043, 0.04, 1.31), metal, 0.004)
            cube(prefix + "_crossbar", (x, -1.617, z + 0.09), (1.12, 0.04, 0.043), metal, 0.004)
    cube("balcony_floor", (0, -1.92, 2.32), (3.73, 1.08, 0.18), stone, 0.022)
    tube("balcony_top_rail", [(-1.8, -2.40, 3.2), (1.8, -2.40, 3.2)], 0.035, metal)
    for side, x in (("left", -1.8), ("right", 1.8)):
        tube("balcony_side_rail_" + side, [(x, -1.6, 3.2), (x, -2.4, 3.2)], 0.035, metal)
    for i in range(19):
        cylinder("baluster_" + str(i), (-1.77 + i * 0.196, -2.39, 2.79), 0.014, 0.78, metal)
    for x in (-1.78, 1.78):
        for y in (-1.68, -2.06):
            cylinder(
                "side_baluster_" + str(int((x + 2) * 100)) + "_" + str(int(-y * 100)),
                (x, y, 2.79),
                0.014,
                0.78,
                metal,
            )
    for x in (-2.16, 2.16):
        cube("lamp_mount_" + str(int((x + 3) * 100)), (x, -1.7, 1.65), (0.12, 0.12, 0.23), metal, 0.02)
        ellipsoid("entrance_lamp_" + str(int((x + 3) * 100)), (x, -1.77, 1.66), (0.13, 0.14, 0.2), light)
    cube("shop_sign", (0, -1.66, 1.94), (2.62, 0.06, 0.20), metal, 0.015)
    # Shallow raised letters are real mesh and retain volumetric thickness.
    curve = bpy.data.curves.new("atelier_letters", "FONT")
    curve.body = "A T E L I E R   1 4"
    curve.align_x = "CENTER"
    curve.size = 0.13
    curve.extrude = 0.008
    curve.bevel_depth = 0
    obj = bpy.data.objects.new("atelier_letters", curve)
    bpy.context.collection.objects.link(obj)
    obj.location = (0, -1.704, 1.895)
    obj.rotation_euler = (math.pi / 2, 0, 0)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.convert(target="MESH")
    finish(bpy.context.object, "atelier_letters", stone)


def add_component(family, component):
    if component == "rear_spoiler":
        mat = bpy.data.materials["black_trim"]
        for side, y in (("left", -0.54), ("right", 0.54)):
            cube("spoiler_support_" + side, (-1.92, y, 0.86), (0.11, 0.05, 0.21), mat, 0.012)
        loft(
            "rear_spoiler",
            [(-2.06, 0.89, 0.95, 1.01), (-1.93, 0.92, 0.99, 1.04), (-1.79, 0.89, 0.96, 1.01)],
            mat,
        )
    elif component == "entrance_planters":
        mat = bpy.data.materials["limestone"]
        leaf = material("foliage", (0.06, 0.12, 0.042), 0, 0.8)
        for side, x in (("left", -2.10), ("right", 2.10)):
            cube("planter_" + side, (x, -1.94, 0.47), (0.40, 0.40, 0.45), mat, 0.04)
            for i in range(5):
                ellipsoid(
                    "topiary_" + side + "_" + str(i),
                    (x + (0.04 if i % 2 else -0.04), -1.94, 0.86 + i * 0.065),
                    (0.30, 0.28, 0.27),
                    leaf,
                )
    elif component == "lumbar_cushion":
        mat = bpy.data.materials.get("saddle_leather") or next(
            m for m in bpy.data.materials if "leather" in m.name
        )
        cube("lumbar_cushion", (0, 0.02, 0.66), (0.65, 0.25, 0.20), mat, 0.08)
    else:
        raise ValueError("unknown_component")


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
        vertices=vertices,
        radius1=radius,
        radius2=radius if radius_end is None else radius_end,
        depth=delta.length,
        location=(Vector(start) + Vector(end)) / 2,
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
            faces.append(
                (i * sides + j, i * sides + (j + 1) % sides, nxt * sides + (j + 1) % sides, nxt * sides + j)
            )
    if not closed:
        faces.extend(
            [tuple(reversed(range(sides))), tuple((len(points) - 1) * sides + j for j in range(sides))]
        )
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
            vertices.append(
                (
                    a * radius * math.copysign(abs(math.cos(theta)) ** exponent, math.cos(theta)),
                    b * radius * math.copysign(abs(math.sin(theta)) ** exponent, math.sin(theta)),
                    height,
                )
            )
    vertices.append((0, 0, c))
    faces = [(0, 1 + (j + 1) % count, 1 + j) for j in range(count)]
    for i in range(rings - 2):
        for j in range(count):
            faces.append(
                (
                    1 + i * count + j,
                    1 + i * count + (j + 1) % count,
                    1 + (i + 1) * count + (j + 1) % count,
                    1 + (i + 1) * count + j,
                )
            )
    faces.extend(
        (len(vertices) - 1, 1 + (rings - 2) * count + j, 1 + (rings - 2) * count + (j + 1) % count)
        for j in range(count)
    )
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
                    vertex = (
                        u * width,
                        -0.45 + 0.77 * t,
                        0.40 - 0.065 * t + 0.075 * u * u - back * thickness,
                    )
                else:
                    vertex = (
                        u * width,
                        0.27 + 0.32 * t - 0.09 * u * u + back * thickness,
                        0.40 + 0.79 * t + 0.012 * u * u,
                    )
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
    faces.extend(
        (rim[i], rim[(i + 1) % len(rim)], rim[(i + 1) % len(rim)] + layer, rim[i] + layer)
        for i in range(len(rim))
    )
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
            points.append(
                (
                    0.996 * a * math.copysign(abs(math.cos(theta)) ** 0.34, math.cos(theta)),
                    0.996 * b * math.copysign(abs(math.sin(theta)) ** 0.34, math.sin(theta)),
                    c * 0.43,
                )
            )
        edge = chair_tube(obj.name + "_piping", points, 0.0034, piping, closed=True)
        edge.location, edge.rotation_euler = obj.location.copy(), obj.rotation_euler.copy()
    for side, label in ((-1, "left"), (1, "right")):
        points = []
        for j in range(25):
            t = j / 24
            points.append(
                (
                    side * (0.485 + 0.013 * math.sin(math.pi * t)),
                    -0.36 + 0.82 * t,
                    0.718 + 0.035 * math.sin(math.pi * t) + 0.01 * t,
                )
            )
        # A vertically flattened rounded beam makes a usable broad open armrest.
        arm = chair_tube("arm_" + label, points, 0.046, walnut, sides=16)
        for vertex in arm.data.vertices:
            vertex.co.z = 0.735 + (vertex.co.z - 0.735) * 0.56
        for fore, y in (("front", -0.30), ("rear", 0.28)):
            chair_beam(
                "arm_support_" + fore + "_" + label,
                (side * 0.40, y, 0.40),
                (side * 0.485, y, 0.719),
                0.013,
                brass,
            )
            chair_beam(
                "arm_joint_" + fore + "_" + label,
                (side * 0.467, y, 0.705),
                (side * 0.467, y, 0.733),
                0.023,
                brass,
            )
        for fore, y in (("front", -0.30), ("rear", 0.30)):
            top = (side * 0.335, y, 0.405)
            bottom = (side * 0.465, y * 1.42, 0.046)
            chair_beam("leg_" + fore + "_" + label, bottom, top, 0.022, walnut, radius_end=0.038)
            delta = (Vector(top) - Vector(bottom)).normalized()
            chair_beam(
                "leg_collar_" + fore + "_" + label,
                Vector(top) - delta * 0.042,
                Vector(top) + delta * 0.012,
                0.039,
                brass,
            )
            chair_beam(
                "foot_" + fore + "_" + label,
                (bottom[0], bottom[1], 0.008),
                (bottom[0], bottom[1], 0.045),
                0.024,
                feet,
            )
        chair_beam(
            "underframe_" + label,
            (side * 0.335, -0.325, 0.377),
            (side * 0.335, 0.355, 0.377),
            0.028,
            walnut,
        )
    for y in (-0.27, 0.28):
        chair_beam(
            "cross_brace_" + ("front" if y < 0 else "rear"),
            (-0.375, y, 0.356),
            (0.375, y, 0.356),
            0.023,
            walnut,
        )
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

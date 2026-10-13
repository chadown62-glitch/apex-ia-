"""Original detailed family assemblers; PR12 primitives remain the shared implementation."""
import math
import bmesh
import bpy
from mathutils import Vector
from blender_ops import material,cube,finish,loft,tube,ellipsoid,cylinder,torus


def build_vehicle():
    paint=material('petrol_paint',(0.018,0.105,0.145),.78,.24)
    black=material('black_trim',(.012,.016,.019),.3,.34)
    rubber=material('tyre_rubber',(.015,.017,.02),0,.75)
    alloy=material('satin_alloy',(.46,.51,.55),.92,.24)
    brake=material('brake_steel',(.19,.22,.25),.8,.4)
    glass=material('smoked_glass',(.025,.065,.082),.12,.12,.3)
    lamp=material('lamp_lens',(.7,.83,1),.2,.15)
    red=material('rear_lens',(.45,.008,.012),.3,.22)
    leather=material('cockpit_leather',(.08,.045,.025),0,.54)
    # Dense, controlled transverse profiles produce shoulder, waist and nose curvature.
    sections=[]
    for i in range(41):
        t=i/40;x=4.8*(t-.5)
        width=.96*(.80+.18*math.sin(math.pi*t)+.06*math.exp(-((t-.23)/.13)**2))
        upper=.64+.15*math.sin(math.pi*t)+.04*math.exp(-((t-.72)/.16)**2)
        sections.append((x,width,.19,upper))
    body=loft('chassis',sections,paint)
    bpy.context.view_layer.objects.active=body
    mod=body.modifiers.new('Shoulder_continuity','SUBSURF');mod.levels=2
    bpy.ops.object.modifier_apply(modifier=mod.name)
    for front,x in (('front',1.4),('rear',-1.4)):
        for side,y in (('left',-0.94),('right',.94)):
            z=.355
            bpy.ops.mesh.primitive_cylinder_add(vertices=64,radius=.405,depth=.8,location=(x,y,z),rotation=(math.pi/2,0,0))
            cutter=bpy.context.object;bpy.context.view_layer.objects.active=body
            mod=body.modifiers.new('Arch_'+front+'_'+side,'BOOLEAN');mod.operation='DIFFERENCE';mod.solver='EXACT';mod.object=cutter
            bpy.ops.object.modifier_apply(modifier=mod.name);bpy.data.objects.remove(cutter,do_unlink=True)
            tyre=torus('tyre_'+front+'_'+side,(x,y,z),.275,.08,rubber,(math.pi/2,0,0));tyre.scale.z=1.45
            sign=-1 if y<0 else 1
            torus('rim_'+front+'_'+side,(x,y+sign*.093,z),.244,.015,alloy,(math.pi/2,0,0))
            cylinder('brake_'+front+'_'+side,(x,y+sign*.068,z),.21,.018,brake,(math.pi/2,0,0),48)
            cylinder('hub_'+front+'_'+side,(x,y+sign*.114,z),.06,.028,alloy,(math.pi/2,0,0))
            for n in range(10):
                angle=2*math.pi*n/10
                a=(x+.052*math.cos(angle),y+sign*.12,z+.052*math.sin(angle))
                b=(x+.226*math.cos(angle+.11),y+sign*.103,z+.226*math.sin(angle+.11))
                tube('spoke_'+front+'_'+side+'_'+str(n),(a,b),.012,alloy)
            cube('caliper_'+front+'_'+side,(x-.16,y+sign*.073,z+.055),(.06,.035,.14),red,.014)
            # Wheel-arch lip, independent closed trim.
            pts=[(x+.411*math.cos(a),y+sign*.077,z+.411*math.sin(a)) for a in [math.pi*j/16 for j in range(17)]]
            tube('arch_lip_'+front+'_'+side,pts,.012,black)
    # Separate glazing panels, pillars and roof (closed thin solids).
    loft('windshield',[(.42,.69,.78,.805),(.28,.655,.91,.935),(.03,.565,1.19,1.215)],glass)
    loft('rear_glass',[(-1.35,.66,.76,.785),(-1.12,.62,.94,.965),(-.87,.555,1.19,1.215)],glass)
    loft('roof',[(-.90,.57,1.18,1.225),(-.64,.585,1.21,1.27),(-.3,.585,1.215,1.275),(.01,.565,1.18,1.22)],paint)
    for side,sgn in (('left',-1),('right',1)):
        # Sloping side glass as a closed hull; differing widths follow cabin taper.
        verts=[(.39,sgn*.685,.81),(-1.29,sgn*.67,.81),(-.87,sgn*.553,1.195),(.01,sgn*.56,1.195)]
        mesh=bpy.data.meshes.new('window_'+side);offset=.009*sgn
        mesh.from_pydata(verts+[(x,y+offset,z) for x,y,z in verts],[],[(0,1,2,3),(7,6,5,4),(0,4,5,1),(1,5,6,2),(2,6,7,3),(3,7,4,0)])
        obj=bpy.data.objects.new('side_glass_'+side,mesh);bpy.context.collection.objects.link(obj);finish(obj,obj.name,glass)
        tube('a_pillar_'+side,[(.45,sgn*.7,.795),(.22,sgn*.64,.985),(.0,sgn*.57,1.22)],.022,paint)
        tube('c_pillar_'+side,[(-1.36,sgn*.695,.79),(-1.1,sgn*.63,1.03),(-.87,sgn*.567,1.22)],.026,paint)
        tube('window_seal_'+side,verts,.009,black,True)
        # Continuous door shut line, handle and side intake.
        tube('door_seam_'+side,[(.45,sgn*.945,.68),(.35,sgn*.955,.33),(-.88,sgn*.955,.32),(-.96,sgn*.95,.7)],.006,black)
        cube('door_handle_'+side,(-.67,sgn*.965,.72),(.16,.018,.022),alloy,.008)
        cube('sill_'+side,(-.02,sgn*.915,.205),(2.25,.1,.065),black,.02)
        tube('mirror_stem_'+side,[(.23,sgn*.71,.88),(.26,sgn*.94,.88)],.018,black)
        ellipsoid('mirror_'+side,(.27,sgn*1.025,.9),(.21,.19,.095),paint)
        ellipsoid('mirror_insert_'+side,(.215,sgn*1.03,.904),(.018,.15,.065),alloy)
        cube('headlamp_'+side,(2.24,sgn*.55,.585),(.14,.47,.054),lamp,.023)
        cube('headlamp_surround_'+side,(2.225,sgn*.55,.584),(.13,.515,.093),black,.025)
        for stripe in range(3):
            cube('led_'+side+'_'+str(stripe),(2.315,sgn*.55,.576+stripe*.012),(.005,.36,.004),lamp,.001)
        cube('tail_lamp_'+side,(-2.29,sgn*.54,.62),(.055,.54,.05),red,.018)
    cube('front_grille',(2.325,0,.32),(.08,1.14,.16),black,.035)
    for i in range(17):cube('grille_fin_'+str(i),(2.369,-.51+i*.064,.32),(.015,.012,.12),alloy,.003)
    cube('front_splitter',(2.29,0,.205),(.2,1.78,.038),black,.012)
    cube('rear_diffuser',(-2.26,0,.215),(.22,1.6,.055),black,.015)
    for y in (-.58,-.39,0,.39,.58):cube('diffuser_fin_'+str(int((y+1)*100)),(-2.27,y,.2),(.28,.02,.09),black,.006)
    for y in (-.54,.54):cylinder('exhaust_'+str(int((y+1)*100)),(-2.39,y,.3),.065,.10,alloy,(0,math.pi/2,0))
    for side,y in (('driver',-.33),('passenger',.33)):
        cube(side+'_seat',(-.52,y,.8),(.52,.46,.11),leather,.07)
        seat=cube(side+'_seat_back',(-.84,y,1.00),(.105,.43,.39),leather,.07);seat.rotation_euler.y=-.17
        cube(side+'_headrest',(-.89,y,1.19),(.11,.22,.14),leather,.05)
    cube('dashboard',(.16,0,.865),(.24,1.12,.13),black,.045)
    torus('steering_wheel',(.0,-.33,1.01),.117,.014,black,(0,math.pi/2-.22,0))
    cube('console',(-.35,0,.85),(.64,.13,.12),black,.028)


def build_building():
    brick=material('warm_masonry',(.43,.18,.09),0,.8)
    stone=material('limestone',(.65,.59,.46),0,.65)
    metal=material('ironwork',(.025,.035,.04),.75,.35)
    glass=material('architectural_glass',(.035,.095,.12),.28,.12,.18)
    wood=material('oak_door',(.18,.07,.022),0,.52)
    light=material('warm_lamp',(.95,.62,.22),.08,.25)
    cube('foundation',(0,0,.12),(5.0,3.7,.24),stone,.04)
    facade=cube('facade',(0,.1,3.38),(4.6,3.2,6.45),brick,.035)
    for x,z in [(-1.09,3.05),(1.09,3.05),(-1.09,5.2),(1.09,5.2),(-.66,1.17)]:
        cutter=cube('recess_cutter',(x,-1.45,z),(2.56 if z==1.17 else 1.12,.45,1.45 if z==1.17 else 1.31),brick,0)
        bpy.context.view_layer.objects.active=facade
        mod=facade.modifiers.new('window_recess','BOOLEAN');mod.operation='DIFFERENCE';mod.solver='EXACT';mod.object=cutter
        bpy.ops.object.modifier_apply(modifier=mod.name);bpy.data.objects.remove(cutter,do_unlink=True)
    # Original relief masonry courses, deliberately modest geometry.
    for row in range(18):
        z=2.2+row*.22
        for col in range(9):
            x=-2.15+col*.51+(.24 if row%2 else 0)
            if x>2.25 or any(abs(x-wx)<.82 and abs(z-wz)<.82 for wx in (-1.09,1.09) for wz in (3.05,5.2)):continue
            cube(f'brick_{row}_{col}',(x,-1.512,z),(.47,.035,.185),brick,.008)
    for z,name in ((.38,'base_course'),(2.05,'shop_header'),(4.2,'middle_course'),(6.65,'cornice')):
        cube(name,(0,-.02,z),(4.82,3.52,.14),stone,.025)
    cube('parapet',(0,.1,6.84),(4.62,3.25,.20),brick,.03)
    cube('roof_cap',(0,.1,6.99),(4.9,3.55,.12),stone,.028)
    cube('shop_window',(-.66,-1.40,1.17),(2.53,.045,1.43),glass,.025)
    cube('entry_door',(1.41,-1.59,1.14),(.92,.11,1.52),wood,.025)
    cube('door_glazing',(1.41,-1.655,1.28),(.65,.035,1.02),glass,.012)
    for x in (-2.02,.66,2.07):cube('shop_pier_'+str(int((x+3)*100)),(x,-1.58,1.14),(.17,.19,1.7),stone,.015)
    for x in (-1.48,-.7,.08):cube('shop_mullion_'+str(int((x+3)*100)),(x,-1.602,1.16),(.045,.045,1.40),metal,.005)
    cylinder('door_handle',(1.70,-1.71,1.11),.025,.26,metal)
    for level,z in ((1,3.05),(2,5.2)):
        for side,x in (('left',-1.09),('right',1.09)):
            prefix='window_'+side if level==1 else 'upper_window_'+side
            cube(prefix,(x,-1.40,z),(1.10,.055,1.29),glass,.015)
            for s in (-1,1):cube(prefix+'_jamb_'+('left' if s<0 else 'right'),(x+s*.62,-1.62,z),(.14,.17,1.54),stone,.018)
            for dz in (-.73,.73):cube(prefix+'_lintel_'+('bottom' if dz<0 else 'top'),(x,-1.62,z+dz),(1.39,.20,.14),stone,.018)
            cube(prefix+'_mullion',(x,-1.612,z),(.043,.04,1.31),metal,.004)
            cube(prefix+'_crossbar',(x,-1.617,z+.09),(1.12,.04,.043),metal,.004)
    cube('balcony_floor',(0,-1.92,2.32),(3.73,1.08,.18),stone,.022)
    tube('balcony_top_rail',[(-1.8,-2.40,3.2),(1.8,-2.40,3.2)],.035,metal)
    for side,x in (('left',-1.8),('right',1.8)):tube('balcony_side_rail_'+side,[(x,-1.6,3.2),(x,-2.4,3.2)],.035,metal)
    for i in range(19):cylinder('baluster_'+str(i),(-1.77+i*.196,-2.39,2.79),.014,.78,metal)
    for x in (-1.78,1.78):
        for y in (-1.68,-2.06):cylinder('side_baluster_'+str(int((x+2)*100))+'_'+str(int(-y*100)),(x,y,2.79),.014,.78,metal)
    for x in (-2.16,2.16):
        cube('lamp_mount_'+str(int((x+3)*100)),(x,-1.7,1.65),(.12,.12,.23),metal,.02)
        ellipsoid('entrance_lamp_'+str(int((x+3)*100)),(x,-1.77,1.66),(.13,.14,.2),light)
    cube('shop_sign',(0,-1.66,1.94),(2.62,.06,.20),metal,.015)
    # Shallow raised letters are real mesh and retain volumetric thickness.
    curve=bpy.data.curves.new('atelier_letters','FONT');curve.body='A T E L I E R   1 4';curve.align_x='CENTER';curve.size=.13;curve.extrude=.005;curve.bevel_depth=.001
    obj=bpy.data.objects.new('atelier_letters',curve);bpy.context.collection.objects.link(obj);obj.location=(0,-1.704,1.895);obj.rotation_euler=(math.pi/2,0,0)
    bpy.ops.object.select_all(action='DESELECT');obj.select_set(True);bpy.context.view_layer.objects.active=obj;bpy.ops.object.convert(target='MESH');finish(bpy.context.object,'atelier_letters',stone)


def add_component(family,component):
    if component=='rear_spoiler':
        mat=bpy.data.materials['black_trim']
        for side,y in (('left',-.54),('right',.54)):cube('spoiler_support_'+side,(-1.92,y,.86),(.11,.05,.21),mat,.012)
        loft('rear_spoiler',[(-2.06,.89,.95,1.01),(-1.93,.92,.99,1.04),(-1.79,.89,.96,1.01)],mat)
    elif component=='entrance_planters':
        mat=bpy.data.materials['limestone'];leaf=material('foliage',(.06,.12,.042),0,.8)
        for side,x in (('left',-2.10),('right',2.10)):
            cube('planter_'+side,(x,-1.94,.47),(.40,.40,.45),mat,.04)
            for i in range(5):ellipsoid('topiary_'+side+'_'+str(i),(x+(.04 if i%2 else -.04),-1.94,.86+i*.065),(.30,.28,.27),leaf)
    elif component=='lumbar_cushion':
        mat=bpy.data.materials.get('saddle_leather') or next(m for m in bpy.data.materials if 'leather' in m.name)
        cube('lumbar_cushion',(0,.02,.66),(.65,.25,.20),mat,.08)
    else:raise ValueError('unknown_component')


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
        chair_beam("cross_brace_" + ("front" if y<0 else "rear"), (-0.375, y, 0.356), (0.375, y, 0.356), 0.023, walnut)
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



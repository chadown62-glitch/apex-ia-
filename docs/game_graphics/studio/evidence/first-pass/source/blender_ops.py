"""Trusted artistic primitives reusing PR12; all model input remains data."""
import hashlib
import json
import math
import sys
from pathlib import Path
import bmesh
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/game_graphics'))
sys.path.insert(0, str(ROOT / 'apex/game_graphics/studio'))
from build_vehicle import material, cube, finish, loft, parent_preserving
from contract import parse_plan


def cylinder(name, location, radius, depth, mat, rotation=(0,0,0), vertices=32):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=location, rotation=rotation)
    return finish(bpy.context.object,name,mat)


def tube(name, points, radius, mat, cyclic=False):
    curve=bpy.data.curves.new(name,'CURVE'); curve.dimensions='3D'; curve.resolution_u=3
    curve.bevel_depth=radius; curve.bevel_resolution=2; curve.use_fill_caps=True
    spline=curve.splines.new('BEZIER'); spline.bezier_points.add(len(points)-1)
    for p,co in zip(spline.bezier_points,points,strict=True):
        p.co=co;p.handle_left_type='AUTO';p.handle_right_type='AUTO'
    spline.use_cyclic_u=cyclic
    obj=bpy.data.objects.new(name,curve);bpy.context.collection.objects.link(obj)
    bpy.ops.object.select_all(action='DESELECT');obj.select_set(True);bpy.context.view_layer.objects.active=obj
    bpy.ops.object.convert(target='MESH')
    return finish(bpy.context.object,name,mat)


def ellipsoid(name,location,dimensions,mat):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24,ring_count=12,radius=1,location=location)
    o=bpy.context.object;o.scale=Vector(dimensions)/2
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    return finish(o,name,mat)


def torus(name,location,major,minor,mat,rotation=(0,0,0)):
    bpy.ops.mesh.primitive_torus_add(major_segments=40,minor_segments=12,major_radius=major,minor_radius=minor,location=location,rotation=rotation)
    return finish(bpy.context.object,name,mat)


def finalize_meshes():
    for obj in list(bpy.context.scene.objects):
        if obj.type!='MESH' or obj.name=='studio_floor':continue
        bpy.ops.object.select_all(action='DESELECT');obj.select_set(True);bpy.context.view_layer.objects.active=obj
        for mod in list(obj.modifiers):bpy.ops.object.modifier_apply(modifier=mod.name)
        bm=bmesh.new();bm.from_mesh(obj.data)
        bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=1e-7)
        bmesh.ops.triangulate(bm,faces=list(bm.faces),quad_method='BEAUTY',ngon_method='BEAUTY')
        bmesh.ops.recalc_face_normals(bm,faces=bm.faces)
        if bm.calc_volume(signed=True)<0:bmesh.ops.reverse_faces(bm,faces=list(bm.faces))
        bm.to_mesh(obj.data);bm.free()
        bpy.ops.object.mode_set(mode='EDIT');bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.smart_project(island_margin=0.015);bpy.ops.object.mode_set(mode='OBJECT')
        obj['apex_studio_part']=True;obj['apex_owner']='graphics'
        # Avoid smooth shading across planar architectural surfaces.
        if obj.name.startswith(('facade','brick','window','upper_window','shop','cornice','foundation','roof_cap','parapet')):
            for poly in obj.data.polygons:poly.use_smooth=False


def geometry_hash(obj):
    if obj.type!='MESH':return None
    return hashlib.sha256(json.dumps({'vertices':[[round(v,7) for v in p.co] for p in obj.data.vertices], 'faces':[list(p.vertices) for p in obj.data.polygons]}).encode()).hexdigest()


def pbr(mat):
    node=mat.node_tree.nodes.get('Principled BSDF') if mat.use_nodes else None
    if not node:return {}
    return {k: {'value':list(node.inputs[k].default_value) if k=='Base Color' else node.inputs[k].default_value, 'linked':node.inputs[k].is_linked} for k in ('Base Color','Roughness','Metallic')}


def hierarchy():
    return {o.name:{'type':o.type,'parent':o.parent.name if o.parent else None,'location_m':list(o.location),
                    'dimensions_m':list(o.dimensions),'materials':[m.name for m in o.data.materials if m] if o.type=='MESH' else [],
                    'pbr':{m.name:pbr(m) for m in o.data.materials if m}, 'vertices':len(o.data.vertices) if o.type=='MESH' else 0,'geometry_sha256':geometry_hash(o)}
            for o in bpy.context.scene.objects if o.type=='MESH' and o.name!='studio_floor'}


def apply_operations(plan, add_component):
    parse_plan(plan);trace=[]
    for item in plan['operations']:
        op,target,p=item['op'],item['target'],item['params']
        before=hierarchy()
        if op in ('inspect_scene','save_revision_and_render'):pass
        elif op=='add_component':
            marker='apex_component_'+p['component']
            if bpy.context.scene.get(marker):raise ValueError('component_already_present')
            add_component(plan['family'],p['component']);bpy.context.scene[marker]=True
            for o in bpy.context.scene.objects:
                if o.type=='MESH' and o.name not in before:o['apex_owner']='graphics';o['apex_studio_part']=True
        elif op=='set_material_pbr':
            mat=bpy.data.materials.get(target)
            users=[o for o in bpy.context.scene.objects if o.type=='MESH' and mat in list(o.data.materials)]
            if not mat or not users or any(o.get('apex_owner')!='graphics' or not o.get('apex_studio_part') for o in users):
                raise ValueError('unknown_asset_material:'+target)
            node=mat.node_tree.nodes.get('Principled BSDF')
            if any(node.inputs[k].is_linked for k in ('Base Color','Roughness','Metallic')):raise ValueError('procedural_material_requires_dedicated_operation')
            node.inputs['Base Color'].default_value=(*p['base_color'],1)
            node.inputs['Roughness'].default_value=p['roughness'];node.inputs['Metallic'].default_value=p['metallic']
        else:
            obj=bpy.data.objects.get(target)
            if obj is None or obj.type!='MESH' or target=='studio_floor' or obj.get('apex_owner')!='graphics' or not obj.get('apex_studio_part'):
                raise ValueError('unauthorized_graphic_target:'+target)
            bpy.ops.object.select_all(action='DESELECT');obj.select_set(True);bpy.context.view_layer.objects.active=obj
            if op=='set_location':obj.location=Vector(p['location_m'])
            elif op=='bevel_edges':
                mod=obj.modifiers.new('APEX_local_bevel','BEVEL');mod.width=p['width_m'];mod.segments=p['segments'];mod.limit_method='ANGLE'
                bpy.ops.object.modifier_apply(modifier=mod.name)
            elif op=='subdivide_surface':
                if len(obj.data.polygons)*(4**p['levels'])>70000:raise ValueError('subdivision_triangle_budget')
                mod=obj.modifiers.new('APEX_local_subdivision','SUBSURF');mod.levels=p['levels'];mod.subdivision_type='CATMULL_CLARK'
                bpy.ops.object.modifier_apply(modifier=mod.name)
        bpy.context.view_layer.update()
        after=hierarchy();changed=[n for n in after if n not in before or before[n]!=after[n]]
        trace.append({'operation':item,'changed_objects':changed,'before':{n:before.get(n) for n in changed},'after':{n:after[n] for n in changed}})
    return trace

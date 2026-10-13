"""Trusted fixed Blender script: display actual neural output, no geometry repair."""
import bpy, hashlib, json, math, struct, time, resource
from pathlib import Path
from mathutils import Vector,Matrix
OUT=Path('/workspace/game-studio-notes/neural14-research/visual-inspection-128')
SOURCE=Path('/workspace/game-studio-runtime/neural14-research/chair-cpu-128/neural-mesh.glb')
EXPECTED='2ab85f1356707a4b56fd6eb5b6d1a3d76bb90fb936fe52d868321c9013ce0d6a'
start=time.monotonic()
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(SOURCE)==EXPECTED
assert not (OUT/'neural.blend').exists()
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=str(SOURCE))
meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
assert meshes
report={'blender':bpy.app.version_string,'source':str(SOURCE),'source_sha256':EXPECTED,'method':'actual TripoSR mesh imported; no remesh, subdivision, hole repair, or procedural replacement','transforms':[],'views':[],'materials':[]}
def vertex_hash(obj):
 h=hashlib.sha256()
 for v in obj.data.vertices:h.update(struct.pack('<fff',*v.co))
 return h.hexdigest()
before={o.name:vertex_hash(o) for o in meshes}
# TripoSR's native coordinates are Z-up. Trimesh GLB export did not convert to Y-up.
# Undo Blender's mandatory glTF Y-up -> Z-up import convention with a rigid rotation.
for obj in meshes:obj.matrix_world=Matrix.Rotation(-math.pi/2,4,'X') @ obj.matrix_world
bpy.context.view_layer.update()
pts=[o.matrix_world@Vector(c) for o in meshes for c in o.bound_box]
lo=Vector([min(v[i] for v in pts) for i in range(3)]);hi=Vector([max(v[i] for v in pts) for i in range(3)])
scale=1.9819968801536927;center=Vector([0.031131267547607422, -0.0002063363790512085, 0.056010231375694275]);lo=Vector([-0.3638817071914673, -0.39903363585472107, -0.3980771601200104]) # Frozen display normalization from 64 baseline
T=Matrix.Translation(Vector((-center.x*scale,-center.y*scale,-lo.z*scale)))@Matrix.Scale(scale,4)
for obj in meshes:
 old=obj.name;obj.matrix_world=T@obj.matrix_world
 assert vertex_hash(obj)==before[old], 'Mesh vertex positions changed'
 obj['provenance']='neural:stabilityai/TripoSR@5b521936b01fbe1890f6f9baed0254ab6351c04a'
 obj['geometry_sha_preserved_after_import']=before[old]
 report['transforms'].append({'object':old,'mesh_local_vertices_sha256':before[old],'final_matrix_world':[list(row) for row in obj.matrix_world],'uniform_display_scale':scale,'rotation_x_degrees':-90,'source_units':'unknown normalized neural units; display normalization only'})
 if not obj.data.materials:
  mat=bpy.data.materials.new('TripoSR_predicted_vertex_colours');mat.use_nodes=True;obj.data.materials.append(mat)
 assert obj.data.color_attributes, 'Predicted vertex colors lost on import'
 for mat in obj.data.materials:
  if not mat:continue
  mat.use_nodes=True
  bsdf=next((n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED'),None)
  if bsdf:
   bsdf.inputs['Roughness'].default_value=.68;bsdf.inputs['Metallic'].default_value=0
   color_attr=obj.data.color_attributes.active_color
   if color_attr and not bsdf.inputs['Base Color'].is_linked:
    vc=mat.node_tree.nodes.new('ShaderNodeVertexColor');vc.layer_name=color_attr.name
    mat.node_tree.links.new(vc.outputs['Color'],bsdf.inputs['Base Color'])
  report['materials'].append({'name':mat.name,'colour_attributes':[c.name for c in obj.data.color_attributes],'nodes':[n.bl_idname for n in mat.node_tree.nodes],'roughness':.68,'metallic':0,'note':'Neutral display shader, not predicted PBR; imported vertex colours retained'})
bpy.context.view_layer.update()
scene=bpy.context.scene
scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=64;scene.cycles.seed=14;scene.cycles.use_denoising=False  # Actual cloud build has no OpenImageDenoise support.
scene.render.threads_mode='FIXED';scene.render.threads=4
scene.render.resolution_x=960;scene.render.resolution_y=720;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA'
scene.view_settings.view_transform='AgX';scene.world.use_nodes=True
scene.world.node_tree.nodes['Background'].inputs[0].default_value=(.16,.19,.24,1)
scene.world.node_tree.nodes['Background'].inputs[1].default_value=.35
bpy.ops.mesh.primitive_plane_add(size=200,location=(0,0,-.018));floor=bpy.context.object;floor.name='studio_floor'
mat=bpy.data.materials.new('neutral_studio_floor');mat.use_nodes=True;mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value=(.055,.075,.10,1);mat.node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value=.82;floor.data.materials.append(mat)
def area(name,loc,power,size,color):
 data=bpy.data.lights.new(name,'AREA');data.energy=power;data.shape='DISK';data.size=size;data.color=color
 ob=bpy.data.objects.new(name,data);scene.collection.objects.link(ob);ob.location=loc;ob.rotation_euler=(Vector((0,0,.8))-ob.location).to_track_quat('-Z','Y').to_euler()
area('key',(-3,-4,5),800,4,(1,.88,.75));area('fill',(4,-1,3),550,3,(.7,.83,1));area('rim',(0,4,4),1000,3,(1,.93,.83))
camd=bpy.data.cameras.new('inspection_camera');cam=bpy.data.objects.new('inspection_camera',camd);scene.collection.objects.link(cam);scene.camera=cam
camd.type='ORTHO';camd.ortho_scale=2.8
z=0.9000000357627869;target=Vector((0,0,z)) # Frozen cameras from 64 baseline
for index,azimuth in enumerate((0,90,180,270)):
 angle=math.radians(azimuth);cam.location=(4*math.cos(angle),4*math.sin(angle),z+1.75);cam.rotation_euler=(target-cam.location).to_track_quat('-Z','Y').to_euler()
 path=OUT/f'view_{azimuth:03d}.png';scene.render.filepath=str(path)
 t=time.monotonic();bpy.ops.render.render(write_still=True)
 report['views'].append({'path':path.name,'sha256':sha(path),'camera_location':list(cam.location),'camera_target':list(target),'orthographic_scale':camd.ortho_scale,'azimuth_degrees':azimuth,'render_seconds':time.monotonic()-t})
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'neural.blend'))
assert all(vertex_hash(obj)==before[obj.name] for obj in meshes)
report.update({'render_engine':'Cycles CPU','samples':64,'seed':14,'denoising':False,'denoising_reason':'Actual first render failed: cloud Blender build lacks OpenImageDenoise; original failure log preserved','resolution':[960,720],'threads':4,'elapsed_seconds':time.monotonic()-start,'self_peak_rss_bytes_linux':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,'source_sha256_after':sha(SOURCE),'blend_sha256':sha(OUT/'neural.blend'),'geometry_vertices_changed_by_display':False,'display_material_fix':'GLB has COLOR_0 but no material; explicit vertex-color node added after first white-render inspection. Initial renders archived, not used as neural quality evidence','interpretation':'Rendering only; source topology and inferred colours retain all defects; camera/light/display transform frozen from baseline64'})
(OUT/'render-report.json').write_text(json.dumps(report,indent=2)+'\n')
print('APEX_NEURAL_RENDER='+json.dumps({'elapsed_seconds':report['elapsed_seconds'],'views':4,'source_sha256':EXPECTED}))

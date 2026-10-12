"""Atelier 3D d'APEX (`apex.brain.studio3d`, `mesh3d`, `gamecode`) : appels sûrs, résumés exacts,
fichiers pour Blender, Unity et Unreal.

Les vérifications utilisent les VRAIS outils quand ils sont installés : Blender (sans écran) pour le
squelette et les exports FBX/glTF, le compilateur C# (dotnet) et g++ pour la syntaxe des scripts Unity et
Unreal, le validateur glTF officiel de Khronos (`APEX_GLTF_VALIDATOR_DIR` : dossier où
`npm install gltf-validator` a été lancé). Sans eux, le test correspondant est sauté (et le dit) ; il
n'est jamais remplacé par une simulation.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from apex.brain import mesh3d as m3
from apex.brain import studio3d
from apex.brain.gamecode import unity_script, unreal_class

API = Path(__file__).parent / "data" / "game_api_min"


def _all_models() -> list[m3.Model]:
    return [
        *(m3.vehicle(k) for k in ("car", "truck", "tank", "motorbike")),
        *(m3.weapon(k) for k in ("sword", "axe", "rifle", "pistol")),
        *(m3.character(build=b) for b in ("slim", "strong")),
    ]


def _glb_document(glb: bytes) -> dict:
    length, kind = struct.unpack("<I4s", glb[12:20])
    assert kind == b"JSON"
    return json.loads(glb[20 : 20 + length])


def test_calls_are_parsed_safely_and_summaries_tell_the_real_dimensions() -> None:
    assert studio3d.run('vehicle("tank", length=7, color="kaki")') == (
        "char 7 × 3,4 × 2,4 m, 10 galets — 16 pièces, 816 triangles, kaki"  # noqa: RUF001
    )
    assert studio3d.run('character(height=1.9, build="strong")') == (
        "personnage 1,9 m, costaud — 16 pièces, 628 triangles, squelette de 20 os"
    )
    assert studio3d.run('weapon(kind="axe")').startswith("hache 0,8 m")
    # Hors bornes : le modèle est construit aux bornes, et c'est DIT.
    assert studio3d.run('vehicle("tank", length=100)').endswith("(length ramené à 30)")
    for refused in (
        '__import__("os").system("ls")',
        'vehicle("tank", length=open("x"))',
        'vehicle("car", speed=3)',
        'vehicle("car", **{"length": 3})',
        'vehicle("car", color="fuchsia")',
        'vehicle("car", length=True)',
        'vehicle("sous-marin")',
        'weapon("sword", "axe")',
        "vehicle(" + "1, " * 200 + ")",
    ):
        assert studio3d.run(refused).startswith("erreur : "), refused
    assert studio3d.is_call(' vehicle ("car")') and not studio3d.is_call("sorted([3, 1])")
    call = studio3d.parse('vehicle("car", color="#4B5320", wheels=6)')
    assert call.arguments() == {"generator": "vehicle", "kind": "car", "color": "#4b5320", "wheels": 6}
    assert studio3d.parse(call.expression()) == call  # l'expression normalisée se relit à l'identique


def test_files_for_blender_unity_unreal_are_complete_and_consistent() -> None:
    model = studio3d.build(studio3d.parse('character(height=2, build="strong")'))
    files = dict(studio3d.model_files(model, "heros-2"))
    assert set(files) == {
        "heros-2.glb",
        "heros-2.obj",
        "heros-2.mtl",
        "heros-2_blender.py",
        "heros-2.png",
        "ApexHeros2Character.cs",
        "ApexHeros2Character.h",
        "ApexHeros2Character.cpp",
        "heros-2_LISEZ-MOI.txt",
    }
    assert files["heros-2.png"].startswith(b"\x89PNG")
    assert b"mtllib heros-2.mtl" in files["heros-2.obj"]
    # OBJ : Y vers le haut (convention du format) — la hauteur du personnage est sur Y.
    ys = [
        float(line.split()[2])
        for line in files["heros-2.obj"].decode().splitlines()
        if line.startswith("v ")
    ]
    assert abs(max(ys) - min(ys) - 2.0) < 0.01
    # glTF : un os par nœud, peau complète, chaque pièce liée (JOINTS_0/WEIGHTS_0) à l'os qui la porte.
    document = _glb_document(files["heros-2.glb"])
    skin = document["skins"][0]
    assert len(skin["joints"]) == 20 and document["accessors"][skin["inverseBindMatrices"]]["count"] == 20
    assert all(
        {"JOINTS_0", "WEIGHTS_0"} <= set(m["primitives"][0]["attributes"]) for m in document["meshes"]
    )
    assert all(n.get("skin") == 0 for n in document["nodes"] if "mesh" in n)
    joint_names = [document["nodes"][j]["name"] for j in skin["joints"]]
    assert joint_names == [b.name for b in model.bones] and set(model.skin.values()) <= set(joint_names)
    # Véhicule : pas de squelette, donc pas de peau.
    assert "skins" not in _glb_document(m3.to_glb(m3.vehicle("tank")))


def _csc() -> list[str] | None:
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        return None
    sdk = Path(dotnet).resolve().parent
    compilers = sorted((sdk / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    runtimes = sorted((sdk / "shared" / "Microsoft.NETCore.App").glob("*"))
    if not compilers or not runtimes:
        return None
    refs = [
        f"-r:{runtimes[-1] / d}"
        for d in ("System.Runtime.dll", "System.Collections.dll", "System.Private.CoreLib.dll")
    ]
    return ["dotnet", str(compilers[-1]), "-nologo", "-target:library", "-warnaserror+", *refs]


def test_unity_scripts_compile_with_the_real_csharp_compiler(tmp_path) -> None:
    """Syntaxe et cohérence des scripts Unity, prouvées par le vrai compilateur C# (avertissement = erreur),
    contre une déclaration MINIMALE de l'API Unity (tests/data/game_api_min) — pas contre Unity lui-même."""
    csc = _csc()
    if csc is None:
        pytest.skip("compilateur C# (dotnet) absent")
    sources = []
    for i, model in enumerate(_all_models()):
        name, code = unity_script(model, f"modele-{i}")
        assert (
            f"public class {name.removesuffix('.cs')} : MonoBehaviour" in code
        )  # Unity : fichier = classe
        (tmp_path / name).write_text(code, encoding="utf-8")
        sources.append(str(tmp_path / name))
    done = subprocess.run(
        [*csc, f"-out:{tmp_path / 'jeu.dll'}", str(API / "UnityEngine.cs"), *sources],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    # Contrôle négatif : une faute dans un script est bien refusée (la vérification n'est pas formelle).
    broken = Path(sources[0])
    broken.write_text(
        broken.read_text(encoding="utf-8").replace("MoveRotation", "MoveRotaton"), encoding="utf-8"
    )
    failed = subprocess.run(
        [*csc, f"-out:{tmp_path / 'faux.dll'}", str(API / "UnityEngine.cs"), *sources],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert failed.returncode != 0 and "MoveRotaton" in failed.stdout


def test_unreal_classes_pass_the_real_cpp_syntax_check(tmp_path) -> None:
    """Syntaxe C++ des classes Unreal (g++ -fsyntax-only, -Wall -Wextra) contre une déclaration MINIMALE de
    l'API Unreal (tests/data/game_api_min/unreal) — pas contre Unreal Engine lui-même."""
    if shutil.which("g++") is None:
        pytest.skip("g++ absent")
    checked = []
    for i, model in enumerate(_all_models()):
        for name, code in unreal_class(model, f"modele-{i}"):
            (tmp_path / name).write_text(code, encoding="utf-8")
            if name.endswith(".h"):
                (tmp_path / name.replace(".h", ".generated.h")).write_text("", encoding="utf-8")
            else:
                checked.append(tmp_path / name)
    command = [
        "g++",
        "-std=c++20",
        "-fsyntax-only",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-unused-parameter",
        "-I",
        str(API / "unreal"),
        "-I",
        str(tmp_path),
    ]
    for source in checked:
        done = subprocess.run(
            [*command, str(source)], capture_output=True, text=True, timeout=120, check=False
        )
        assert done.returncode == 0, source.name + "\n" + done.stderr
    source = checked[0]
    source.write_text(
        source.read_text(encoding="utf-8").replace("GetActorForwardVector", "GetActorForwardVectr"),
        encoding="utf-8",
    )
    failed = subprocess.run(
        [*command, str(source)], capture_output=True, text=True, timeout=120, check=False
    )
    assert failed.returncode != 0 and "GetActorForwardVectr" in failed.stderr


_POSE = """
import json, math
import bpy
rig = next(o for o in bpy.data.objects if o.type == "ARMATURE")
def center(name):
    obj = bpy.data.objects[name]
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    count = len(mesh.vertices)
    point = [sum((obj.matrix_world @ v.co)[i] for v in mesh.vertices) / count for i in range(3)]
    evaluated.to_mesh_clear()
    return point
before = {n: center(n) for n in ("main_G", "tete", "pied_D")}
rig.pose.bones["bras_G"].rotation_mode = "XYZ"
rig.pose.bones["bras_G"].rotation_euler = (0, 0, math.radians(90))
bpy.context.view_layer.update()
print("APEX_POSE " + json.dumps({n: math.dist(before[n], center(n)) for n in before}))
"""

_IMPORT = """
import json, sys
import bpy
path = sys.argv[sys.argv.index("--") + 1]
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)
(bpy.ops.import_scene.fbx if path.endswith(".fbx") else bpy.ops.import_scene.gltf)(filepath=path)
rigs = [o for o in bpy.data.objects if o.type == "ARMATURE"]
# L'importeur glTF de Blender ajoute une forme d'os (« Icosphere ») dans une collection non exportée.
def exported(o):
    return "glTF_not_exported" not in [c.name for c in o.users_collection]
meshes = [o for o in bpy.data.objects if o.type == "MESH" and exported(o)]
skinned = [o for o in meshes if any(m.type == "ARMATURE" and m.object is not None for m in o.modifiers)]
triangles = sum(len(p.vertices) - 2 for o in meshes for p in o.data.polygons)
print("APEX_IMPORT " + json.dumps({"bones": len(rigs[0].data.bones) if rigs else 0, "meshes": len(meshes),
      "skinned": len(skinned), "triangles": triangles}))
"""


def _blender(*args: str) -> str:
    done = subprocess.run(
        ["blender", "-b", "--factory-startup", *args],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert "Traceback" not in done.stdout + done.stderr, done.stdout[-3000:] + done.stderr[-3000:]
    return done.stdout


def _tagged(output: str, tag: str) -> dict:
    return json.loads(next(x for x in output.splitlines() if x.startswith(tag + " ")).split(" ", 1)[1])


def test_blender_poses_the_skeleton_and_exports_rigged_fbx_and_gltf(tmp_path) -> None:
    """Le VRAI Blender : tourner l'os du bras déplace la main, pas la tête ni le pied ; le FBX (pour Unity)
    et le glTF exportés par le script, et le glTF écrit par APEX lui-même, se réimportent avec 20 os et 16
    pièces liées
    — rien d'autre (le cube de la scène de départ n'est jamais exporté)."""
    if shutil.which("blender") is None:
        pytest.skip("Blender absent de cet environnement")
    model = m3.character(build="strong")
    stats = model.stats()
    script, pose = tmp_path / "perso.py", tmp_path / "pose.py"
    script.write_text(m3.to_blender_script(model), encoding="utf-8")
    pose.write_text(_POSE, encoding="utf-8")
    moved = _tagged(_blender("--python", str(script), "--python", str(pose)), "APEX_POSE")
    assert moved["main_G"] > 0.5 and moved["tete"] < 1e-6 and moved["pied_D"] < 1e-6
    importer = tmp_path / "import.py"
    importer.write_text(_IMPORT, encoding="utf-8")
    (tmp_path / "apex.glb").write_bytes(m3.to_glb(model))
    for output in ("perso.fbx", "perso.glb"):
        _blender("--python", str(script), "--", str(tmp_path / output))
    for output in ("perso.fbx", "perso.glb", "apex.glb"):
        imported = _tagged(_blender("--python", str(importer), "--", str(tmp_path / output)), "APEX_IMPORT")
        assert imported == {
            "bones": 20,
            "meshes": stats["parts"],
            "skinned": stats["parts"],
            "triangles": stats["triangles"],
        }, output


def test_gltf_files_pass_the_official_khronos_validator(tmp_path) -> None:
    folder = os.environ.get("APEX_GLTF_VALIDATOR_DIR")
    if (
        not folder
        or shutil.which("node") is None
        or not (Path(folder) / "node_modules" / "gltf-validator").is_dir()
    ):
        pytest.skip("validateur glTF de Khronos non installé (APEX_GLTF_VALIDATOR_DIR)")
    paths = []
    for i, model in enumerate(_all_models()):
        path = tmp_path / f"m{i}.glb"
        path.write_bytes(m3.to_glb(model))
        paths.append(str(path))
    script = tmp_path / "validate.js"
    script.write_text(
        """const fs = require("fs"); const v = require("gltf-validator");
(async () => { for (const f of process.argv.slice(2)) {
  const r = await v.validateBytes(new Uint8Array(fs.readFileSync(f)));
  console.log(JSON.stringify({ f, errors: r.issues.numErrors, warnings: r.issues.numWarnings,
    codes: r.issues.messages.map((m) => m.code) })); } })();""",
        encoding="utf-8",
    )
    done = subprocess.run(
        ["node", str(script), *paths],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env={**os.environ, "NODE_PATH": str(Path(folder) / "node_modules")},
    )
    reports = [json.loads(line) for line in done.stdout.splitlines()]
    assert len(reports) == len(paths) and all(r["errors"] == 0 and r["warnings"] == 0 for r in reports), (
        reports
    )


def test_3d_requests_teach_the_call_and_unseen_phrasings_never_train() -> None:
    """Données d'instruction 3D : chaque référence est notée juste par le score (le résumé de l'atelier y
    figure), recopier la demande ne rapporte rien, et les formulations comme les combinaisons réservées à
    l'évaluation ne sont jamais montrées à l'entraînement."""
    from apex.brain.chat import CALL, visible_text
    from apex.neural.model3d_tasks import HELDOUT_TEMPLATES, TEMPLATES_EN, TEMPLATES_FR, model3d_examples
    from apex.neural.tasks import score

    evaluation = model3d_examples(20, seed=3, split="eval")
    train = model3d_examples(800, seed=3, split="train", exclude=frozenset(e.user for e in evaluation))
    assert (
        sorted({e.family for e in evaluation}) == ["model3d", "model3d_heldout"] and len(evaluation) == 40
    )
    assert len(train) == 800 and {e.family for e in train} == {"model3d"}
    for example in evaluation + train:
        assert example.assistant.startswith(CALL) and score(example.check, visible_text(example.assistant))
        assert not score(example.check, example.user)  # recopier la demande ne suffit pas
    reserved = (*TEMPLATES_FR[-HELDOUT_TEMPLATES:], *TEMPLATES_EN[-HELDOUT_TEMPLATES:])
    starts = tuple(t.split("{", 1)[0] for t in reserved)
    assert all(starts) and not any(e.user.startswith(starts) for e in train)
    assert all(e.user.startswith(starts) for e in evaluation if e.family == "model3d_heldout")
    assert {e.meta["call"].split("(")[0] for e in train} == {"vehicle", "weapon", "character"}


_ADDON_CHECK = """
import json, math, sys
import bpy
bpy.ops.preferences.addon_install(filepath=sys.argv[sys.argv.index("--") + 1])
bpy.ops.preferences.addon_enable(module="apex_atelier3d")
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)
p = bpy.context.scene.apex_atelier3d
built = []
KINDS = (("vehicle", "tank"), ("weapon", "sword"), ("character", "humanoid"), ("character", "humanoid"))
for generator, kind in KINDS:
    p.generator, p.kind = generator, kind
    p.use_length = generator != "character"
    p.length = 7.0 if kind == "tank" else 1.2
    before = {o.name for o in bpy.data.objects if o.parent is None}
    result = bpy.ops.apex.build_model()
    (root,) = [o for o in bpy.data.objects if o.parent is None and o.name not in before]
    meshes = [o for o in root.children if o.type == "MESH"]
    rigs = [o for o in root.children if o.type == "ARMATURE"]
    built.append({"result": sorted(result), "parts": len(meshes),
                  "triangles": sum(len(f.vertices) - 2 for o in meshes for f in o.data.polygons),
                  "bones": len(rigs[0].data.bones) if rigs else 0,
                  "own_rig": all(any(m.type == "ARMATURE" and m.object == rigs[0] for m in o.modifiers)
                                 for o in meshes) if rigs else None})
rig = next(c for c in root.children if c.type == "ARMATURE")
hand = next(c for c in root.children if c.type == "MESH" and c.data.name.startswith("main_G"))
def center(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    count = len(mesh.vertices)
    point = [sum((obj.matrix_world @ v.co)[i] for v in mesh.vertices) / count for i in range(3)]
    evaluated.to_mesh_clear()
    return point
start = center(hand)
rig.pose.bones["bras_G"].rotation_mode = "XYZ"
rig.pose.bones["bras_G"].rotation_euler = (0, 0, math.radians(90))
bpy.context.view_layer.update()
print("APEX_ADDON " + json.dumps({"built": built, "moved": math.dist(start, center(hand))}))
"""


def test_blender_addon_installs_and_builds_each_kind_with_its_own_rig(tmp_path) -> None:
    """L'extension « APEX Atelier 3D », installée dans le VRAI Blender : chaque construction donne les
    pièces, triangles et os des générateurs d'APEX ; un second personnage dans la même scène est lié à SON
    squelette (le défaut d'une recherche par nom), qui déplace bien sa main."""
    from apex.brain.blender_addon import addon_zip

    if shutil.which("blender") is None:
        pytest.skip("Blender absent de cet environnement")
    archive, check = tmp_path / "apex_atelier3d.zip", tmp_path / "check.py"
    archive.write_bytes(addon_zip())
    check.write_text(_ADDON_CHECK, encoding="utf-8")
    report = _tagged(_blender("--python", str(check), "--", str(archive)), "APEX_ADDON")
    expected = [
        m3.vehicle("tank", length=7.0).stats(),
        m3.weapon("sword", length=1.2).stats(),
        m3.character().stats(),
        m3.character().stats(),
    ]
    for built, stats in zip(report["built"], expected, strict=True):
        assert built["result"] == ["FINISHED"]
        assert (built["parts"], built["triangles"], built["bones"]) == (
            stats["parts"],
            stats["triangles"],
            stats["bones"],
        )
    assert report["built"][2]["own_rig"] is True and report["built"][3]["own_rig"] is True
    assert report["moved"] > 0.5


def test_published_blender_addon_is_the_current_one() -> None:
    """`releases/blender/apex_atelier3d.zip` est exactement l'extension produite par le code actuel (archive
    reproductible) : jamais une version périmée publiée."""
    import hashlib

    from apex.brain.blender_addon import addon_zip

    published = Path(__file__).parents[1] / "releases" / "blender" / "apex_atelier3d.zip"
    current = addon_zip()
    assert published.read_bytes() == current, "régénérer : voir docs/ATELIER_3D.md (extension Blender)"
    digest = (published.parent / "apex_atelier3d.zip.sha256").read_text(encoding="utf-8").split()[0]
    assert digest == hashlib.sha256(current).hexdigest()

"""Extension Blender « APEX Atelier 3D » : les générateurs d'APEX (`apex.brain.mesh3d`) DANS Blender.

Un panneau « APEX » (barre latérale de la vue 3D, touche N) : type, sorte, dimensions, morphologie, couleur,
bouton « Construire ». Le modèle est construit dans la scène, avec les mêmes matériaux, pièces et squelette lié
que le script Blender d'APEX (même code de construction, `mesh3d.BLENDER_BUILD`). Rien d'autre : aucun réseau,
aucun fichier écrit, aucune dépendance hors de Blender.

Installation : Blender > Edit > Preferences > Add-ons > Install… > `apex_atelier3d.zip`, puis cocher
« APEX Atelier 3D ». Vérifié dans Blender 4.0 (installation, construction de chaque sorte, squelette), voir
`tests/test_studio3d.py`.
"""

# Gabarit de l'extension, plus lisible avec ses lignes d'origine (E501).
# ruff: noqa: E501

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from apex.brain import mesh3d

ADDON = "apex_atelier3d"
VERSION = (1, 0, 0)

_INIT = '''"""APEX Atelier 3D : véhicules, armes et personnages paramétriques (générateurs d'APEX)."""

bl_info = {
    "name": "APEX Atelier 3D",
    "author": "APEX",
    "version": %(version)r,
    "blender": (3, 6, 0),
    "location": "Vue 3D > barre latérale (N) > APEX",
    "description": "Véhicules, armes et personnages paramétriques, avec squelette lié pour les personnages",
    "category": "Add Mesh",
}

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty

from . import mesh3d

KINDS = {
    "vehicle": [("car", "Voiture", ""), ("truck", "Camion", ""), ("tank", "Char", ""), ("motorbike", "Moto", "")],
    "weapon": [("sword", "Épée", ""), ("axe", "Hache", ""), ("rifle", "Fusil", ""), ("pistol", "Pistolet", "")],
    "character": [("humanoid", "Personnage", "")],
}
%(build)s

def _kinds(self, context):
    return KINDS[self.generator]


class APEX_Atelier3DSettings(bpy.types.PropertyGroup):
    generator: EnumProperty(
        name="Type",
        items=[("vehicle", "Véhicule", ""), ("weapon", "Arme", ""), ("character", "Personnage", "")],
    )
    kind: EnumProperty(name="Sorte", items=_kinds)
    use_length: BoolProperty(name="Longueur", default=False)
    length: FloatProperty(name="Longueur (m)", default=4.0, min=0.1, max=30.0)
    use_width: BoolProperty(name="Largeur", default=False)
    width: FloatProperty(name="Largeur (m)", default=1.8, min=0.4, max=8.0)
    use_height: BoolProperty(name="Hauteur", default=False)
    height: FloatProperty(name="Hauteur (m)", default=1.8, min=0.5, max=8.0)
    use_wheels: BoolProperty(name="Roues", default=False)
    wheels: IntProperty(name="Roues", default=4, min=2, max=16)
    build: EnumProperty(
        name="Morphologie", items=[("slim", "Mince", ""), ("normal", "Normale", ""), ("strong", "Costaude", "")],
        default="normal",
    )
    use_color: BoolProperty(name="Couleur", default=False)
    color: FloatVectorProperty(name="Couleur", subtype="COLOR", size=3, min=0.0, max=1.0, default=(0.3, 0.36, 0.22))


class APEX_OT_build_model(bpy.types.Operator):
    """Construire le modèle (générateurs d'APEX)"""

    bl_idname = "apex.build_model"
    bl_label = "Construire"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        p = context.scene.apex_atelier3d
        options = {}
        if p.generator == "character":
            if p.use_height:
                options["height"] = p.height
            options["build"] = p.build
        else:
            if p.use_length:
                options["length"] = p.length
            if p.generator == "vehicle":
                for key in ("width", "height", "wheels"):
                    if getattr(p, "use_" + key):
                        options[key] = getattr(p, key)
        if p.use_color:
            options["color"] = tuple(p.color)
        try:
            model = mesh3d.GENERATORS[p.generator](p.kind, **options)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        root, _rig = build_scene(mesh3d.blender_data(model), context.collection)
        root.location = context.scene.cursor.location
        stats = model.stats()
        self.report({"INFO"}, "%%s : %%d pièces, %%d triangles, %%d os" %% (
            model.name, stats["parts"], stats["triangles"], stats["bones"]))
        return {"FINISHED"}


class APEX_PT_atelier3d(bpy.types.Panel):
    bl_label = "Atelier 3D"
    bl_idname = "APEX_PT_atelier3d"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "APEX"

    def draw(self, context):
        p = context.scene.apex_atelier3d
        layout = self.layout
        layout.prop(p, "generator")
        layout.prop(p, "kind")
        if p.generator == "character":
            row = layout.row()
            row.prop(p, "use_height", text="")
            row.prop(p, "height")
            layout.prop(p, "build")
        else:
            row = layout.row()
            row.prop(p, "use_length", text="")
            row.prop(p, "length")
            if p.generator == "vehicle":
                for key in ("width", "height", "wheels"):
                    row = layout.row()
                    row.prop(p, "use_" + key, text="")
                    row.prop(p, key)
        row = layout.row()
        row.prop(p, "use_color", text="")
        row.prop(p, "color")
        layout.operator("apex.build_model", icon="MESH_CUBE")


_CLASSES = (APEX_Atelier3DSettings, APEX_OT_build_model, APEX_PT_atelier3d)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.apex_atelier3d = PointerProperty(type=APEX_Atelier3DSettings)


def unregister():
    del bpy.types.Scene.apex_atelier3d
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
'''


def addon_files() -> dict[str, str]:
    """{chemin dans l'archive : contenu} — `__init__.py` (interface) et `mesh3d.py` (copie des générateurs)."""
    source = Path(mesh3d.__file__).read_text(encoding="utf-8")
    init = _INIT % {"version": VERSION, "build": mesh3d.BLENDER_BUILD}
    return {f"{ADDON}/__init__.py": init, f"{ADDON}/mesh3d.py": source}


def addon_zip() -> bytes:
    """L'extension prête à installer (Blender > Preferences > Add-ons > Install…)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(addon_files().items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))  # archive reproductible
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content.encode("utf-8"))
    return buffer.getvalue()

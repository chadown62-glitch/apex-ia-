"""Read actual GLB bytes and the pinned JSON contract through the existing Workspace."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from typing import Any

from jsonschema import Draft202012Validator

from apex.game_engineer.contract import CONTRACT_SCHEMA_SHA256, WHEELS
from apex.tools.workspace import Workspace


def decode_glb(data: bytes) -> dict[str, Any]:
    if len(data) < 20 or struct.unpack_from("<4sII", data) != (b"glTF", 2, len(data)):
        raise ValueError("invalid_glb_header_or_length")
    cursor, gltf, binary = 12, None, None
    while cursor < len(data):
        if cursor + 8 > len(data):
            raise ValueError("truncated_glb_chunk")
        length, kind = struct.unpack_from("<I4s", data, cursor)
        cursor += 8
        if length % 4 or cursor + length > len(data):
            raise ValueError("invalid_glb_chunk_length")
        if kind == b"JSON":
            if gltf is not None or cursor != 20:
                raise ValueError("invalid_glb_json_order")
            gltf = json.loads(data[cursor : cursor + length])
        elif kind == b"BIN\x00":
            if binary is not None:
                raise ValueError("duplicate_glb_buffer")
            binary = data[cursor : cursor + length]
        else:
            raise ValueError("unsupported_glb_chunk")
        cursor += length
    if not isinstance(gltf, dict) or binary is None or gltf.get("asset", {}).get("version") != "2.0":
        raise ValueError("glb_embedded_mesh_required")
    buffers = gltf.get("buffers", [])
    if len(buffers) != 1 or "uri" in buffers[0] or not 0 < buffers[0]["byteLength"] <= len(binary):
        raise ValueError("external_or_invalid_glb_buffer")
    for view in gltf.get("bufferViews", []):
        if (
            view.get("buffer", 0) != 0
            or view.get("byteOffset", 0) < 0
            or view.get("byteOffset", 0) + view["byteLength"] > buffers[0]["byteLength"]
        ):
            raise ValueError("invalid_glb_buffer_view")
    return gltf


def inspect_bundle(workspace: Workspace, folder: str) -> dict[str, Any]:
    schema_bytes = workspace.read_bytes(folder + "/vehicle-asset-v1.schema.json")
    if hashlib.sha256(schema_bytes).hexdigest() != CONTRACT_SCHEMA_SHA256:
        raise ValueError("unagreed_vehicle_schema")
    manifest = json.loads(workspace.read_bytes(folder + "/manifest.json"))
    Draft202012Validator(json.loads(schema_bytes)).validate(manifest)
    entries = manifest["files"]
    paths = [f["path"] for f in entries]
    if len(set(paths)) != len(paths) or not {
        "vehicle.glb",
        "vehicle.blend",
        "spec.json",
        "vehicle-asset-v1.schema.json",
    }.issubset(paths):
        raise ValueError("incomplete_or_duplicate_asset_entries")
    verified = {}
    for entry in entries:
        data = workspace.read_bytes(folder + "/" + entry["path"])
        if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["bytes"]:
            raise ValueError("asset_integrity_failed:" + entry["path"])
        verified[entry["path"]] = data
    if json.loads(verified["spec.json"]) != manifest["spec"]:
        raise ValueError("spec_file_mismatch")
    gltf = decode_glb(verified["vehicle.glb"])
    nodes = gltf.get("nodes", [])
    parents = {child for node in nodes for child in node.get("children", [])}
    wheel_nodes = {}
    if len(manifest["wheels"]) != 4 or {w["object"] for w in manifest["wheels"]} != set(WHEELS):
        raise ValueError("four_distinct_wheels_required")
    for wheel in manifest["wheels"]:
        matches = [(i, node) for i, node in enumerate(nodes) if node.get("name") == wheel["object"]]
        if len(matches) != 1 or "mesh" not in matches[0][1] or matches[0][0] in parents:
            raise ValueError("independent_root_wheel_required:" + wheel["object"])
        _, node = matches[0]
        x, y, z = wheel["center_m"]
        expected_glb = [x, z, -y]
        position = node.get("translation", [0, 0, 0])
        if (
            len(position) != 3
            or not all(math.isfinite(v) for v in position)
            or math.dist(position, expected_glb) > 0.002
        ):
            raise ValueError("glb_pivot_mismatch:" + wheel["object"])
        wheel_nodes[wheel["object"]] = {"glb_pivot_m": position, "blender_pivot_m": [x, y, z]}
    primitives = [p for mesh in gltf.get("meshes", []) for p in mesh.get("primitives", [])]
    if not primitives or any(
        not {"POSITION", "NORMAL", "TEXCOORD_0"}.issubset(p.get("attributes", {})) or "material" not in p
        for p in primitives
    ):
        raise ValueError("glb_normals_uv_material_required")
    return {
        "status": "verified_intake",
        "manifest": manifest,
        "manifest_sha256": hashlib.sha256(workspace.read_bytes(folder + "/manifest.json")).hexdigest(),
        "verified_files": len(entries),
        "mesh_nodes": len(gltf.get("meshes", [])),
        "materials": len(gltf.get("materials", [])),
        "wheel_nodes": wheel_nodes,
        "engine_import_verified": False,
        "handedness_in_unreal_verified": False,
        "limits": "GLB byte/pivot checks do not qualify Unreal import, bones, collisions or physics",
    }

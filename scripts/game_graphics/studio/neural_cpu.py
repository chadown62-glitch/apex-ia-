"""Fixed worker for pinned TripoSR inference. Invoked only by the APEX CPU wrapper.

Model implementation is upstream MIT code with documented compatibility changes.
This worker never trains or executes code supplied by an LLM.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import time
import traceback
from pathlib import Path
from typing import Any

WEIGHTS_SHA256 = "429e2c6b22a0923967459de24d67f05962b235f79cde6b032aa7ed2ffcd970ee"
CONFIG_SHA256 = "74ca708ce086bf68e97709ea6b3d91f14717921c04691e84043f0eb8fcc68e62"
DINO_CONFIG_SHA256 = "b87c0270b97db085fd82cf114a761fd0f62ae7914fbd407c752a2260646b689c"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def peak_rss_bytes() -> int | None:
    if os.name != "posix":
        return None
    import resource
    import sys

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fixed CPU-only TripoSR worker")
    for name in ("source", "weights", "config", "dino-config", "input", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--resolution", type=int, choices=(32, 64, 128), default=64)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(path.name not in ("worker.log", "provenance.json") for path in output.iterdir()):
        raise ValueError("worker_output_contains_existing_artifacts")
    started = time.monotonic()
    report: dict[str, Any] = {
        "status": "RUNNING",
        "method": "neural_single_image_to_triplane_to_mesh",
        "device": "cpu",
        "cuda_used": False,
        "threads": 4,
        "resolution": args.resolution,
        "density_threshold": 25.0,
        "renderer_chunk_size": 4096,
        "stages": {},
        "input": str(args.input.resolve()),
    }

    def save() -> None:
        report["elapsed_seconds"] = time.monotonic() - started
        report["self_peak_rss_bytes"] = peak_rss_bytes()
        temporary = output / "inference.json.part"
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(output / "inference.json")

    def stage(name: str, began: float) -> None:
        report["stages"][name] = {
            "seconds": time.monotonic() - began,
            "self_peak_rss_bytes": peak_rss_bytes(),
        }
        save()
        print(name, json.dumps(report["stages"][name]), flush=True)

    try:
        began = time.monotonic()
        for path, expected in (
            (args.weights, WEIGHTS_SHA256),
            (args.config, CONFIG_SHA256),
            (args.dino_config, DINO_CONFIG_SHA256),
        ):
            if sha256(path) != expected:
                raise ValueError("pinned_model_data_mismatch:" + path.name)
        os.environ["APEX_TRIPOSR_DINO_CONFIG"] = str(args.dino_config.resolve())
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import numpy as np
        import torch
        import tsr.system
        from omegaconf import OmegaConf
        from PIL import Image
        from tsr.system import TSR
        from tsr.utils import resize_foreground

        if Path(tsr.system.__file__).resolve() != args.source.resolve() / "tsr/system.py":
            raise ValueError("unexpected_model_implementation_loaded")
        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        torch.manual_seed(42)
        report["torch_cuda_available"] = torch.cuda.is_available()
        report["cpu_selected_even_if_cuda_available"] = True
        report["packages"] = {
            name: importlib.metadata.version(name)
            for name in (
                "torch",
                "transformers",
                "trimesh",
                "numpy",
                "Pillow",
                "einops",
                "omegaconf",
            )
        }
        report.update(
            {
                "weights_repository": "stabilityai/TripoSR",
                "weights_revision": "5b521936b01fbe1890f6f9baed0254ab6351c04a",
                "weights_sha256": WEIGHTS_SHA256,
                "weights_license": "MIT",
                "input_sha256": sha256(args.input),
            }
        )
        with Image.open(args.input) as opened:
            opened.load()
            image = opened.copy()
        if image.width > 4096 or image.height > 4096:
            raise ValueError("input_image_dimension_budget")
        if image.mode == "RGBA" and image.getextrema()[3][0] < 255:
            if image.getextrema()[3][1] == 0:
                raise ValueError("input_has_no_visible_foreground")
            image = resize_foreground(image, 0.85)
            pixels = np.array(image, dtype=np.float32) / 255
            rgb = pixels[:, :, :3] * pixels[:, :, 3:4] + (1 - pixels[:, :, 3:4]) * 0.5
            image = Image.fromarray(np.rint(rgb * 255).astype(np.uint8))
            report["preprocessing"] = (
                "upstream resize_foreground 0.85; alpha composite neutral grey; no segmentation model"
            )
        else:
            image = image.convert("RGB")
            report["preprocessing"] = "RGB conversion only; input background retained"
        image.save(output / "model-input.png")
        report["model_input_sha256"] = sha256(output / "model-input.png")
        stage("imports_hash_preprocess", began)
        began = time.monotonic()
        cfg = OmegaConf.load(args.config)
        OmegaConf.resolve(cfg)
        model = TSR(cfg)
        state = torch.load(args.weights, map_location="cpu", weights_only=True)
        model.load_state_dict(state, strict=True)
        del state
        model = model.to("cpu").eval()
        model.renderer.set_chunk_size(4096)
        stage("model_load", began)
        began = time.monotonic()
        with torch.inference_mode():
            codes = model([image], device="cpu")
        torch.save(codes.detach().cpu(), output / "scene-codes.pt")
        stage("neural_forward", began)
        began = time.monotonic()
        with torch.inference_mode():
            mesh = model.extract_mesh(codes, has_vertex_color=True, resolution=args.resolution)[0]
        if not len(mesh.vertices) or not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
            raise ValueError("empty_or_nonfinite_neural_mesh")
        stage("neural_mesh_extraction", began)
        began = time.monotonic()
        mesh.export(output / "neural-mesh.glb")
        mesh.export(output / "neural-mesh.ply")
        stage("export", began)
        report["mesh"] = {
            "vertices": len(mesh.vertices),
            "triangles": len(mesh.faces),
            "watertight_raw": bool(mesh.is_watertight),
            "bounds": mesh.bounds.tolist(),
            "vertex_color_count": len(mesh.visual.vertex_colors),
            "glb_sha256": sha256(output / "neural-mesh.glb"),
            "ply_sha256": sha256(output / "neural-mesh.ply"),
        }
        report["status"] = "INFERENCE_EXECUTED_QUALITY_UNQUALIFIED"
        report["limitations"] = [
            "Single image hides back-side geometry; no fidelity guarantee",
            "Vertex colours only, no predicted PBR or baked UV textures",
            "No training or weight update performed",
            (
                "Raw topology may contain nearly coincident seams; "
                "independent inspector metrics state their weld tolerance"
            ),
            (
                "Native Blender views and independent visual inspection required; "
                "runtime success does not establish beauty"
            ),
        ]
        return 0
    except BaseException as exc:
        report["status"] = "FAILED"
        report["error"] = type(exc).__name__ + ": " + str(exc)
        report["traceback"] = traceback.format_exc()
        raise
    finally:
        save()


if __name__ == "__main__":
    raise SystemExit(main())

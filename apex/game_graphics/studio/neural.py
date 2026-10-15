"""Bounded, pinned TripoSR CPU experiment using the existing APEX resource lease.

The model source must have the documented three-line compatibility adaptations.
Dependency directories are operator-controlled installations, never LLM code.
This is local trusted execution, not an operating-system sandbox.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import psutil

from apex.game_graphics.resources import resource_lease

CODE_REVISION = "107cefdc244c39106fa830359024f6a2f1c78871"
WEIGHTS_REVISION = "5b521936b01fbe1890f6f9baed0254ab6351c04a"
WEIGHTS_SHA256 = "429e2c6b22a0923967459de24d67f05962b235f79cde6b032aa7ed2ffcd970ee"
CONFIG_SHA256 = "74ca708ce086bf68e97709ea6b3d91f14717921c04691e84043f0eb8fcc68e62"
DINO_CONFIG_SHA256 = "b87c0270b97db085fd82cf114a761fd0f62ae7914fbd407c752a2260646b689c"
PATCHED_SOURCE_SHA256 = {
    "tsr/bake_texture.py": "931d066c25d5943dc7b71469b63c308df3f37b8e7f6df558ee5e348d7936d5b7",
    "tsr/models/isosurface.py": "94c9a2ec27f8fe3c2fb57558ef28be253eb77074d2c171d49a41cf73d95d6f5e",
    "tsr/models/nerf_renderer.py": "afe8c12c964078b2c6bb3922d2e30969aa59f7edbacb8cb73d2c3df839dc785f",
    "tsr/models/network_utils.py": "b3d48e86be9ca3f3abc1bfd774f640fd350e4365843d804e2a3145e7692e1419",
    "tsr/models/tokenizers/image.py": "0ca9b3a1f86a80a8c83d9236468e6e5f4e57e7861fb6733f8290277879843f09",
    "tsr/models/tokenizers/triplane.py": "44811fbd53013d6f49e229ecfd0717f89dd8ebeb71608aeb1f8e5ed1d5e8e378",
    "tsr/models/transformer/attention.py": (
        "347c1a707257cb4eb664ebe9ecd16db7b6006131e74925c049245d2c0101ba7e"
    ),
    "tsr/models/transformer/basic_transformer_block.py": (
        "ced8ce4c5c5bbc13368130bc15dee1717cc6bc7f35699f64d03aeec185b1198e"
    ),
    "tsr/models/transformer/transformer_1d.py": (
        "fd83a71d59d04ef0a45dbadcc63f9d56d354ebc011a40f5589eb1786a1dd15f4"
    ),
    "tsr/system.py": "a82d7cc65b2d7867c322fe3e54cfa92ed23fc5d2706189f00a86395b8dbe5957",
    "tsr/utils.py": "ea8c04ed09c9306cd95c05db227900ad2c940f3f9721a9b23ebcd42ca002dc86",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_inputs(source: Path, weights: Path, config: Path, dino: Path) -> dict[str, Any]:
    """Verify the actual clone revision, all imported source files and all model data."""
    revision = subprocess.run(  # noqa: S603 - fixed git query, no shell
        [shutil.which("git") or "/usr/bin/git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    ).stdout.strip()
    if revision != CODE_REVISION:
        raise ValueError("triposr_source_revision_mismatch")
    actual = {
        str(path.relative_to(source)): file_sha256(path) for path in sorted((source / "tsr").rglob("*.py"))
    }
    if actual != PATCHED_SOURCE_SHA256:
        raise ValueError("triposr_source_or_documented_patch_mismatch")
    for path, expected in (
        (weights, WEIGHTS_SHA256),
        (config, CONFIG_SHA256),
        (dino, DINO_CONFIG_SHA256),
    ):
        if file_sha256(path) != expected:
            raise ValueError("triposr_data_sha256_mismatch:" + path.name)
    return {
        "code_revision": revision,
        "code_source": "https://github.com/VAST-AI-Research/TripoSR",
        "weights_repository": "stabilityai/TripoSR",
        "weights_revision": WEIGHTS_REVISION,
        "weights_sha256": WEIGHTS_SHA256,
        "config_sha256": CONFIG_SHA256,
        "dino_config_sha256": DINO_CONFIG_SHA256,
        "license": "MIT",
        "source_files": actual,
        "patches": [
            "Optional rembg import; authored RGBA input needs no segmentation model",
            "ViT configuration loaded from the pinned local DINO JSON",
            "torch.load explicitly weights_only=True",
        ],
    }


def stop_owned(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        return
    parent = psutil.Process(process.pid)
    children = parent.children(recursive=True)
    for item in [*children, parent]:
        with suppress(psutil.NoSuchProcess):
            item.terminate()
    _, alive = psutil.wait_procs([*children, parent], timeout=10)
    for item in alive:
        item.kill()
    process.wait(timeout=10)


def run_device(args: argparse.Namespace, *, device: str, worker: Path | None = None) -> dict[str, Any]:
    """Run the fixed worker, never Python supplied by a generated plan."""
    if device not in ("cpu", "cuda"):
        raise ValueError("neural_device_must_be_explicit")
    if device == "cuda":
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("cuda_required_no_implicit_cpu_fallback")
    source = args.source.resolve(strict=True)
    weights = args.weights.resolve(strict=True)
    if weights.is_dir():
        weights = weights / "model.ckpt"
    config = args.config.resolve(strict=True)
    dino = (args.dino_config or weights.parent / "dino-config.json").resolve(strict=True)
    image = args.input.resolve(strict=True)
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("neural_output_must_be_new")
    normalized = str(output.resolve()).replace("\\", "/").lower()
    if "c:/gameproject" in normalized or normalized.startswith("/mnt/c/gameproject"):
        raise ValueError("personal_project_protected")
    if len(output.resolve().parts) < 3:
        raise ValueError("neural_output_too_broad")
    if args.resolution not in (32, 64, 128):
        raise ValueError("bounded_marching_cubes_resolution_required")
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 720:
        raise ValueError("cpu_timeout_must_be_1_to_720_seconds")
    if not math.isfinite(args.rss) or not 512 <= args.rss <= 20480:
        raise ValueError("cpu_rss_must_be_512_to_20480_MiB")
    deps = [path.resolve(strict=True) for path in args.deps]
    if not all(path.is_dir() for path in deps):
        raise ValueError("trusted_dependency_directories_required")
    worker = worker or Path(__file__).resolve().parents[3] / "scripts/game_graphics/studio/neural_cpu.py"
    if not worker.is_file():
        raise ValueError("fixed_neural_worker_missing")
    facts = verify_inputs(source, weights, config, dino)
    facts.update({"input_sha256": file_sha256(image), "worker_sha256": file_sha256(worker)})
    env = dict(os.environ)
    env.update(
        {
            "APEX_TRIPOSR_DINO_CONFIG": str(dino),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "OMP_NUM_THREADS": "4",
            "MKL_NUM_THREADS": "4",
            "OPENBLAS_NUM_THREADS": "4",
            "PYTHONPATH": os.pathsep.join(map(str, [source, *deps])),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    argv = [
        sys.executable,
        str(worker),
        "--source",
        str(source),
        "--weights",
        str(weights),
        "--config",
        str(config),
        "--dino-config",
        str(dino),
        "--input",
        str(image),
        "--output",
        str(output),
        "--resolution",
        str(args.resolution),
        "--device",
        device,
    ]
    receipt: dict[str, Any] = {
        "method": "neural_triposr_" + device,
        "device": device,
        "threads": 4,
        "resolution": args.resolution,
        "timeout_seconds": args.timeout,
        "rss_limit_bytes": int(args.rss * 1024**2),
        "samples": [],
        "provenance": facts,
        "isolation": "trusted_local_process_not_os_sandbox",
        "inference_executed": False,
    }
    # Same shared APEX_HOME lock used by Qwen and Blender; no competing agent scheduler.
    with resource_lease(args.runtime.resolve() / "resource.lock"):
        output.mkdir(parents=True, exist_ok=False)
        (output / "provenance.json").write_text(json.dumps(facts, indent=2) + "\n")
        started = time.monotonic()
        process: subprocess.Popen[Any] | None = None
        try:
            with (output / "worker.log").open("w") as log:
                process = subprocess.Popen(  # noqa: S603 - verified source, fixed worker, no LLM code
                    argv,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=(os.name == "posix"),
                )
                receipt["pid"] = process.pid
                while process.poll() is None:
                    rss = 0
                    try:
                        parent = psutil.Process(process.pid)
                        for item in [parent, *parent.children(recursive=True)]:
                            with suppress(psutil.NoSuchProcess):
                                rss += item.memory_info().rss
                    except psutil.NoSuchProcess:
                        pass
                    elapsed = time.monotonic() - started
                    receipt["samples"].append({"seconds": elapsed, "rss_bytes": rss})
                    if elapsed > args.timeout or rss > receipt["rss_limit_bytes"]:
                        receipt["stop_reason"] = "TIMEOUT" if elapsed > args.timeout else "RSS_LIMIT"
                        break
                    time.sleep(0.25)
        except BaseException as exc:
            receipt["error"] = type(exc).__name__ + ": " + str(exc)
            raise
        finally:
            if process is not None:
                stop_owned(process)
            receipt.update(
                {
                    "elapsed_seconds": time.monotonic() - started,
                    "return_code": process.returncode if process else None,
                    "owned_process_stopped": process is None or process.poll() is not None,
                    "sampled_peak_rss_bytes": max(
                        (sample["rss_bytes"] for sample in receipt["samples"]),
                        default=0,
                    ),
                }
            )
            report = output / "inference.json"
            if report.is_file():
                try:
                    payload = json.loads(report.read_text())
                    receipt["inference_executed"] = "neural_forward" in payload.get("stages", {})
                    receipt["worker_status"] = payload.get("status")
                except (ValueError, TypeError, AttributeError):
                    receipt["worker_report_unreadable"] = True
            receipt["mesh_created"] = (output / "neural-mesh.glb").is_file()
            receipt["success"] = (
                receipt["return_code"] == 0
                and not receipt.get("stop_reason")
                and receipt["inference_executed"]
                and receipt["mesh_created"]
                and receipt.get("worker_status") == "INFERENCE_EXECUTED_QUALITY_UNQUALIFIED"
            )
            (output / "resources.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def run_cpu(args: argparse.Namespace, *, worker: Path | None = None) -> dict[str, Any]:
    """PR17 CPU evidence path, retained only when CPU is chosen explicitly."""
    return run_device(args, device="cpu", worker=worker)


def run_gpu(args: argparse.Namespace, *, worker: Path | None = None) -> dict[str, Any]:
    """GPU-first path; its performance remains unqualified until an actual CUDA run."""
    return run_device(args, device="cuda", worker=worker)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pinned TripoSR; CUDA default, CPU requires explicit choice; no downloads."
    )
    for name in ("source", "weights", "config", "input", "output", "runtime"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument(
        "--deps",
        type=Path,
        nargs="+",
        required=True,
        help="Operator-controlled dependency/module/native-extension directories",
    )
    parser.add_argument("--dino-config", type=Path)
    parser.add_argument("--resolution", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=720)
    parser.add_argument("--rss", type=float, default=20480, help="Sampled RSS cap in MiB")
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()
    result = run_device(args, device=args.device)
    print(
        json.dumps(
            {key: value for key, value in result.items() if key not in ("samples", "provenance")},
            indent=2,
        )
    )
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

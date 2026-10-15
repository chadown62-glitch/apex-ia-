"""Own a real llama.cpp process; reuse APEX's ModelProvider and verify pinned GGUF."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import psutil

from apex.config.schema import ModelEndpoint
from apex.models.provider import get_default_provider

CODER_REPO = "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
CODER_COMMIT = "13fb94bfda8c8cf22497dc57b78f391a9acb426a"
CODER_FILE = "qwen2.5-coder-7b-instruct-q5_k_m.gguf"
CODER_SHA256 = "586844eac4d6d6321689f0192c8aa8e69cd8625974a5cc2d925b1a03366e4d16"
CODER_LICENSE_SHA256 = "832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e"


def fetch_coder(runtime: Path) -> Path:
    """Explicit setup only: check the immutable license before downloading weights."""
    from huggingface_hub import hf_hub_download

    url = f"https://huggingface.co/{CODER_REPO}/resolve/{CODER_COMMIT}/LICENSE"
    with urlopen(url, timeout=60) as response:
        license_text = response.read(262144)
    if hashlib.sha256(license_text).hexdigest() != CODER_LICENSE_SHA256:
        raise RuntimeError("coder_license_changed_review_required")
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "Qwen-Apache-2.0.txt").write_bytes(license_text)
    path = Path(
        hf_hub_download(
            repo_id=CODER_REPO,
            filename=CODER_FILE,
            revision=CODER_COMMIT,
            cache_dir=str(runtime / "hf-cache"),
        )
    )
    if file_sha256(path) != CODER_SHA256 or path.stat().st_size != 5_444_831_232:
        raise RuntimeError("coder_download_integrity_failed")
    descriptor = runtime / "coder-model.json"
    descriptor.write_text(
        json.dumps(
            {
                "repo": CODER_REPO,
                "commit": CODER_COMMIT,
                "file": CODER_FILE,
                "path": str(path.resolve()),
                "sha256": CODER_SHA256,
                "bytes": path.stat().st_size,
                "license": "Apache-2.0",
                "license_reviewed_before_download": True,
            },
            indent=2,
        )
    )
    return descriptor


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class CoderSession:
    def __init__(self, manifest: Path, log_dir: Path, *, port: int = 8082,
                 compute_backend: str = "cpu") -> None:
        self.manifest, self.log_dir, self.port = manifest, log_dir, port
        if compute_backend not in ("auto", "cuda", "cpu"):
            raise ValueError("unknown_coder_compute_backend")
        self.compute_backend = compute_backend
        self.process: subprocess.Popen[bytes] | None = None
        self.log: Any = None
        self.facts: dict[str, Any] = {}
        self.endpoint = ModelEndpoint(
            pool_id="local",
            runtime="openai_compatible",
            url=f"http://127.0.0.1:{port}/v1",
            served_model="qwen2.5-coder-7b-instruct-q5",
            max_context=8192,
        )

    def __enter__(self) -> CoderSession:
        from llama_cpp import llama_cpp

        gpu_supported = bool(llama_cpp.llama_supports_gpu_offload())
        if self.compute_backend in ("auto", "cuda"):
            import torch

            if not torch.cuda.is_available() or not gpu_supported:
                raise RuntimeError("cuda_coder_unavailable_choose_cpu_explicitly")
            layers, device = "-1", "cuda"
        else:
            layers, device = "0", "cpu_explicit"
        if not self.manifest.is_file():
            raise RuntimeError("coder_model_missing")
        model = json.loads(self.manifest.read_text())
        path = Path(model["path"])
        if (
            model["repo"] != CODER_REPO
            or model["commit"] != CODER_COMMIT
            or model["sha256"] != CODER_SHA256
            or model["license"] != "Apache-2.0"
            or model.get("license_reviewed_before_download") is not True
            or file_sha256(path) != CODER_SHA256
        ):
            raise RuntimeError("coder_artifact_unverified")
        if get_default_provider(self.endpoint).detect().is_available:
            raise RuntimeError("model_port_already_owned")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log = (self.log_dir / "llama-server.log").open("w")
        started = time.monotonic()
        self.process = subprocess.Popen(  # noqa: S603 - verified local weights and fixed localhost server
            [
                sys.executable,
                "-m",
                "llama_cpp.server",
                "--model",
                str(path),
                "--model_alias",
                self.endpoint.served_model,
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--n_ctx",
                "8192",
                "--n_threads",
                "4",
                "--n_gpu_layers",
                layers,
            ],
            stdout=self.log,
            stderr=subprocess.STDOUT,
            start_new_session=(os.name != "nt"),
        )
        try:
            provider = get_default_provider(self.endpoint)
            while not provider.detect().is_available:
                if self.process.poll() is not None or time.monotonic() - started > 120:
                    raise RuntimeError("local_coder_start_failed")
                time.sleep(0.2)
            self.facts = {
                "repo": CODER_REPO,
                "commit": CODER_COMMIT,
                "sha256": CODER_SHA256,
                "quantization": "Q5_K_M",
                "device": device,
                "llama_gpu_offload_supported": gpu_supported,
                "load_s": time.monotonic() - started,
                "pid": self.process.pid,
                "rss_after_load_bytes": psutil.Process(self.process.pid).memory_info().rss,
            }
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def sample_memory(self) -> None:
        if self.process and self.process.poll() is None:
            self.facts["rss_after_generation_bytes"] = psutil.Process(self.process.pid).memory_info().rss

    def __exit__(self, kind: Any, value: Any, tb: Any) -> None:
        if self.process and self.process.poll() is None:
            if os.name == "nt":
                owned = psutil.Process(self.process.pid)
                tree = [*owned.children(recursive=True), owned]
                for item in tree:
                    with suppress(psutil.NoSuchProcess):
                        item.terminate()
                _, survivors = psutil.wait_procs(tree, timeout=10)
                for item in survivors:
                    with suppress(psutil.NoSuchProcess):
                        item.kill()
            else:
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    self.process.kill()
                else:
                    os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=10)
        if self.process:
            self.facts["process_stopped_verified"] = self.process.poll() is not None
            self.facts["returncode"] = self.process.returncode
        if self.log:
            self.log.close()


def triposr_readiness() -> dict[str, Any]:
    import torch

    return {
        "repo": "stabilityai/TripoSR",
        "commit": "5b521936b01fbe1890f6f9baed0254ab6351c04a",
        "license": "MIT",
        "cuda_available": torch.cuda.is_available(),
        "cuda_devices": torch.cuda.device_count(),
        "status": "blocked" if not torch.cuda.is_available() else "unverified",
        "reason": "GPU qualification/VRAM measurement unavailable; CPU reconstruction not benchmarked",
        "weights_downloaded": False,
        "announced_vram_gb": 6,
        "vram_claim_kind": "upstream claim, not measured",
    }

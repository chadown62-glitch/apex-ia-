"""Own a real llama.cpp process; reuse APEX's ModelProvider and verify pinned GGUF."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
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


def file_sha256(path: Path, *, cancel_event=None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("user_cancelled_verifying_weights")
            h.update(chunk)
    return h.hexdigest()


class CoderSession:
    def __init__(
        self, manifest: Path, log_dir: Path, *, port: int = 8082, compute_backend: str = "cpu"
    ) -> None:
        self.manifest, self.log_dir, self.port = manifest, log_dir, port
        if compute_backend not in ("auto", "cuda", "cpu"):
            raise ValueError("unknown_coder_compute_backend")
        self.compute_backend = compute_backend
        self.process: subprocess.Popen[bytes] | None = None
        self.log: Any = None
        self.facts: dict[str, Any] = {}
        self.cancel_event = None
        self._owned_children: list[psutil.Process] = []
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
            or file_sha256(path, cancel_event=self.cancel_event) != CODER_SHA256
        ):
            raise RuntimeError("coder_artifact_unverified")
        # Reject ANY listener, including a non-LLM service or a server presenting
        # a different model. Never shut it down or reuse it as our Qwen process.
        with socket.socket() as port_check:
            try:
                if os.name == "nt":
                    port_check.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                port_check.bind(("127.0.0.1", self.port))
            except OSError as exc:
                raise RuntimeError("model_port_already_owned") from exc
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise InterruptedError("user_cancelled_before_model_start")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log = (self.log_dir / "llama-server.log").open("w")
        started = time.monotonic()
        self.facts = {
            "repo": CODER_REPO,
            "commit": CODER_COMMIT,
            "sha256": CODER_SHA256,
            "quantization": "Q5_K_M",
            "device": device,
            "llama_gpu_offload_supported": gpu_supported,
            "server_ready": False,
        }
        try:
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
                env={**os.environ, "PYTHONUTF8": "1"},
                start_new_session=(os.name != "nt"),
            )
        except BaseException:
            self.log.close()
            raise
        try:
            provider = get_default_provider(self.endpoint)
            while True:
                if self.cancel_event is not None and self.cancel_event.is_set():
                    raise InterruptedError("user_cancelled_loading_model")
                if self.process.poll() is not None or time.monotonic() - started > 120:
                    raise RuntimeError("local_coder_start_failed")
                if provider.detect().is_available:
                    if self.process.poll() is not None:
                        raise RuntimeError("local_coder_exited_during_start")
                    break
                time.sleep(0.2)
            self.facts = {
                **self.facts,
                "load_s": time.monotonic() - started,
                "server_ready": True,
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

    def request_stop(self, *, force: bool = False) -> None:
        """Snapshot owned descendants before terminating their parent on Windows.

        The cancellation watcher and __exit__ use the same process identities;
        no process search by name and no foreign server termination.
        """
        if self.process is None or self.process.poll() is not None:
            return
        with suppress(psutil.NoSuchProcess):
            children = psutil.Process(self.process.pid).children(recursive=True)
            known = {p.pid for p in self._owned_children}
            self._owned_children.extend(p for p in children if p.pid not in known)
        if os.name == "nt":
            for child in self._owned_children:
                with suppress(psutil.NoSuchProcess):
                    child.kill() if force else child.terminate()
            self.process.kill() if force else self.process.terminate()
        else:
            with suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL if force else signal.SIGTERM)

    def __exit__(self, kind: Any, value: Any, tb: Any) -> None:
        stopped_at = time.monotonic()
        if self.process and self.process.poll() is None:
            self.request_stop()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.request_stop(force=True)
                self.process.wait(timeout=10)
        if self._owned_children:
            _, survivors = psutil.wait_procs(self._owned_children, timeout=2)
            for child in survivors:
                with suppress(psutil.NoSuchProcess):
                    child.kill()
            _, survivors = psutil.wait_procs(survivors, timeout=2)
            self.facts["owned_children_stopped_verified"] = not survivors
        if self.process:
            self.facts["process_stopped_verified"] = self.process.poll() is not None
            self.facts["returncode"] = self.process.returncode
            self.facts["shutdown_s"] = time.monotonic() - stopped_at
        if self.log:
            self.log.close()
        if self.log_dir.is_dir():
            from apex.game_studio.lifecycle import atomic_json

            atomic_json(self.log_dir / "model-lifecycle.json", self.facts)


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

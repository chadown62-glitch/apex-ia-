"""Cooperative single-device lease for this program; no claim of global GPU enforcement."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

import psutil


def check_external_workloads() -> None:
    from apex.discovery import PHASES, discovery_path
    from apex.standalone import paths
    from apex.standalone.process import alive

    for path in {
        discovery_path(),
        paths().runtime_dir / "backend.json",
        paths({}).runtime_dir / "backend.json",
    }:
        if path.exists():
            record = json.loads(path.read_text())
            if (
                not isinstance(record, dict)
                or type(record.get("pid")) is not int
                or record["pid"] <= 0
                or record.get("phase") not in PHASES
            ):
                raise RuntimeError("core_state_unverified")
            if record.get("phase") in ("starting", "serving") and alive(record["pid"]):
                raise RuntimeError("core_active_handoff_required")
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        name = (process.info["name"] or "").lower()
        if process.info["pid"] != os.getpid() and (
            name.startswith("blender") or name.startswith("unrealeditor")
        ):
            raise RuntimeError("external_graphics_workload_active")
        if process.info["pid"] != os.getpid() and (
            "llama-server" in name
            or any(arg == "llama_cpp.server" for arg in (process.info["cmdline"] or []))
        ):
            raise RuntimeError("external_model_workload_active")


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


@contextmanager
def resource_lease(path: Path) -> Iterator[None]:
    home = os.environ.get("APEX_HOME")
    if not home or not Path(home).is_absolute():
        raise RuntimeError("shared_resource_profile_required")
    shared = Path(home) / ".apex" / "model-session.lock"
    with ExitStack() as stack:
        stack.enter_context(_locked(shared))
        if path.resolve() != shared.resolve():
            stack.enter_context(_locked(path))
        check_external_workloads()
        yield

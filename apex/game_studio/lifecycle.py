"""Durable resource accounting and exclusive worker ownership, not task states.

Reuse the existing cross-platform file lease. Time is reserved ahead in short
slices: a crash charges at most ten unused seconds, never resets the budget or
charges offline time. Attempt reservations survive crashes. Only the worker
writes receipts; other processes request cancellation via a marker.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from apex.game_graphics.resources import _locked


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def busy(folder: Path) -> bool:
    try:
        with _locked(folder / "worker.lock"):
            return False
    except OSError:
        return True


def snapshot(folder: Path) -> dict[str, Any]:
    path = folder / "budget.json"
    return json.loads(path.read_text()) if path.exists() else {}


class RunBudget:
    def __init__(self, folder: Path, limit_s: float, cancel: threading.Event):
        self.folder, self.limit_s, self.cancel = folder, limit_s, cancel
        self.lock = threading.RLock()
        self.data = snapshot(folder)
        self.used = float(self.data.get("charged_s", 0))
        self.data.pop("stop_reason", None)
        self.data.setdefault("attempts", {})
        # Preserve reservations made by the first candidate schema.
        counts = self.data["attempts"]
        if "asset_revision" not in counts and ("baseline" in counts or "artistic_revision" in counts):
            counts["asset_revision"] = counts.get("baseline", 0) + counts.get("artistic_revision", 0)
        self.stop = threading.Event()
        self.started = time.monotonic()
        self.thread: threading.Thread | None = None

    def checkpoint(self, final: bool = False) -> None:
        with self.lock:
            elapsed = self.used + time.monotonic() - self.started
            self.data.update(
                charged_s=min(self.limit_s, elapsed + (0 if final else 10)),
                observed_s=elapsed,
                limit_s=self.limit_s,
                recovery_policy="unused reservation charged on crash; at most 10 seconds",
                active=not final,
            )
            if elapsed >= self.limit_s:
                self.data["stop_reason"] = "cumulative_time_budget_exhausted"
                self.cancel.set()
            if (self.folder / "cancel.request").exists():
                self.data["stop_reason"] = "user_cancelled"
                self.cancel.set()
            atomic_json(self.folder / "budget.json", self.data)

    def consume(self, name: str, limit: int) -> None:
        with self.lock:
            count = self.data["attempts"].get(name, 0)
            if count >= limit:
                raise RuntimeError("cumulative_attempt_budget_exhausted:" + name)
            self.data["attempts"][name] = count + 1
            self.checkpoint()

    def __enter__(self):
        if self.used >= self.limit_s:
            raise RuntimeError("cumulative_time_budget_exhausted")
        self.checkpoint()

        def monitor():
            while not self.stop.wait(0.25):
                try:
                    self.checkpoint()
                except OSError:
                    self.cancel.set()
                    return

        self.thread = threading.Thread(target=monitor, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        if self.thread:
            self.thread.join()
        self.checkpoint(final=True)


def claim(folder: Path):
    lease = _locked(folder / "worker.lock")
    try:
        lease.__enter__()
    except OSError as exc:
        raise ValueError("game_studio_worker_already_owned") from exc
    return lease


def remaining(folder: Path, limit: float) -> float:
    return max(0, limit - float(snapshot(folder).get("charged_s", 0)))


def request_cancel(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "cancel.request").touch()


@contextmanager
def cancellable_session(session, cancel, *, max_rss_mib=12000):
    """Stop only this CoderSession's subprocess, including during model loading."""

    stop = threading.Event()
    session.cancel_event = cancel
    measured = {"sampled_tree_rss_peak_bytes": 0, "rss_limit_mib": max_rss_mib}

    def watch():
        import psutil

        recorded = False
        signalled = None
        while not stop.wait(0.1):
            process = session.process
            if process is not None and process.poll() is None:
                try:
                    root = psutil.Process(process.pid)
                    rss = sum(p.memory_info().rss for p in [root, *root.children(recursive=True)])
                    measured["sampled_tree_rss_peak_bytes"] = max(
                        measured["sampled_tree_rss_peak_bytes"], rss
                    )
                    if rss > max_rss_mib * 1024**2:
                        measured["stop_reason"] = "model_sampled_memory_limit"
                        cancel.set()
                except psutil.NoSuchProcess:
                    pass
            if process is not None and process.poll() is None and not recorded:
                try:
                    owned = psutil.Process(process.pid)
                    atomic_json(
                        session.log_dir / "model.owner.json",
                        {
                            "pid": process.pid,
                            "create_time": owned.create_time(),
                            "command": owned.cmdline(),
                            "kind": "qwen",
                            "stopped": False,
                        },
                    )
                    recorded = True
                except psutil.NoSuchProcess:
                    pass
            if cancel.is_set() and process is not None and process.poll() is None:
                try:
                    if signalled is None:
                        session.request_stop()
                        signalled = time.monotonic()
                    elif time.monotonic() - signalled > 2:
                        session.request_stop(force=True)
                except ProcessLookupError:
                    pass

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        if cancel.is_set():
            raise InterruptedError("user_cancelled")
        yield session.__enter__()
    finally:
        stop.set()
        watcher.join()
        session.__exit__(None, None, None)
        session.facts.update(measured)
        if session.log_dir.is_dir():
            atomic_json(session.log_dir / "model-lifecycle.json", session.facts)
        owner_path = session.log_dir / "model.owner.json"
        if owner_path.is_file():
            record = json.loads(owner_path.read_text())
            record["stopped"] = session.process is None or session.process.poll() is not None
            atomic_json(owner_path, record)


def cleanup_owned(folder: Path) -> list[dict[str, Any]]:
    """Caller holds worker.lock. PID reuse or mismatched command never grants kill."""
    import psutil

    reports = []
    for path in folder.rglob("*.owner.json"):
        record = json.loads(path.read_text())
        if record.get("stopped"):
            continue
        try:
            owned = psutil.Process(record["pid"])
            if owned.create_time() != record["create_time"] or owned.cmdline() != record["command"]:
                reports.append({"file": str(path), "result": "identity_changed_not_touched"})
                continue
            command = record["command"]
            if not (
                (record["kind"] == "qwen" and "llama_cpp.server" in command)
                or (record["kind"] == "blender" and Path(command[0]).name.lower().startswith("blender"))
            ):
                raise RuntimeError("owned_process_kind_unverified")
            tree = [*owned.children(recursive=True), owned]
            for process in tree:
                with suppress(psutil.NoSuchProcess):
                    process.terminate()
            _, alive = psutil.wait_procs(tree, timeout=3)
            for process in alive:
                process.kill()
            _, alive = psutil.wait_procs(alive, timeout=3)
            if any(p.status() != psutil.STATUS_ZOMBIE for p in alive):
                raise RuntimeError("owned_process_cleanup_failed")
        except psutil.NoSuchProcess:
            pass
        record["stopped"] = True
        record["recovery_verified"] = True
        atomic_json(path, record)
        reports.append({"file": str(path), "result": "owned_process_stopped"})
    return reports

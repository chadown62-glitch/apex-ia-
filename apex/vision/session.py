"""Cooperative cross-process specialist lease; core handoff is a proposed contract, not wired."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

from apex.vision.contract import VisionError, VisionRequest
from apex.vision.ocr import check_deadline


class ModelSession:
    def __init__(self, path: Path, request: VisionRequest, deadline: float) -> None:
        self.path = path
        self.request = request
        self.deadline = deadline
        self.handle: BinaryIO | None = None

    def __enter__(self) -> ModelSession:
        # Until Claude accepts a shared handoff, refuse an active registered core rather than loading
        # a specialist beside it. This is not a universal lock on unrelated inference processes.
        discovery = self.path.parent / "runtime" / "backend.json"
        if discovery.exists():
            from apex.standalone.process import alive

            try:
                record = json.loads(discovery.read_text(encoding="utf-8"))
                if record.get("phase") in ("starting", "serving") and alive(int(record["pid"])):
                    raise VisionError(
                        "main_runtime_active", "Arrêter le cœur enregistré avant la session vision."
                    )
            except VisionError:
                raise
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise VisionError(
                    "main_runtime_unverified", "État du cœur illisible ; transfert non autorisé."
                ) from exc
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            while True:
                check_deadline(self.request, self.deadline)
                try:
                    if os.name == "nt":
                        import msvcrt

                        # One byte must exist for Windows range locking. Do not truncate held locks.
                        if self.path.stat().st_size == 0:
                            handle.write(b"0")
                            handle.flush()
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.handle = handle
                    return self
                except (BlockingIOError, PermissionError):
                    if self.request.cancel is not None:
                        self.request.cancel.wait(0.05)
                    else:
                        time.sleep(0.05)
        except BaseException:
            handle.close()
            raise

    def __exit__(
        self, kind: type[BaseException] | None, value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        if self.handle is not None:
            # Closing releases the OS lock, including on exceptions; never unlink a shared lock inode.
            self.handle.close()
            self.handle = None


def default_session_path() -> Path:
    home = os.environ.get("APEX_HOME")
    return (Path(home) if home else Path.cwd()) / ".apex" / "model-session.lock"

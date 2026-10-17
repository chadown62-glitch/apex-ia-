"""Game Studio side of Issue #21's in-process quiescence contract.

This adapter does not unload a brain, kill a process, choose a compute backend,
or qualify CUDA. The existing CUDA entry guard remains mandatory. A CPU task
also needs the shared exclusive lease when the brain is resident on CUDA.
"""

from __future__ import annotations

import importlib
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from apex.game_graphics.resources import _locked, check_external_workloads
from apex.game_graphics.resources import resource_lease as shared_resource_lease
from apex.game_studio.lifecycle import atomic_json

LOG = logging.getLogger(__name__)


def _brain():
    try:
        return importlib.import_module("apex.models.gpu_handoff")
    except ModuleNotFoundError as exc:
        if exc.name != "apex.models.gpu_handoff":
            raise  # A broken installed dependency is not an absent brain.
        return None


def _same_path(value, expected):
    return isinstance(value, str) and Path(value).is_absolute() and Path(value).resolve() == expected


@contextmanager
def resource_lease(path: Path):
    """ACK → shared lease → operation → release → return; serialized borrowers.

    The per-profile handoff lock protects Game Studio callers across threads and
    processes. Other specialists must use the same protocol; the governor remains
    responsible for atomic owner validation against callers outside this adapter.
    """
    home = os.environ.get("APEX_HOME")
    if not home or not Path(home).is_absolute():
        raise RuntimeError("shared_resource_profile_required")
    shared = (Path(home) / ".apex/model-session.lock").resolve()
    requester = "game-studio:" + uuid4().hex
    receipt_path = path.parent / ("gpu-handoff-" + requester.split(":")[1] + ".json")
    receipt = {
        "schema_version": 1,
        "requester": requester,
        "lease_path": str(shared),
        "cuda_qualified": False,
        "events": [],
    }
    started = time.monotonic()
    brain, requested, primary_error = None, False, False
    # Do not call another process's governor: it cannot unload the actual owner.
    check_external_workloads()
    with _locked(shared.with_name("game-studio-handoff.lock")):
        try:
            brain = _brain()
            if brain is not None:
                status = brain.gpu_status()
                receipt["before"] = status
                if not _same_path(status.get("lease_path"), shared):
                    raise RuntimeError("brain_handoff_profile_mismatch")
                if status.get("transition") is not False or status.get("lent_to") is not None:
                    raise RuntimeError("brain_handoff_busy")
                requested = True
                ack = brain.request_gpu(30.0, requester=requester)
                receipt["ack"] = ack
                if not isinstance(ack, dict) or not (
                    ack.get("released") is True
                    and ack.get("requester") == requester
                    and ack.get("lease_held") is False
                    and ack.get("admissions_open") is False
                    and _same_path(ack.get("lease_path"), shared)
                ):
                    raise RuntimeError("brain_quiescence_ack_refused_or_invalid")
                if brain.gpu_status().get("lent_to") != requester:
                    raise RuntimeError("brain_handoff_owner_mismatch")
            else:
                receipt["brain"] = "module_absent_standalone_exclusive_lease_only"
            with shared_resource_lease(path):
                receipt["events"].append("shared_lease_acquired")
                yield
            receipt["events"].append("shared_lease_released")
        except BaseException as exc:
            primary_error = True
            receipt["error"] = type(exc).__name__ + ": " + str(exc)
            raise
        finally:
            try:
                if brain is not None and requested:
                    status = brain.gpu_status()
                    if status.get("lent_to") == requester:
                        # The context above released our OS locks even on an exception.
                        returned = brain.return_gpu(requester=requester)
                        receipt["return"] = returned
                        if returned.get("resumed") is not True or returned.get("requester") != requester:
                            raise RuntimeError("brain_return_not_acknowledged")
                        receipt["events"].append("brain_admissions_returned")
                    elif status.get("lent_to") is not None:
                        raise RuntimeError("brain_handoff_owner_changed_no_foreign_return")
            except Exception as exc:
                receipt["cleanup_error"] = type(exc).__name__ + ": " + str(exc)
                if not primary_error:
                    raise
                LOG.exception("Game Studio handoff cleanup failed; original task error preserved")
            finally:
                receipt["elapsed_s"] = time.monotonic() - started
                atomic_json(receipt_path, receipt)

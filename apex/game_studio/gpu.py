"""Game Studio side of Issue #21's in-process quiescence contract.

This adapter does not unload a brain, kill a process, choose a compute backend,
or qualify CUDA. The existing CUDA entry guard remains mandatory. Explicit CPU
tasks serialize their workloads without borrowing or returning the brain's GPU.
"""

from __future__ import annotations

import importlib
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from apex.game_graphics.resources import SharedResourceBusy, _locked, check_external_workloads
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


def _lenders(value):
    """Protocol 2 ownership is structured; lent_to is display-only."""
    counts = value.get("lenders")
    if not isinstance(counts, dict) or any(
        not isinstance(owner, str) or not owner or type(count) is not int or count <= 0
        for owner, count in counts.items()
    ):
        raise RuntimeError("brain_handoff_invalid_lenders")
    return counts


@contextmanager
def resource_lease(path: Path, *, compute_backend="cuda", cancel_event=None, timeout_s=30):
    """CPU is opt-in, never a fallback; both backends serialize Studio workloads.

    The workload lock also protects the fixed local Qwen port and host RAM.
    CPU work must use the pinned CPU controller and trusted CPU Blender scripts.
    An assistant's enclosing GPU loan is owned and returned by that assistant.
    """
    if compute_backend not in ("cpu", "cuda", "auto"):
        raise ValueError("unknown_coder_compute_backend")
    home = os.environ.get("APEX_HOME")
    if not home or not Path(home).is_absolute():
        raise RuntimeError("shared_resource_profile_required")
    deadline = time.monotonic() + max(0, timeout_s)
    lease = None
    while lease is None:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("user_cancelled_waiting_for_resources")
        candidate = _locked(Path(home) / ".apex/game-studio-workload.lock")
        try:
            candidate.__enter__()
            lease = candidate
        except SharedResourceBusy:
            # Legacy/GPU callers remain fail-fast; CPU mission waits are bounded
            # and charged by their existing persistent RunBudget.
            if compute_backend != "cpu" or time.monotonic() >= deadline:
                raise
            if cancel_event is not None:
                cancel_event.wait(0.1)
            else:
                time.sleep(0.1)
    try:
        if compute_backend == "cpu":
            with _locked(path):
                check_external_workloads()
                yield
        else:
            with _gpu_lease(path):
                yield
    finally:
        lease.__exit__(None, None, None)


@contextmanager
def _gpu_lease(path: Path):
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
    protocol = 1
    # Do not call another process's governor: it cannot unload the actual owner.
    check_external_workloads()
    with _locked(shared.with_name("game-studio-handoff.lock")):
        try:
            brain = _brain()
            if brain is not None:
                status = brain.gpu_status()
                receipt["before"] = status
                protocol = status.get("protocol_version", 1)
                if type(protocol) is not int or protocol not in (1, 2):
                    raise RuntimeError("brain_handoff_unsupported_protocol")
                if not _same_path(status.get("lease_path"), shared):
                    raise RuntimeError("brain_handoff_profile_mismatch")
                if protocol == 2:
                    _lenders(status)
                if status.get("transition") is not False or (
                    protocol == 1 and status.get("lent_to") is not None
                ):
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
                    and (
                        protocol == 1
                        or (ack.get("protocol_version") == 2 and _lenders(ack).get(requester) == 1)
                    )
                ):
                    raise RuntimeError("brain_quiescence_ack_refused_or_invalid")
                owner_status = brain.gpu_status()
                owns = (
                    _lenders(owner_status).get(requester) == 1
                    if protocol == 2
                    else owner_status.get("lent_to") == requester
                )
                if not owns:
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
                    if protocol == 2:
                        # Atomic ownership check/decrement belongs to the governor.
                        # Unknown requesters cannot mutate it, even if request_gpu
                        # raised after accepting our loan. Never infer ownership
                        # from a racy status snapshot or concatenated lent_to.
                        returned = brain.return_gpu(requester=requester)
                        receipt["return"] = returned
                        if not (
                            returned.get("protocol_version") == 2
                            and returned.get("returned") is True
                            and returned.get("requester") == requester
                            and requester not in _lenders(returned)
                            and type(returned.get("resumed")) is bool
                            and returned.get("admissions_open") is returned["resumed"]
                            and returned["resumed"] == (not _lenders(returned))
                        ):
                            raise RuntimeError("brain_return_not_acknowledged")
                        receipt["events"].append("brain_loan_returned")
                        if returned["resumed"]:
                            receipt["events"].append("brain_admissions_returned")
                    elif (status := brain.gpu_status()).get("lent_to") == requester:
                        # The context above released our OS locks even on an exception.
                        returned = brain.return_gpu(requester=requester)
                        receipt["return"] = returned
                        if (
                            returned.get("resumed") is not True
                            or returned.get("requester") != requester
                            or returned.get("lent_to") != requester
                        ):
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

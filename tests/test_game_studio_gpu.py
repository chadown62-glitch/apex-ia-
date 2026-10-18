"""Ownership and failure tests; no test double qualifies CUDA hardware."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from apex.game_graphics.resources import _locked
from apex.game_studio.gpu import resource_lease


class BrainContract:
    """Contract double matching PR27, intentionally without global ownership enforcement."""

    def __init__(self, path):
        self.path, self.lent_to, self.open, self.calls = path, None, True, []

    def gpu_status(self):
        return {"lease_path": str(self.path), "lent_to": self.lent_to, "transition": False}

    def request_gpu(self, timeout_s, *, requester):
        self.calls.append("request")
        self.lent_to, self.open = requester, False
        return {
            "released": True,
            "requester": requester,
            "lease_path": str(self.path),
            "lease_held": False,
            "admissions_open": False,
            "cuda": {"measured": False},
        }

    def return_gpu(self, *, requester):
        # Return must happen only AFTER the OS lease has been released.
        with _locked(self.path):
            self.calls.append("return")
        self.open, self.lent_to = True, None
        return {"resumed": True, "requester": requester, "lent_to": requester}


@pytest.fixture
def contract(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_HOME", str(tmp_path))
    brain = BrainContract(tmp_path / ".apex/model-session.lock")
    with (
        patch("apex.game_studio.gpu._brain", return_value=brain),
        patch("apex.game_studio.gpu.check_external_workloads"),
        patch("apex.game_graphics.resources.check_external_workloads"),
    ):
        yield brain, tmp_path / "mission/resource.lock"


def test_ack_then_real_lease_then_return(contract):
    brain, path = contract
    with resource_lease(path):
        assert not brain.open
        with pytest.raises(OSError), _locked(brain.path):
            pytest.fail("lease was not exclusive")
    assert brain.calls == ["request", "return"]
    report = json.loads(next(path.parent.glob("gpu-handoff-*.json")).read_text())
    assert report["events"] == [
        "shared_lease_acquired",
        "shared_lease_released",
        "brain_admissions_returned",
    ]
    assert report["ack"]["cuda"]["measured"] is False


def test_exception_releases_and_returns(contract):
    brain, path = contract
    with pytest.raises(RuntimeError, match="task_failed"), resource_lease(path):
        raise RuntimeError("task_failed")
    assert brain.open and brain.calls == ["request", "return"]
    with _locked(brain.path):
        pass


def test_concurrent_borrower_cannot_reopen_brain(contract):
    brain, path = contract
    with resource_lease(path):
        with pytest.raises(OSError), resource_lease(path.parent / "other.lock"):
            pytest.fail("concurrent borrower entered")
        assert brain.calls == ["request"] and not brain.open
    assert brain.open


def test_existing_foreign_loan_is_not_overwritten(contract):
    brain, path = contract
    brain.lent_to = "another-specialist"
    with pytest.raises(RuntimeError, match="busy"), resource_lease(path):
        pytest.fail("foreign loan overwritten")
    assert brain.calls == [] and brain.lent_to == "another-specialist"


def test_lease_busy_after_ack_returns_loan(contract):
    brain, path = contract
    # Hold the task-specific lease; shared lease must be freed before return.
    with _locked(path), pytest.raises(OSError), resource_lease(path):
        pytest.fail("busy task lease acquired")
    assert brain.open and brain.calls == ["request", "return"]


def test_malformed_ack_stops_operation_but_returns_owned_loan(contract):
    brain, path = contract
    original = brain.request_gpu

    def malformed(timeout_s, *, requester):
        return {**original(timeout_s, requester=requester), "released": "true"}

    brain.request_gpu = malformed
    with pytest.raises(RuntimeError, match="ack_refused"), resource_lease(path):
        pytest.fail("invalid ACK accepted")
    assert brain.open and brain.calls == ["request", "return"]


def test_owner_change_does_not_return_foreign_loan(contract):
    brain, path = contract
    with pytest.raises(RuntimeError, match="owner_changed"), resource_lease(path):
        brain.lent_to = "foreign"
    assert brain.calls == ["request"] and not brain.open


def test_profile_mismatch_never_requests_unload(contract):
    brain, path = contract
    brain.path = brain.path.with_name("different-profile.lock")
    with pytest.raises(RuntimeError, match="profile_mismatch"), resource_lease(path):
        pytest.fail("wrong profile accepted")
    assert brain.calls == []


def test_absent_module_keeps_standalone_exclusive_lease(contract):
    brain, path = contract
    with (
        patch("apex.game_studio.gpu._brain", return_value=None),
        resource_lease(path),
        pytest.raises(OSError),
        _locked(brain.path),
    ):
        pytest.fail("lease absent")
    assert brain.calls == []


def test_broken_brain_dependency_does_not_fall_back():
    from apex.game_studio.gpu import _brain

    with (
        patch(
            "apex.game_studio.gpu.importlib.import_module",
            side_effect=ModuleNotFoundError("broken dependency", name="torch"),
        ),
        pytest.raises(ModuleNotFoundError, match="broken dependency"),
    ):
        _brain()


def test_packaged_vehicle_schema_matches_source(tmp_path):
    from runtime_build.build import build_runtime_from_worktree

    source = Path(__file__).resolve().parents[1]
    built = tmp_path / "runtime"
    build_runtime_from_worktree(built)
    relative = Path("docs/game_graphics/vehicle-asset-v1.schema.json")
    assert (built / relative).read_bytes() == (source / relative).read_bytes().replace(b"\r\n", b"\n")
    from apex.game_engineer.contract import CONTRACT_SCHEMA_SHA256
    from apex.game_graphics.models import file_sha256

    assert file_sha256(built / relative) == CONTRACT_SCHEMA_SHA256


def test_packaged_crlf_schema_keeps_agreed_hash(tmp_path):
    from apex.game_engineer.contract import CONTRACT_SCHEMA_SHA256
    from apex.game_graphics.models import file_sha256
    from runtime_build.build import build_runtime_from_worktree

    original = Path.read_bytes

    def windows_checkout(path):
        data = original(path)
        if path.name == "vehicle-asset-v1.schema.json":
            return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        return data

    built = tmp_path / "runtime"
    with patch.object(Path, "read_bytes", windows_checkout):
        build_runtime_from_worktree(built)
    assert file_sha256(built / "docs/game_graphics/vehicle-asset-v1.schema.json") == CONTRACT_SCHEMA_SHA256


def test_explicit_cpu_keeps_foreign_gpu_lease_and_never_calls_brain(contract):
    brain, path = contract
    brain.lent_to = "assistant:mission"
    with (
        _locked(brain.path),
        resource_lease(path, compute_backend="cpu"),
        pytest.raises(OSError),
        _locked(brain.path),
    ):
        pytest.fail("CPU released a foreign GPU lease")
    assert brain.calls == [] and brain.lent_to == "assistant:mission"


def test_cpu_workloads_still_exclude_each_other(contract):
    brain, path = contract
    with (
        resource_lease(path, compute_backend="cpu"),
        pytest.raises(BlockingIOError, match="shared_resource_busy"),
        resource_lease(path.parent / "another.lock", compute_backend="cpu", timeout_s=0),
    ):
        pytest.fail("concurrent Qwen server allowed")
    assert brain.calls == []


def test_cpu_wait_is_cancelled_without_returning_another_loan(contract):
    import threading

    brain, path = contract
    cancel = threading.Event()
    with resource_lease(path, compute_backend="cpu"):
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        try:
            with (
                pytest.raises(InterruptedError, match="waiting_for_resources"),
                resource_lease(path.parent / "another.lock", compute_backend="cpu", cancel_event=cancel),
            ):
                pytest.fail("cancelled operation started")
        finally:
            timer.join()
    assert brain.calls == []


def test_filesystem_access_error_is_not_resource_busy(tmp_path):
    from apex.game_graphics.resources import SharedResourceBusy

    with (
        patch.object(Path, "open", side_effect=PermissionError("directory access denied")),
        pytest.raises(PermissionError) as error,
        _locked(tmp_path / "lock"),
    ):
        pytest.fail("inaccessible path opened")
    assert not isinstance(error.value, SharedResourceBusy)


def test_busy_cpu_releases_when_first_operation_finishes(contract):
    import threading
    import time

    _, path = contract
    entered = threading.Event()
    release = threading.Event()

    def first():
        with resource_lease(path, compute_backend="cpu"):
            entered.set()
            release.wait(2)

    worker = threading.Thread(target=first)
    worker.start()
    try:
        assert entered.wait(2)
        timer = threading.Timer(0.1, release.set)
        timer.start()
        started = time.monotonic()
        with resource_lease(path.parent / "second.lock", compute_backend="cpu", timeout_s=2):
            assert time.monotonic() - started >= 0.08
        timer.join()
    finally:
        release.set()
        worker.join()


def test_cpu_auto_controller_uses_pinned_qwen_even_on_cuda_host(tmp_path):
    from apex.game_studio.qualification import REQUEST
    from apex.game_studio.service import GameStudioService
    from tests.test_taskloop_execution import sqlite_session_factory

    manifest = tmp_path / "coder-model.json"
    manifest.write_text("{}")  # Admission only; never loaded or called a verified model.
    service = GameStudioService(sqlite_session_factory(), root=tmp_path / "missions")
    with (
        patch(
            "apex.game_studio.service.probe",
            return_value={
                "vehicle_blender": {"status": "available_unqualified"},
                "cuda": {"available": True},
            },
        ),
        patch("apex.game_studio.service.native_coder_endpoint", return_value=object()),
        patch("apex.game_studio.service.coder_manifest", return_value=manifest),
        patch.object(service, "_start"),
    ):
        job = service.create({**REQUEST, "controller_model": "auto"}, [])
    assert job["request"]["resolved_controller_model"] == "pinned_7b"
    assert job["status"] == "queued"


class BrainContractV2(BrainContract):
    """Counted-loan contract double; CUDA remains unqualified."""

    def __init__(self, path):
        super().__init__(path)
        self.lenders = {}

    def gpu_status(self):
        return {
            **super().gpu_status(),
            "protocol_version": 2,
            "lenders": dict(self.lenders),
            "lent_to": "display-only",
        }

    def request_gpu(self, timeout_s, *, requester):
        ack = super().request_gpu(timeout_s, requester=requester)
        self.lenders[requester] = self.lenders.get(requester, 0) + 1
        return {**ack, "protocol_version": 2, "lenders": dict(self.lenders)}

    def return_gpu(self, *, requester):
        with _locked(self.path):
            self.calls.append("return")
        count = self.lenders.get(requester, 0)
        if count == 1:
            del self.lenders[requester]
        elif count > 1:
            self.lenders[requester] -= 1
        self.open = not self.lenders
        return {
            "protocol_version": 2,
            "returned": count > 0,
            "resumed": count > 0 and self.open,
            "admissions_open": self.open,
            "requester": requester,
            "lenders": dict(self.lenders),
            "lent_to": "display-only",
        }


@pytest.mark.parametrize("foreign_before", [False, True])
def test_v2_returns_only_owned_loan_with_other_borrowers(contract, foreign_before):
    old, path = contract
    brain = BrainContractV2(old.path)
    if foreign_before:
        brain.request_gpu(1, requester="foreign")
    with patch("apex.game_studio.gpu._brain", return_value=brain), resource_lease(path):
        if not foreign_before:
            brain.request_gpu(1, requester="foreign")
        assert len(brain.lenders) == 2
    assert brain.lenders == {"foreign": 1} and not brain.open
    report = json.loads(next(path.parent.glob("gpu-handoff-*.json")).read_text())
    assert report["return"]["returned"] is True
    assert report["return"]["resumed"] is False
    assert "brain_loan_returned" in report["events"]
    assert "brain_admissions_returned" not in report["events"]


def test_v2_accepted_request_raising_still_returns_its_loan(contract):
    old, path = contract
    brain = BrainContractV2(old.path)
    original = brain.request_gpu

    def failing(timeout_s, *, requester):
        original(timeout_s, requester=requester)
        raise RuntimeError("request_failed_after_acceptance")

    brain.request_gpu = failing
    with (
        patch("apex.game_studio.gpu._brain", return_value=brain),
        pytest.raises(RuntimeError, match="request_failed_after_acceptance"),
        resource_lease(path),
    ):
        pytest.fail("failed request entered")
    assert not brain.lenders and brain.open


def test_v2_busy_os_lease_returns_own_loan_without_foreign_return(contract):
    old, path = contract
    brain = BrainContractV2(old.path)
    brain.request_gpu(1, requester="foreign")
    with (
        patch("apex.game_studio.gpu._brain", return_value=brain),
        _locked(path),
        pytest.raises(OSError),
        resource_lease(path),
    ):
        pytest.fail("busy lease acquired")
    assert brain.lenders == {"foreign": 1}


def test_v2_invalid_count_rejected_before_borrow(contract):
    old, path = contract
    brain = BrainContractV2(old.path)
    brain.lenders["foreign"] = True
    with (
        patch("apex.game_studio.gpu._brain", return_value=brain),
        pytest.raises(RuntimeError, match="invalid_lenders"),
        resource_lease(path),
    ):
        pytest.fail("malformed ownership accepted")
    assert brain.calls == []

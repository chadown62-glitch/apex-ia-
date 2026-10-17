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
        return {"resumed": True, "requester": requester}


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
    with patch("apex.game_studio.gpu._brain", return_value=None), resource_lease(path):
        with pytest.raises(OSError), _locked(brain.path):
            pytest.fail("lease absent")
    assert brain.calls == []


def test_broken_brain_dependency_does_not_fall_back():
    from apex.game_studio.gpu import _brain

    with patch(
        "apex.game_studio.gpu.importlib.import_module",
        side_effect=ModuleNotFoundError("broken dependency", name="torch"),
    ):
        with pytest.raises(ModuleNotFoundError, match="broken dependency"):
            _brain()


def test_packaged_vehicle_schema_matches_source(tmp_path):
    from runtime_build.build import build_runtime_from_worktree

    source = Path(__file__).resolve().parents[1]
    built = tmp_path / "runtime"
    build_runtime_from_worktree(built)
    relative = Path("docs/game_graphics/vehicle-asset-v1.schema.json")
    assert (built / relative).read_bytes() == (source / relative).read_bytes()

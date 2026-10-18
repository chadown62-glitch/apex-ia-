"""Persistent accounting and real interprocess exclusion, without fake Blender."""

import json
import subprocess
import sys
import threading
import time

import pytest

from apex.game_studio.lifecycle import RunBudget, busy, claim, request_cancel, snapshot


def test_real_process_lease_prevents_second_owner_and_releases(tmp_path):
    lease = claim(tmp_path)
    try:
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; from apex.game_studio.lifecycle import busy; "
                    "import sys; sys.exit(0 if busy(Path(sys.argv[1])) else 1)"
                ),
                str(tmp_path),
            ],
            capture_output=True,
        )
        assert process.returncode == 0, process.stderr
        with pytest.raises(ValueError, match="already_owned"):
            claim(tmp_path)
    finally:
        lease.__exit__(None, None, None)
    assert not busy(tmp_path)


def test_attempts_and_time_survive_new_instance(tmp_path):
    with RunBudget(tmp_path, 100, threading.Event()) as budget:
        budget.consume("model", 1)
        time.sleep(0.03)
    before = snapshot(tmp_path)["charged_s"]
    assert before > 0
    with RunBudget(tmp_path, 100, threading.Event()) as resumed:
        assert resumed.used == before
        with pytest.raises(RuntimeError, match="attempt_budget_exhausted"):
            resumed.consume("model", 1)
    assert snapshot(tmp_path)["charged_s"] >= before


def test_crash_reservation_is_charged_and_offline_time_is_not(tmp_path):
    (tmp_path / "budget.json").write_text(
        json.dumps({"charged_s": 20, "observed_s": 10, "active": True, "attempts": {"model": 2}})
    )
    with RunBudget(tmp_path, 100, threading.Event()) as resumed:
        assert resumed.used == 20
        assert resumed.data["attempts"]["model"] == 2
    assert 20 <= snapshot(tmp_path)["charged_s"] < 21


def test_budget_and_remote_cancel_stop_actual_worker_event(tmp_path):
    event = threading.Event()
    with RunBudget(tmp_path, 0.03, event):
        assert event.wait(2)
    event = threading.Event()
    with RunBudget(tmp_path, 10, event):
        request_cancel(tmp_path)
        assert event.wait(2)
    assert snapshot(tmp_path)["stop_reason"] == "user_cancelled"


def test_second_service_does_not_interrupt_a_live_receipt(tmp_path):
    from uuid import uuid4

    from apex.game_studio.service import GameStudioService
    from apex.policy.model import Subject
    from tests.test_taskloop_execution import sqlite_session_factory

    factory = sqlite_session_factory()
    service = GameStudioService(factory, root=tmp_path)
    mission = service.missions.create(
        title="Ownership test",
        description="Owned thread test",
        objective="Observe ownership without interruption",
        specialty="game-graphics",
        created_by=Subject.HUMAN,
    )
    service.missions.mark_running(mission.id)
    job = str(uuid4())
    record = {
        "id": job,
        "mission_id": mission.id,
        "status": "running",
        "events": [],
        "current_task": "held",
    }
    service._save(record)
    lease = claim(tmp_path / job)
    try:
        observer = GameStudioService(factory, root=tmp_path)
        assert observer._read(job)["status"] == "running"
        assert observer.missions.get(mission.id).state.value == "running"
        with pytest.raises(ValueError, match="already_owned"):
            observer.resume(job)
    finally:
        lease.__exit__(None, None, None)
    recovered = GameStudioService(factory, root=tmp_path)
    assert recovered._read(job)["status"] == "interrupted"
    assert recovered.missions.get(mission.id).state.value == "failed"


def test_pid_reuse_identity_never_authorizes_termination(tmp_path):
    import psutil

    from apex.game_studio.lifecycle import cleanup_owned

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        owned = psutil.Process(child.pid)
        (tmp_path / "mismatch.owner.json").write_text(
            json.dumps(
                {
                    "pid": child.pid,
                    "create_time": owned.create_time() - 10,
                    "command": owned.cmdline(),
                    "kind": "blender",
                    "stopped": False,
                }
            )
        )
        report = cleanup_owned(tmp_path)
        assert report[0]["result"] == "identity_changed_not_touched"
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=3)


def test_cancelled_parent_keeps_missionservice_cancelled_state(tmp_path):
    from uuid import uuid4

    from apex.game_studio.project import GameStudioProjectService
    from apex.game_studio.service import GameStudioService
    from apex.policy.model import Subject
    from tests.test_taskloop_execution import sqlite_session_factory

    service = GameStudioProjectService(GameStudioService(sqlite_session_factory(), root=tmp_path))
    mission = service.missions.create(
        title="Cancel test",
        description="Observe cancellation",
        objective="cancel",
        specialty="game-graphics",
        created_by=Subject.HUMAN,
    )
    project = str(uuid4())
    service._save(
        {
            "id": project,
            "mission_id": mission.id,
            "recipe": "dynamic-v1",
            "request": {},
            "plan": None,
            "events": [],
        }
    )
    cancel = threading.Event()
    cancel.set()
    service._run_dynamic(project, cancel, time.monotonic())
    assert service.missions.get(mission.id).state.value == "cancelled"


def test_transient_windows_reader_keeps_previous_ledger_until_replace(tmp_path, monkeypatch):
    from apex.game_studio import lifecycle

    target = tmp_path / "budget.json"
    lifecycle.atomic_json(target, {"charged_s": 42})
    replace = lifecycle.os.replace
    calls = []

    def windows_reader(source, destination):
        calls.append(1)
        if len(calls) <= 2:
            assert json.loads(target.read_text())["charged_s"] == 42
            error = PermissionError("reader denies delete sharing")
            error.winerror = 32
            raise error
        replace(source, destination)

    monkeypatch.setattr(lifecycle.os, "replace", windows_reader)
    lifecycle.atomic_json(target, {"charged_s": 43, "label": "création"})
    assert json.loads(target.read_text())["charged_s"] == 43
    assert len(calls) == 3 and not list(tmp_path.glob("*.tmp"))


def test_persistent_io_failure_preserves_old_budget_and_reports_stop(tmp_path, monkeypatch):
    from apex.game_studio import lifecycle

    cancel = threading.Event()
    with RunBudget(tmp_path, 30, cancel) as budget:
        before = snapshot(tmp_path)["charged_s"]
        with monkeypatch.context() as scoped:
            scoped.setattr(
                lifecycle.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk failure"))
            )
            assert cancel.wait(2)
            assert snapshot(tmp_path)["charged_s"] == before
            assert budget.data["stop_reason"] == "budget_persistence_failed"
        assert not list(tmp_path.glob("*.tmp"))
    assert snapshot(tmp_path)["stop_reason"] == "budget_persistence_failed"


def test_transient_read_denial_does_not_reset_budget(tmp_path, monkeypatch):
    from apex.game_studio import lifecycle

    lifecycle.atomic_json(tmp_path / "budget.json", {"charged_s": 47, "attempts": {"planner": 1}})
    original = type(tmp_path).open
    calls = []

    def windows_read(path, *args, **kwargs):
        calls.append(1)
        if len(calls) <= 2:
            error = PermissionError("replacement sharing violation")
            error.winerror = 5
            raise error
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "open", windows_read)
    assert snapshot(tmp_path) == {"charged_s": 47, "attempts": {"planner": 1}}


def test_parent_failure_stops_only_its_live_child(tmp_path):
    from uuid import uuid4

    from apex.game_studio.project import GameStudioProjectService
    from apex.game_studio.service import GameStudioService
    from tests.test_taskloop_execution import sqlite_session_factory

    service = GameStudioProjectService(GameStudioService(sqlite_session_factory(), root=tmp_path))
    job, foreign = str(uuid4()), str(uuid4())
    entered = threading.Event()
    cancelled = threading.Event()

    def child():
        lease = claim(tmp_path / job)
        try:
            with RunBudget(tmp_path / job, 5, cancelled):
                entered.set()
                cancelled.wait(3)
        finally:
            lease.__exit__(None, None, None)

    worker = threading.Thread(target=child)
    worker.start()
    other = claim(tmp_path / foreign)
    try:
        assert entered.wait(2)
        assert service._stop_children({"steps": [{"job_id": job}]}) == [{"job_id": job, "stopped": True}]
        assert cancelled.is_set()
        assert busy(tmp_path / foreign) and not (tmp_path / foreign / "cancel.request").exists()
    finally:
        cancelled.set()
        worker.join()
        other.__exit__(None, None, None)


def test_windows_crt_permission_error_without_winerror_is_retried(tmp_path, monkeypatch):
    import errno

    from apex.game_studio import lifecycle

    lifecycle.atomic_json(tmp_path / "budget.json", {"charged_s": 61})
    original = type(tmp_path).open
    calls = []

    def crt_read(path, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError(errno.EACCES, "Permission denied")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(lifecycle.os, "name", "nt")
        scoped.setattr(type(tmp_path), "open", crt_read)
        assert snapshot(tmp_path)["charged_s"] == 61
    assert len(calls) == 2

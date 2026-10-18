"""Server ownership tests. A tiny child exercises lifecycle, never impersonates Qwen inference."""

import json
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apex.game_graphics import models
from apex.game_studio.lifecycle import cancellable_session


@pytest.fixture
def session(tmp_path):
    manifest = tmp_path / "model.json"
    manifest.write_text(
        json.dumps(
            {
                "repo": models.CODER_REPO,
                "commit": models.CODER_COMMIT,
                "sha256": models.CODER_SHA256,
                "license": "Apache-2.0",
                "license_reviewed_before_download": True,
                "path": str(tmp_path / "contract-only.gguf"),
            }
        )
    )
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    with patch.object(models, "file_sha256", return_value=models.CODER_SHA256):
        yield models.CoderSession(manifest, tmp_path / "logs", port=port, compute_backend="cpu")


def test_hash_cancelled_before_model_start(tmp_path):
    source = tmp_path / "weights"
    source.write_bytes(b"not a model")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError, match="verifying_weights"):
        models.file_sha256(source, cancel_event=cancel)


def test_any_listener_is_rejected_and_not_stopped(session):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", session.port))
        listener.listen()
        with pytest.raises(RuntimeError, match="model_port_already_owned"):
            session.__enter__()
        assert listener.fileno() >= 0
    assert session.process is None


def test_cpu_is_explicit_and_cancellation_stops_owned_child(session):
    popen = subprocess.Popen
    commands = []

    def child(command, **kwargs):
        commands.append(command)
        return popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)

    provider = SimpleNamespace(detect=lambda: SimpleNamespace(is_available=False))
    cancel = threading.Event()
    timer = threading.Timer(0.3, cancel.set)
    with (
        patch.object(models.subprocess, "Popen", side_effect=child),
        patch.object(models, "get_default_provider", return_value=provider),
    ):
        timer.start()
        try:
            with pytest.raises((InterruptedError, RuntimeError)), cancellable_session(session, cancel):
                pytest.fail("cancelled load entered generation")
        finally:
            timer.join()
    assert commands[0][-2:] == ["--n_gpu_layers", "0"]
    assert session.process.poll() is not None
    assert session.facts["process_stopped_verified"] is True
    report = json.loads((session.log_dir / "model-lifecycle.json").read_text())
    assert report["server_ready"] is False and report["process_stopped_verified"] is True


def test_dead_owned_child_cannot_adopt_another_endpoint(session):
    process = SimpleNamespace(pid=99999, returncode=42, poll=lambda: 42)
    provider = SimpleNamespace(detect=lambda: SimpleNamespace(is_available=True))
    with (
        patch.object(models.subprocess, "Popen", return_value=process),
        patch.object(models, "get_default_provider", return_value=provider),
        pytest.raises(RuntimeError, match="local_coder_start_failed"),
    ):
        session.__enter__()
    assert session.facts["process_stopped_verified"] is True
    assert session.facts["server_ready"] is False


def test_spawn_error_closes_log(session):
    with (
        patch.object(models.subprocess, "Popen", side_effect=OSError("spawn refused")),
        pytest.raises(OSError, match="spawn refused"),
    ):
        session.__enter__()
    assert session.log.closed

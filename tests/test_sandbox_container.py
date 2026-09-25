"""Tests de `apex.sandbox.container.ContainerSandbox`.

Deux familles, JAMAIS confondues (consigne de la mission N+25 : « distinguer
bug logiciel / environnement incapable de fournir une garantie / Docker
réellement fonctionnel ») :

- `TestContainerSandboxLogic*`/`TestDetectContainerCapabilities*` : la
  logique Python d'orchestration (construction d'argv, séquencement du
  nettoyage, câblage de l'annulation, classification) contre un DOUBLE de
  `docker` (`unittest.mock.patch` sur `subprocess.Popen`/`subprocess.run`,
  jamais un vrai conteneur). Ces tests s'exécutent PARTOUT, y compris sur
  cette machine de développement où le démon Docker est injoignable — ils ne
  prouvent RIEN sur les garanties de sécurité réelles de Docker, seulement
  que ce module les demande correctement et réagit correctement à ce que
  Docker répond.
- `TestRealDockerExecution` (fin du fichier) : exécution contre un VRAI démon
  Docker, avec les VRAIS drapeaux de sécurité — sautée (`skipUnless`) si
  `detect_container_capabilities().is_available` est faux, exactement le
  même patron que `tests/db_test_support.py::postgres_testable` pour les
  tests qui dépendent de PostgreSQL. Sur cette machine, ce démon est
  injoignable (`docker info` échoue, voir `apex/sandbox/detect.py`) : cette
  classe est donc sautée ICI. Elle ne l'est PAS censée l'être sur une machine
  où Docker fonctionne réellement — c'est elle, seule, qui prouve les
  garanties de sécurité annoncées.
"""

from __future__ import annotations

import json
import subprocess
import threading
import unittest
from unittest.mock import patch

from apex.errors import IsolationUnavailable, NotImplementedYet
from apex.sandbox.container import (
    ContainerSandbox,
    _NON_ROOT_UID,
    detect_container_capabilities,
    docker_cli_available,
)
from apex.sandbox.model import (
    CancellationToken,
    ExecutionStatus,
    IsolationLevel,
    ResourceLimits,
    SandboxCapabilityReport,
    SandboxSpec,
)


def _limits(**overrides) -> ResourceLimits:
    defaults = dict(cpu_cores=1.0, memory_mb=64, pids_limit=16, timeout_s=5, disk_mb=16, network="none")
    defaults.update(overrides)
    return ResourceLimits(**defaults)


def _spec(command: tuple[str, ...] = ("python3", "-c", "print('ok')"), **limit_overrides) -> SandboxSpec:
    return SandboxSpec(mission_id="m1", clone_id="c1", command=command, limits=_limits(**limit_overrides))


def _available_report(**overrides) -> SandboxCapabilityReport:
    defaults = dict(
        level=IsolationLevel.CONTAINERIZED, namespaces_available=True, cgroups_available=True,
        privilege_drop_available=True, network_isolation_available=True,
        docker_daemon_reachable=True, seccomp_available=False, detail="test: forced available",
    )
    defaults.update(overrides)
    return SandboxCapabilityReport(**defaults)


def _unavailable_report(**overrides) -> SandboxCapabilityReport:
    defaults = dict(
        level=IsolationLevel.NONE, namespaces_available=False, cgroups_available=False,
        privilege_drop_available=False, network_isolation_available=False,
        docker_daemon_reachable=False, seccomp_available=False, detail="test: forced unavailable",
    )
    defaults.update(overrides)
    return SandboxCapabilityReport(**defaults)


# ---------------------------------------------------------------------------
# Double déterministe du CLI docker — jamais un vrai conteneur.
# ---------------------------------------------------------------------------


class _FakeContainerState:
    def __init__(self) -> None:
        self.running = True
        self.exit_code = 0
        self.oom_killed = False


def _completed(returncode: int, stdout: str, stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr)


class _FakePopen:
    """Simule `subprocess.Popen` pour `docker run` — `hang=True` bloque
    `.communicate()` jusqu'à ce que `docker kill` (ou l'appelant) libère
    l'événement, exactement comme un vrai `docker run` bloquerait jusqu'à
    ce que le conteneur se termine ou soit tué."""

    def __init__(self, *, state: _FakeContainerState, stdout: str, stderr: str, hang: bool, kill_event: threading.Event) -> None:
        self._state = state
        self._stdout = stdout.encode()
        self._stderr = stderr.encode()
        self._hang = hang
        self._kill_event = kill_event
        self._communicated_once = False

    @property
    def returncode(self) -> int:
        return self._state.exit_code

    def communicate(self, timeout: float | None = None):
        if self._hang and not self._communicated_once:
            fired = self._kill_event.wait(timeout=timeout)
            if not fired:
                raise subprocess.TimeoutExpired(cmd=["docker", "run"], timeout=timeout)
        self._communicated_once = True
        return self._stdout, self._stderr


class _FakeDockerRuntime:
    """État mutable partagé entre les invocations `docker run/kill/inspect/rm`
    d'un même test — miroir de `write_fake_docker` (tests/test_launcher.py)
    mais pour le CLI d'exécution de conteneur plutôt que `docker compose`."""

    def __init__(self, *, stdout: str = "ok\n", stderr: str = "", exit_code: int = 0, hang: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.containers: dict[str, _FakeContainerState] = {}
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.oom_killed = False
        self.create_container = True
        self.hang = hang
        self._kill_events: dict[str, threading.Event] = {}

    def _event_for(self, name: str) -> threading.Event:
        return self._kill_events.setdefault(name, threading.Event())

    def popen_side_effect(self, argv, **kwargs):
        self.calls.append(list(argv))
        assert argv[0] == "docker" and argv[1] == "run", f"unexpected Popen argv: {argv}"
        name = argv[argv.index("--name") + 1]
        state = _FakeContainerState()
        state.exit_code = self.exit_code
        state.oom_killed = self.oom_killed
        if self.create_container:
            self.containers[name] = state
        return _FakePopen(
            state=state, stdout=self.stdout, stderr=self.stderr, hang=self.hang, kill_event=self._event_for(name),
        )

    def run_side_effect(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[:2] == ["docker", "kill"]:
            name = argv[2]
            state = self.containers.get(name)
            if state is not None:
                state.running = False
            self._event_for(name).set()
            return _completed(0, "", "")
        if argv[:2] == ["docker", "inspect"]:
            fmt = argv[argv.index("--format") + 1]
            name = argv[-1]
            state = self.containers.get(name)
            if state is None:
                return _completed(1, "", "Error: No such object")
            if fmt == "{{.State.Running}}":
                return _completed(0, "true" if state.running else "false", "")
            if fmt == "{{json .State}}":
                return _completed(0, json.dumps({"ExitCode": state.exit_code, "OOMKilled": state.oom_killed}), "")
            raise AssertionError(f"unexpected inspect format: {fmt}")
        if argv[:2] == ["docker", "rm"]:
            name = argv[-1]
            self.containers.pop(name, None)
            return _completed(0, "", "")
        raise AssertionError(f"unexpected docker invocation: {argv}")


def _patched(runtime: _FakeDockerRuntime):
    return (
        patch("apex.sandbox.container.subprocess.Popen", side_effect=runtime.popen_side_effect),
        patch("apex.sandbox.container.subprocess.run", side_effect=runtime.run_side_effect),
    )


# ---------------------------------------------------------------------------
# Construction d'argv — invariants statiques, jamais de subprocess.
# ---------------------------------------------------------------------------


class TestBuildArgv(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ContainerSandbox(image="python:3.12-slim")

    def test_required_security_flags_are_present(self) -> None:
        argv = self.sandbox._build_argv(_spec(), "apex_test_name")
        for flag in (
            "--network", "--read-only", "--tmpfs", "--user", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "--memory", "--cpus",
        ):
            self.assertIn(flag, argv)
        self.assertIn(f"{_NON_ROOT_UID}:{_NON_ROOT_UID}", argv)
        self.assertIn("none", argv)  # --network none

    def test_never_contains_privileged_or_host_mounts_or_docker_socket(self) -> None:
        """Invariant de sécurité statique — même discipline que
        `tests/test_launcher.py::TestKillIsScopedToVerifiedOrphanReclaim` :
        ces drapeaux ne sont pas filtrés après coup, ils ne sont
        STRUCTURELLEMENT jamais construits."""
        spec = SandboxSpec(
            mission_id="m", clone_id="c", command=("bash", "-c", "true"),
            limits=_limits(), env={"SOME_VAR": "value"},
        )
        argv = self.sandbox._build_argv(spec, "apex_test_name")
        self.assertNotIn("--privileged", argv)
        self.assertNotIn("-v", argv)
        self.assertNotIn("--mount", argv)
        self.assertNotIn("--volume", argv)
        joined = " ".join(argv)
        self.assertNotIn("docker.sock", joined)
        self.assertNotIn("/var/run/docker.sock", joined)

    def test_env_vars_passed_explicitly_never_inherited(self) -> None:
        spec = SandboxSpec(
            mission_id="m", clone_id="c", command=("true",),
            limits=_limits(), env={"FOO": "bar"},
        )
        argv = self.sandbox._build_argv(spec, "apex_test_name")
        idx = argv.index("-e")
        self.assertEqual(argv[idx + 1], "FOO=bar")

    def test_command_and_image_are_the_final_argv_elements(self) -> None:
        argv = self.sandbox._build_argv(_spec(command=("echo", "hi")), "apex_test_name")
        self.assertEqual(argv[-3:], ["python:3.12-slim", "echo", "hi"])


# ---------------------------------------------------------------------------
# detect_container_capabilities / sonde empirique.
# ---------------------------------------------------------------------------


class TestDetectContainerCapabilities(unittest.TestCase):
    def test_docker_daemon_unreachable_yields_unavailable(self) -> None:
        with patch("apex.sandbox.container._check_docker_daemon", return_value=(False, "démon injoignable (test)")):
            report = detect_container_capabilities(image="python:3.12-slim")
        self.assertIs(report.level, IsolationLevel.NONE)
        self.assertFalse(report.is_available)
        self.assertFalse(report.docker_daemon_reachable)
        self.assertIn("injoignable", report.detail)

    def _probe_stdout(self, *, uid: int, network_blocked: bool, rootfs_writable: bool) -> str:
        return json.dumps({"uid": uid, "network_blocked": network_blocked, "rootfs_writable": rootfs_writable})

    def test_probe_success_yields_containerized(self) -> None:
        stdout = self._probe_stdout(uid=_NON_ROOT_UID, network_blocked=True, rootfs_writable=False)
        with patch("apex.sandbox.container._check_docker_daemon", return_value=(True, "démon joignable (test)")):
            with patch("apex.sandbox.container.subprocess.run", return_value=_completed(0, stdout, "")):
                report = detect_container_capabilities(image="python:3.12-slim")
        self.assertIs(report.level, IsolationLevel.CONTAINERIZED)
        self.assertTrue(report.is_available)

    def test_probe_reports_uid_still_root_as_unavailable(self) -> None:
        """La garantie « utilisateur non-root » n'est jamais crue sur parole."""
        stdout = self._probe_stdout(uid=0, network_blocked=True, rootfs_writable=False)
        with patch("apex.sandbox.container._check_docker_daemon", return_value=(True, "ok")):
            with patch("apex.sandbox.container.subprocess.run", return_value=_completed(0, stdout, "")):
                report = detect_container_capabilities(image="python:3.12-slim")
        self.assertFalse(report.is_available)
        self.assertIs(report.level, IsolationLevel.NONE)

    def test_probe_reports_network_not_blocked_as_unavailable(self) -> None:
        stdout = self._probe_stdout(uid=_NON_ROOT_UID, network_blocked=False, rootfs_writable=False)
        with patch("apex.sandbox.container._check_docker_daemon", return_value=(True, "ok")):
            with patch("apex.sandbox.container.subprocess.run", return_value=_completed(0, stdout, "")):
                report = detect_container_capabilities(image="python:3.12-slim")
        self.assertFalse(report.is_available)

    def test_probe_reports_writable_rootfs_as_unavailable(self) -> None:
        stdout = self._probe_stdout(uid=_NON_ROOT_UID, network_blocked=True, rootfs_writable=True)
        with patch("apex.sandbox.container._check_docker_daemon", return_value=(True, "ok")):
            with patch("apex.sandbox.container.subprocess.run", return_value=_completed(0, stdout, "")):
                report = detect_container_capabilities(image="python:3.12-slim")
        self.assertFalse(report.is_available)

    def test_probe_nonzero_returncode_is_never_guessed_as_success(self) -> None:
        with patch("apex.sandbox.container._check_docker_daemon", return_value=(True, "ok")):
            with patch("apex.sandbox.container.subprocess.run", return_value=_completed(125, "", "image introuvable")):
                report = detect_container_capabilities(image="does-not-exist:latest")
        self.assertFalse(report.is_available)
        self.assertIn("125", report.detail)

    def test_probe_unparseable_output_is_never_guessed_as_success(self) -> None:
        with patch("apex.sandbox.container._check_docker_daemon", return_value=(True, "ok")):
            with patch("apex.sandbox.container.subprocess.run", return_value=_completed(0, "not json at all", "")):
                report = detect_container_capabilities(image="python:3.12-slim")
        self.assertFalse(report.is_available)

    def test_docker_cli_available_reflects_real_binary_presence(self) -> None:
        # Le binaire `docker` (client) existe bien sur cette machine, même si
        # le démon est injoignable — les deux faits sont distincts.
        self.assertTrue(docker_cli_available())


# ---------------------------------------------------------------------------
# execute() — orchestration, contre le double déterministe.
# ---------------------------------------------------------------------------


class TestExecuteRefusesWhenUnavailable(unittest.TestCase):
    def test_refuses_without_ever_invoking_docker(self) -> None:
        sandbox = ContainerSandbox(capability_report=_unavailable_report())
        runtime = _FakeDockerRuntime()
        p1, p2 = _patched(runtime)
        with p1, p2:
            with self.assertRaises(IsolationUnavailable):
                sandbox.execute(_spec())
        self.assertEqual(runtime.calls, [], "aucune invocation docker ne doit avoir lieu quand l'isolation est refusée")

    def test_refuses_non_none_network(self) -> None:
        sandbox = ContainerSandbox(capability_report=_available_report())
        with self.assertRaises(NotImplementedYet):
            sandbox.execute(_spec(network="proxy"))


class TestExecuteClassification(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ContainerSandbox(image="python:3.12-slim", capability_report=_available_report())

    def test_success(self) -> None:
        runtime = _FakeDockerRuntime(stdout="hello\n", exit_code=0)
        p1, p2 = _patched(runtime)
        with p1, p2:
            result = self.sandbox.execute(_spec())
        self.assertIs(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.exit_code.value, 0)
        self.assertIn("hello", result.stdout)
        self.assertIs(result.isolation_level, IsolationLevel.CONTAINERIZED)

    def test_nonzero_exit_is_failed(self) -> None:
        runtime = _FakeDockerRuntime(stderr="boom\n", exit_code=1)
        p1, p2 = _patched(runtime)
        with p1, p2:
            result = self.sandbox.execute(_spec())
        self.assertIs(result.status, ExecutionStatus.FAILED)
        self.assertEqual(result.exit_code.value, 1)
        self.assertIn("boom", result.stderr)
        self.assertIsNotNone(result.error_detail)

    def test_oom_killed_detected_via_inspect_state_not_exit_code(self) -> None:
        runtime = _FakeDockerRuntime(exit_code=137)
        runtime.oom_killed = True
        p1, p2 = _patched(runtime)
        with p1, p2:
            result = self.sandbox.execute(_spec(memory_mb=32))
        self.assertIs(result.status, ExecutionStatus.OOM_KILLED)

    def test_creation_failure_when_docker_binary_cannot_start(self) -> None:
        """Aucun conteneur n'a tourné : une indisponibilité du sandbox, jamais
        un résultat d'exécution imputable au candidat (PERMISSION_DENIED était
        classé comme un refus de politique CAUSÉ par le candidat)."""
        p1 = patch("apex.sandbox.container.subprocess.Popen", side_effect=OSError("docker: command not found"))
        # `docker rm -f` (filet de sécurité, voir TestCleanup) est quand même
        # tenté même si Popen n'a jamais démarré — doit rester un no-op sûr.
        p2 = patch("apex.sandbox.container.subprocess.run", return_value=_completed(1, "", "no such container"))
        with p1, p2, self.assertRaises(IsolationUnavailable) as caught:
            self.sandbox.execute(_spec())
        self.assertIn("docker: command not found", str(caught.exception))

    def test_daemon_refusing_to_create_the_container_is_an_unavailable_sandbox(self) -> None:
        stderr = "Unable to find image\ndocker: pull access denied\n"
        runtime = _FakeDockerRuntime(stderr=stderr, exit_code=125)
        runtime.create_container = False
        p1, p2 = _patched(runtime)
        with p1, p2, self.assertRaises(IsolationUnavailable) as caught:
            self.sandbox.execute(_spec())
        self.assertIn("pull access denied", str(caught.exception))

    def test_a_workload_exiting_125_inside_a_real_container_stays_a_candidate_failure(self) -> None:
        runtime = _FakeDockerRuntime(exit_code=125)
        p1, p2 = _patched(runtime)
        with p1, p2:
            result = self.sandbox.execute(_spec())
        self.assertIs(result.status, ExecutionStatus.FAILED)
        self.assertEqual(result.exit_code.value, 125)


class TestExecuteTimeout(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ContainerSandbox(image="python:3.12-slim", capability_report=_available_report())

    def test_timeout_triggers_a_real_docker_kill_then_classifies_timeout(self) -> None:
        runtime = _FakeDockerRuntime(hang=True)
        p1, p2 = _patched(runtime)
        with p1, p2:
            result = self.sandbox.execute(_spec(timeout_s=0.05))
        self.assertIs(result.status, ExecutionStatus.TIMEOUT)
        kill_calls = [c for c in runtime.calls if c[:2] == ["docker", "kill"]]
        self.assertEqual(len(kill_calls), 1, "docker kill doit avoir été appelé exactement une fois")


class TestExecuteCancellation(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ContainerSandbox(image="python:3.12-slim", capability_report=_available_report())

    def test_cancellation_requested_before_communicate_kills_and_classifies_cancelled(self) -> None:
        """`CancellationToken._register` déclenche `on_cancel` immédiatement
        si l'annulation a déjà été demandée — la même garantie anti-course
        que `apex.sandbox.model.CancellationToken` documente. Tuer le
        processus CLI `docker run` ne suffirait pas (voir docstring du
        module) : ce test vérifie qu'un VRAI `docker kill` (sur le double)
        a bien été émis, pas seulement que `execute()` est revenu vite."""
        runtime = _FakeDockerRuntime(hang=True)
        token = CancellationToken()
        token.request_cancel()  # demandé AVANT même le démarrage du conteneur
        p1, p2 = _patched(runtime)
        with p1, p2:
            result = self.sandbox.execute(_spec(timeout_s=5.0), cancel_token=token)
        self.assertIs(result.status, ExecutionStatus.CANCELLED)
        kill_calls = [c for c in runtime.calls if c[:2] == ["docker", "kill"]]
        self.assertEqual(len(kill_calls), 1)

    def test_cancellation_requested_from_another_thread_mid_execution(self) -> None:
        runtime = _FakeDockerRuntime(hang=True)
        token = CancellationToken()
        p1, p2 = _patched(runtime)

        def cancel_soon() -> None:
            import time

            time.sleep(0.1)
            token.request_cancel()

        threading.Thread(target=cancel_soon, daemon=True).start()
        with p1, p2:
            result = self.sandbox.execute(_spec(timeout_s=5.0), cancel_token=token)
        self.assertIs(result.status, ExecutionStatus.CANCELLED)


class TestCleanup(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ContainerSandbox(image="python:3.12-slim", capability_report=_available_report())

    def _rm_calls(self, runtime: _FakeDockerRuntime) -> list[list[str]]:
        return [c for c in runtime.calls if c[:2] == ["docker", "rm"]]

    def test_container_removed_after_success(self) -> None:
        runtime = _FakeDockerRuntime(exit_code=0)
        p1, p2 = _patched(runtime)
        with p1, p2:
            self.sandbox.execute(_spec())
        self.assertEqual(len(self._rm_calls(runtime)), 1)
        self.assertEqual(runtime.containers, {})

    def test_container_removed_after_failure(self) -> None:
        runtime = _FakeDockerRuntime(exit_code=1, stderr="err")
        p1, p2 = _patched(runtime)
        with p1, p2:
            self.sandbox.execute(_spec())
        self.assertEqual(len(self._rm_calls(runtime)), 1)

    def test_container_removed_after_timeout(self) -> None:
        runtime = _FakeDockerRuntime(hang=True)
        p1, p2 = _patched(runtime)
        with p1, p2:
            self.sandbox.execute(_spec(timeout_s=0.05))
        self.assertEqual(len(self._rm_calls(runtime)), 1)

    def test_container_removed_after_cancellation(self) -> None:
        runtime = _FakeDockerRuntime(hang=True)
        token = CancellationToken()
        token.request_cancel()
        p1, p2 = _patched(runtime)
        with p1, p2:
            self.sandbox.execute(_spec(timeout_s=5.0), cancel_token=token)
        self.assertEqual(len(self._rm_calls(runtime)), 1)

    def test_state_is_inspected_before_removal_not_after(self) -> None:
        """Ordre critique (voir docstring du module) : `docker rm` efface
        `OOMKilled`/`ExitCode` — l'inspection doit précéder la suppression."""
        runtime = _FakeDockerRuntime(exit_code=0)
        p1, p2 = _patched(runtime)
        with p1, p2:
            self.sandbox.execute(_spec())
        inspect_idx = next(i for i, c in enumerate(runtime.calls) if c[:2] == ["docker", "inspect"])
        rm_idx = next(i for i, c in enumerate(runtime.calls) if c[:2] == ["docker", "rm"])
        self.assertLess(inspect_idx, rm_idx)

    def test_removal_is_attempted_even_when_docker_run_itself_failed_to_start(self) -> None:
        with patch("apex.sandbox.container.subprocess.Popen", side_effect=OSError("no docker")):
            with patch("apex.sandbox.container.subprocess.run", return_value=_completed(0, "", "")) as run_mock:
                with self.assertRaises(IsolationUnavailable):
                    self.sandbox.execute(_spec())
        rm_calls = [c for c in run_mock.call_args_list if c.args and c.args[0][:2] == ["docker", "rm"]]
        self.assertEqual(len(rm_calls), 1, "le filet de sécurité docker rm -f doit s'exécuter même sans conteneur créé")


class TestNeverClaimsFalseIsolation(unittest.TestCase):
    def test_result_isolation_level_matches_detected_report(self) -> None:
        report = _available_report()
        sandbox = ContainerSandbox(image="python:3.12-slim", capability_report=report)
        runtime = _FakeDockerRuntime()
        p1, p2 = _patched(runtime)
        with p1, p2:
            result = sandbox.execute(_spec())
        self.assertIs(result.isolation_level, IsolationLevel.CONTAINERIZED)

    def test_never_reports_hardened(self) -> None:
        report = _available_report()
        sandbox = ContainerSandbox(image="python:3.12-slim", capability_report=report)
        runtime = _FakeDockerRuntime()
        p1, p2 = _patched(runtime)
        with p1, p2:
            result = sandbox.execute(_spec())
        self.assertIsNot(result.isolation_level, IsolationLevel.HARDENED)


# ---------------------------------------------------------------------------
# Intégration RÉELLE — sautée si aucun démon Docker réel ne satisfait
# empiriquement les garanties de sécurité (c'est le cas sur cette machine).
# ---------------------------------------------------------------------------


def _real_container_sandbox_available() -> bool:
    if not docker_cli_available():
        return False
    return detect_container_capabilities().is_available


@unittest.skipUnless(
    _real_container_sandbox_available(),
    "aucun démon Docker réel ne satisfait empiriquement les garanties de sécurité sur cette machine "
    "(voir apex.sandbox.container.detect_container_capabilities) — ce test prouve les garanties "
    "réelles et ne peut donc pas être simulé ; il s'exécutera sur une machine où Docker fonctionne.",
)
class TestRealDockerExecution(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ContainerSandbox()

    def test_real_container_runs_and_reports_success(self) -> None:
        result = self.sandbox.execute(_spec(command=("python3", "-c", "print('real-ok')")))
        self.assertIs(result.status, ExecutionStatus.SUCCESS)
        self.assertIn("real-ok", result.stdout)

    def test_real_network_is_actually_blocked(self) -> None:
        script = (
            "import socket\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "s.settimeout(2)\n"
            "try:\n"
            "    s.connect(('1.1.1.1', 443))\n"
            "    print('LEAK')\n"
            "except OSError as e:\n"
            "    print('blocked:', e)\n"
        )
        result = self.sandbox.execute(_spec(command=("python3", "-c", script)))
        self.assertNotIn("LEAK", result.stdout)
        self.assertIn("blocked", result.stdout)

    def test_real_process_runs_as_non_root(self) -> None:
        result = self.sandbox.execute(_spec(command=("python3", "-c", "import os; print(os.getuid())")))
        self.assertEqual(result.stdout.strip(), str(_NON_ROOT_UID))

    def test_real_rootfs_write_is_blocked(self) -> None:
        result = self.sandbox.execute(_spec(command=("bash", "-c", "touch /apex_should_never_exist 2>&1")))
        self.assertIn("Read-only file system", result.stdout + result.stderr)

    def test_real_cleanup_leaves_no_container_behind(self) -> None:
        result = self.sandbox.execute(_spec())
        name = result.facts["container_name"]
        out = subprocess.run(["docker", "inspect", name], capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0, "le conteneur aurait dû être supprimé après l'exécution")


if __name__ == "__main__":
    unittest.main()

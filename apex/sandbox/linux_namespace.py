"""LinuxNamespaceSandbox — le seul backend `SandboxProvider` implémenté.

Composition de mécanismes réels, chacun vérifié empiriquement (voir
`apex/sandbox/detect.py`) : namespaces Linux (mount/pid/net/ipc/uts),
cgroups v1 (cpu/memory/pids), abaissement de privilège réel (`setpriv`,
pas un remap de namespace cosmétique — voir la note de `detect.py`), racine
remontée en lecture seule, `/tmp` éphémère isolé, réseau coupé (y compris la
boucle locale vers l'hôte — donc vers PostgreSQL).

**Ce que ce backend NE fournit PAS**, à ne jamais prétendre : pas de jail de
filesystem complet (aucun `pivot_root` vers un rootfs minimal — la racine
reste visible en lecture seule, un fichier monde-lisible de l'hôte reste
lisible), pas de seccomp-bpf (aucun appel système n'est filtré). Registre :
`SandboxProvider` = STUB, niveau NAMESPACED — jamais "container-grade" ni
"fully isolated".
"""

from __future__ import annotations

import math
import os
import shlex
import signal
import subprocess
import time
import uuid
from typing import Any

from apex.errors import IsolationUnavailable
from apex.obs.status import Measurement
from apex.sandbox.detect import detect_capabilities
from apex.sandbox.model import (
    CancellationToken,
    ExecutionStatus,
    IsolationLevel,
    SandboxCapabilityReport,
    SandboxSpec,
)
from apex.sandbox.provider import SandboxProvider
from apex.sandbox.result import SandboxResult

# Racine du dépôt APEX, masquée dans le sandbox (sources et fichiers locaux jamais lisibles).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#: Remplacé par un tmpfs dans le sandbox (voir `_build_argv`) — comparaison de chemins seulement.
_SANDBOX_TMP = "/tmp"  # noqa: S108
_SYSTEM_PREFIXES = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt", "/proc", "/sys", "/dev")


def project_root_to_mask(root: str = _PROJECT_ROOT) -> str | None:
    """Seule une copie SOURCE du dépôt (pyproject.toml + apex/) est masquée ;
    sous /tmp elle l'est déjà par le tmpfs, et un répertoire système (paquet
    installé) ne l'est jamais — l'interpréteur y vit."""
    real = os.path.realpath(root)
    if real in ("/", _SANDBOX_TMP) or real.startswith(_SANDBOX_TMP + "/"):
        return None
    if any(real == prefix or real.startswith(prefix + "/") for prefix in _SYSTEM_PREFIXES):
        return None
    has_project_file = os.path.isfile(os.path.join(real, "pyproject.toml"))
    is_checkout = has_project_file and os.path.isdir(os.path.join(real, "apex"))
    if not is_checkout:
        return None
    return real

#: Cap de capture stdout/stderr — une mission diagnostique ne produit pas de
#: volume important ; au-delà, tronquer plutôt que de laisser le processus
#: parent accumuler une sortie non bornée (limite documentée : `communicate()`
#: lit jusqu'à la fin AVANT que ce module ne tronque — voir docstring de
#: `_run_and_capture`).
_MAX_CAPTURE_BYTES = 64 * 1024

_V1_CONTROLLERS = ("cpu", "memory", "pids")


def _cgroup_base(controller: str, name: str) -> str:
    return f"/sys/fs/cgroup/{controller}/{name}"


class LinuxNamespaceSandbox(SandboxProvider):
    def __init__(self, *, capability_report: SandboxCapabilityReport | None = None) -> None:
        # Injectable pour les tests (simuler UNAVAILABLE sans changer la machine).
        self._cached_report = capability_report

    def detect(self) -> SandboxCapabilityReport:
        if self._cached_report is None:
            self._cached_report = detect_capabilities()
        return self._cached_report

    # -- exécution ------------------------------------------------------

    def execute(self, spec: SandboxSpec, *, cancel_token: CancellationToken | None = None) -> SandboxResult:
        report = self.detect()
        if not report.is_available:
            raise IsolationUnavailable(
                "exécution de mission en sandbox",
                required="namespaced (namespaces + cgroups + privilège réel + réseau coupé)",
                available=report.level.value,
            )
        if spec.limits.network != "none":
            from apex.errors import NotImplementedYet

            raise NotImplementedYet(
                "réseau « proxy » en sandbox", phase=7, fallback="network=none uniquement pour l'instant",
            )

        cgroup_name = f"apex_{uuid.uuid4().hex[:16]}"
        cgroup_paths = self._create_cgroups(cgroup_name, spec)
        started = time.perf_counter()
        proc: subprocess.Popen | None = None
        timed_out = False
        stdout_bytes = stderr_bytes = b""
        creation_error: str | None = None

        try:
            argv = self._build_argv(spec, cgroup_paths)
            try:
                proc = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env=self._child_env(spec), start_new_session=True,
                )
            except OSError as exc:
                creation_error = str(exc)
            else:
                self._assign_cgroups(proc.pid, cgroup_paths)
                if cancel_token is not None:
                    # Enregistré APRÈS le vrai démarrage du processus — voir
                    # la docstring de CancellationToken._register pour la
                    # garantie contre la course avec une annulation demandée
                    # entre-temps. `start_new_session=True` fait que le pgid
                    # vaut le pid lui-même (même mécanisme que
                    # `_kill_process_group`, réutilisé ici, pas dupliqué).
                    cancel_token._register(
                        on_cancel=lambda p=proc: self._kill_process_group(p),
                        is_alive=lambda p=proc: self._process_group_alive(p),
                    )
                try:
                    stdout_bytes, stderr_bytes = proc.communicate(timeout=spec.limits.timeout_s)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._kill_process_group(proc)
                    stdout_bytes, stderr_bytes = proc.communicate()
        finally:
            elapsed_s = time.perf_counter() - started
            memory_peak_bytes, oom = self._read_memory_stats(cgroup_paths.get("memory"))
            pids_peak, pid_limit_hit = self._read_pids_stats(cgroup_paths.get("pids"))
            self._cleanup_cgroups(cgroup_paths)

        if creation_error is not None:
            return self._creation_failure_result(spec, report, creation_error)

        assert proc is not None
        returncode = proc.returncode
        exit_code = returncode if returncode is not None and returncode >= 0 else None
        signal_num = -returncode if returncode is not None and returncode < 0 else None

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        stdout_truncated = len(stdout) > _MAX_CAPTURE_BYTES
        stderr_truncated = len(stderr) > _MAX_CAPTURE_BYTES
        stdout = stdout[:_MAX_CAPTURE_BYTES]
        stderr = stderr[:_MAX_CAPTURE_BYTES]

        # Annulation explicitement demandée : un drapeau distinct, jamais
        # deviné depuis le seul signal 9 (qui coïncide avec un timeout ou
        # une limite de ressource — même principe que `timed_out` ci-dessus).
        cancelled = cancel_token.is_cancel_requested if cancel_token is not None else False

        status = self._classify(
            exit_code=exit_code, signal_num=signal_num, timed_out=timed_out,
            oom=oom, pid_limit_hit=pid_limit_hit, stderr=stderr, cancelled=cancelled,
        )

        return SandboxResult(
            mission_id=spec.mission_id,
            clone_id=spec.clone_id,
            status=status,
            isolation_level=report.level,
            exit_code=Measurement.real(exit_code) if exit_code is not None else Measurement.unknown(),
            signal=Measurement.real(signal_num) if signal_num is not None else Measurement.unknown(),
            elapsed_s=Measurement.real(round(elapsed_s, 3)),
            cpu_time_s=Measurement.unknown(),  # cpu.stat (v1) ne donne que throttling, pas le temps consommé
            memory_peak_mb=(
                Measurement.real(round(memory_peak_bytes / (1024 * 1024), 2))
                if memory_peak_bytes is not None else Measurement.unknown()
            ),
            pids_peak=Measurement.real(pids_peak) if pids_peak is not None else Measurement.unknown(),
            stdout=stdout, stderr=stderr,
            stdout_truncated=stdout_truncated, stderr_truncated=stderr_truncated,
            error_detail=None if status is ExecutionStatus.SUCCESS else self._error_summary(status, stderr),
            facts={"cgroup_name": cgroup_name, "isolation_detail": report.detail},
        )

    def _kill_process_group(self, proc: subprocess.Popen) -> None:
        """`proc.kill()` seul ne suffit PAS ici : `proc.pid` est le processus
        `unshare` EXTÉRIEUR, qui se contente d'attendre son enfant forké
        (pid 1 du nouveau namespace PID) — le tuer, lui, laisse cet enfant
        orphelin et TOUJOURS EN COURS D'EXÉCUTION (bug réel rencontré et
        corrigé pendant le développement : un timeout de 2s laissait le
        travail continuer jusqu'à sa fin naturelle 30s plus tard). On tue
        tout le GROUPE de processus (`start_new_session=True` place `unshare`
        et sa descendance dans le même groupe, le pid namespace ne change pas
        ce fait) — c'est un concept du noyau hôte, jamais namespacé."""
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _process_group_alive(self, proc: subprocess.Popen) -> bool:
        """Constat réel, jamais supposé — utilisé par `CancellationToken`
        (étape 8) pour prouver qu'un groupe de processus est bien mort avant
        de le déclarer terminé (`wait_until_terminated`). Un signal envoyé
        ne suffit jamais comme preuve, seul un constat direct compte — même
        principe que le reste de ce fichier (`memory.failcnt`, `pids.events`)."""
        try:
            os.killpg(os.getpgid(proc.pid), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # existe, signal refusé — jamais interprété comme mort

    def _creation_failure_result(
        self, spec: SandboxSpec, report: SandboxCapabilityReport, error: str,
    ) -> SandboxResult:
        return SandboxResult(
            mission_id=spec.mission_id, clone_id=spec.clone_id,
            status=ExecutionStatus.PERMISSION_DENIED, isolation_level=report.level,
            exit_code=Measurement.unknown(), signal=Measurement.unknown(),
            elapsed_s=Measurement.real(0.0), cpu_time_s=Measurement.unknown(),
            memory_peak_mb=Measurement.unknown(), pids_peak=Measurement.unknown(),
            stdout="", stderr="", stdout_truncated=False, stderr_truncated=False,
            error_detail=f"création de la sandbox impossible : {error}",
        )

    # -- construction de la commande isolée ------------------------------

    def _build_argv(self, spec: SandboxSpec, cgroup_paths: dict[str, str]) -> list[str]:
        cpu_seconds = max(1, math.ceil(spec.limits.timeout_s) + 2)
        # RLIMIT_AS volontairement plus généreux (x1.5) que le cgroup mémoire :
        # le cgroup (limite réelle, noyau, mesurable via memory.failcnt) doit
        # rester le mécanisme PRINCIPAL. Sans cette marge, RLIMIT_AS — vérifié
        # synchrone à chaque malloc() — gagne toujours la course et masque la
        # vraie limite derrière un simple MemoryError Python.
        address_space_kb = int(spec.limits.memory_mb * 1.5 * 1024)
        file_size_kb = spec.limits.disk_mb * 1024
        command = " ".join(shlex.quote(part) for part in spec.command)
        # Auto-assignation aux cgroups en TOUTE PREMIÈRE ligne, à l'intérieur
        # du processus lui-même : écrire le pid depuis le PARENT Python
        # (après Popen) arrive trop tard — `unshare --fork` a déjà pu créer
        # et faire hériter sa descendance AVANT que ce pid ne soit déplacé
        # dans le cgroup, laissant le vrai travail namespacé hors de toute
        # limite réelle. Un processus qui s'auto-assigne avant de forker quoi
        # que ce soit élimine cette course — vérifié par test d'intégration.
        cgroup_self_assign = "\n".join(
            f"echo $$ > {shlex.quote(path)}/cgroup.procs 2>/dev/null || true"
            for path in cgroup_paths.values()
        )
        masked = project_root_to_mask()
        mask_line = ""
        if masked is not None:
            mask_line = f"mount -t tmpfs -o size=1m,mode=0555 tmpfs {shlex.quote(masked)}\n"
        script = (
            f"{cgroup_self_assign}\n"
            "set -e\n"
            "mount --make-rprivate /\n"
            f"mount -t tmpfs -o size={spec.limits.disk_mb}m tmpfs /tmp\n"
            "mkdir -p /tmp/work\n"
            "chmod 1777 /tmp/work\n"
            f"{mask_line}"
            "mount -o remount,ro,bind /\n"
            "cd /tmp/work\n"
            f"ulimit -t {cpu_seconds} -v {address_space_kb} -u {spec.limits.pids_limit} -f {file_size_kb}\n"
            f"exec setpriv --reuid=65534 --regid=65534 --clear-groups --no-new-privs {command}\n"
        )
        return [
            "unshare", "--mount", "--pid", "--net", "--ipc", "--uts", "--fork",
            "bash", "-c", script,
        ]

    def _child_env(self, spec: SandboxSpec) -> dict[str, str]:
        """Environnement volontairement minimal — jamais un `os.environ`
        hérité : aucun secret, aucune variable POSTGRES_*/APEX_DB_*_PASSWORD
        ne peut atteindre le processus sandboxé, quel que soit ce que le
        processus APEX parent connaît par ailleurs."""
        return {"PATH": "/usr/bin:/bin", "HOME": "/tmp/work", "LANG": "C.UTF-8", **spec.env}

    # -- cgroups ---------------------------------------------------------

    def _create_cgroups(self, name: str, spec: SandboxSpec) -> dict[str, str]:
        paths: dict[str, str] = {}
        limits = spec.limits
        writes = {
            "cpu": [
                ("cpu.cfs_period_us", "100000"),
                ("cpu.cfs_quota_us", str(max(1000, int(limits.cpu_cores * 100_000)))),
            ],
            "memory": [
                ("memory.limit_in_bytes", str(limits.memory_mb * 1024 * 1024)),
                ("memory.swappiness", "0"),
            ],
            "pids": [("pids.max", str(limits.pids_limit))],
        }
        for controller in _V1_CONTROLLERS:
            path = _cgroup_base(controller, name)
            os.makedirs(path, exist_ok=True)
            for filename, value in writes[controller]:
                try:
                    with open(os.path.join(path, filename), "w") as fh:
                        fh.write(value)
                except OSError:
                    pass  # best-effort par fichier ; l'absence de limite reste visible dans facts/measurements
            paths[controller] = path
        return paths

    def _assign_cgroups(self, pid: int, paths: dict[str, str]) -> None:
        for path in paths.values():
            try:
                with open(os.path.join(path, "cgroup.procs"), "w") as fh:
                    fh.write(str(pid))
            except OSError:
                pass

    def _read_memory_stats(self, path: str | None) -> tuple[int | None, bool]:
        if path is None:
            return None, False
        peak = self._read_int(os.path.join(path, "memory.max_usage_in_bytes"))
        failcnt = self._read_int(os.path.join(path, "memory.failcnt"))
        return peak, bool(failcnt)

    def _read_pids_stats(self, path: str | None) -> tuple[int | None, bool]:
        if path is None:
            return None, False
        peak = self._read_int(os.path.join(path, "pids.peak"))
        events_path = os.path.join(path, "pids.events")
        hit = False
        try:
            with open(events_path) as fh:
                for line in fh:
                    key, _, value = line.strip().partition(" ")
                    if key == "max" and value.strip().isdigit():
                        hit = int(value.strip()) > 0
        except OSError:
            pass
        return peak, hit

    def _read_int(self, path: str) -> int | None:
        try:
            with open(path) as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            return None

    def _cleanup_cgroups(self, paths: dict[str, str]) -> None:
        """Nettoyage garanti — appelé depuis un `finally`. Avant `rmdir`
        (qui échoue tant qu'un processus reste membre), on tue explicitement
        tout pid encore listé dans `cgroup.procs` — filet de sécurité
        indépendant du kill par groupe de processus (`_kill_process_group`),
        au cas où un futur workload changerait son propre pgid."""
        for path in paths.values():
            self._kill_stragglers(path)
        for path in paths.values():
            try:
                os.rmdir(path)
            except OSError:
                pass  # voir tests/test_sandbox_security.py::test_no_process_leak_after_* pour la vérification

    def _kill_stragglers(self, cgroup_path: str) -> None:
        try:
            with open(os.path.join(cgroup_path, "cgroup.procs")) as fh:
                pids = [int(line) for line in fh if line.strip().isdigit()]
        except OSError:
            return
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    # -- classification ---------------------------------------------------

    def _classify(
        self, *, exit_code: int | None, signal_num: int | None, timed_out: bool,
        oom: bool, pid_limit_hit: bool, stderr: str, cancelled: bool = False,
    ) -> ExecutionStatus:
        # Vérifié avant timeout/signal : une annulation explicite (Scheduler,
        # étape 8) est un drapeau constaté, pas deviné depuis un signal 9 —
        # qui coïncide autrement avec RESOURCE_LIMIT_EXCEEDED ci-dessous.
        if cancelled:
            return ExecutionStatus.CANCELLED
        if timed_out:
            return ExecutionStatus.TIMEOUT
        if pid_limit_hit:
            return ExecutionStatus.PROCESS_LIMIT_EXCEEDED
        if oom:
            return ExecutionStatus.OOM_KILLED
        if signal_num == 24:  # SIGXCPU
            return ExecutionStatus.RESOURCE_LIMIT_EXCEEDED
        if signal_num == 9:
            return ExecutionStatus.RESOURCE_LIMIT_EXCEEDED
        if "No space left on device" in stderr:
            return ExecutionStatus.DISK_LIMIT_EXCEEDED
        if "Network is unreachable" in stderr or "Name or service not known" in stderr:
            return ExecutionStatus.NETWORK_DENIED
        if exit_code == 0:
            return ExecutionStatus.SUCCESS
        return ExecutionStatus.FAILED

    def _error_summary(self, status: ExecutionStatus, stderr: str) -> str:
        tail = stderr.strip().splitlines()[-1] if stderr.strip() else ""
        return f"{status.value}" + (f" — {tail}" if tail else "")

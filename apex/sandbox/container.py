"""ContainerSandbox — second backend `SandboxProvider`, exécution isolée via
Docker plutôt que des namespaces Linux directs.

Anticipé depuis l'étape 7 (`apex.sandbox.model.IsolationLevel.CONTAINERIZED`,
`docs/UNTRUSTED_DATA_SECURITY.md` §6) mais jamais implémenté tant que le
démon Docker n'était joignable sur AUCUNE machine de développement connue.
Ce module existe désormais parce qu'un déploiement réel (WSL2, Docker Engine
joignable, mais `unshare`/cgroups v1/`setpriv`/isolation réseau namespace
tous refusés par la politique de virtualisation de l'hôte — RLIMIT_ONLY)
en a besoin : sur une telle machine, `LinuxNamespaceSandbox` ne peut
JAMAIS atteindre NAMESPACED, et Docker est le seul mécanisme d'isolation
noyau réellement disponible.

Même discipline que `apex.sandbox.linux_namespace` et `apex.sandbox.detect` :
- **Jamais de présence de binaire prise pour une garantie.** `detect()` ne se
  contente pas de `docker info` (repris de `apex.sandbox.detect._check_
  docker_daemon`, jamais dupliqué) : il lance un VRAI conteneur avec les
  drapeaux de sécurité demandés et vérifie EMPIRIQUEMENT, depuis l'intérieur
  de ce conteneur, que le réseau est coupé, que l'utilisateur n'est pas root
  et que la racine est réellement en lecture seule — exactement le principe
  de la consigne de cette mission : « ne jamais supposer qu'une option Docker
  fournit réellement une protection sans la vérifier ».
- **Jamais de dégradation silencieuse.** `execute()` lève `IsolationUnavailable`
  si `detect().is_available` est faux — aucun repli implicite vers une
  exécution non conteneurisée.
- **Cleanup systématique, jamais supposé.** Pas de `--rm` : l'état final du
  conteneur (`docker inspect`, en particulier `OOMKilled`) doit être lu AVANT
  toute suppression, sans quoi Docker aurait déjà effacé la preuve. La
  suppression (`docker rm -f`) est ensuite inconditionnelle, dans un `finally`
  — même schéma que `_cleanup_cgroups` dans `linux_namespace.py`.
- **Annulation/timeout réels.** Tuer le processus CLI `docker run` (le
  parent Python) NE tue PAS le conteneur : le démon Docker continue de
  l'exécuter indépendamment du client qui l'a lancé — piège symétrique de
  celui déjà documenté pour `unshare` dans `linux_namespace.py`. La seule
  terminaison qui compte est `docker kill <name>`, suivie d'une vérification
  réelle (`docker inspect`) que le conteneur est bien arrêté.

**Ce que ce backend NE fournit PAS**, à ne jamais prétendre : pas de profil
seccomp personnalisé (celui par défaut de Docker s'applique, jamais vérifié
ici explicitement — `seccomp_available` reste `False`, cohérent avec
`apex.sandbox.detect`), pas de gVisor/runsc (voir `apex.gate.checks.
gate_19_isolation_state` et `gate_20_runsc_gonogo` pour ce niveau, distinct).
`IsolationLevel.CONTAINERIZED`, jamais `HARDENED`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from typing import Any

from apex.errors import IsolationUnavailable, NotImplementedYet
from apex.obs.status import Measurement
from apex.sandbox.detect import _check_docker_daemon
from apex.sandbox.capture import BoundedCapture
from apex.sandbox.model import (
    CancellationToken,
    ExecutionStatus,
    IsolationLevel,
    SandboxCapabilityReport,
    SandboxSpec,
)
from apex.sandbox.provider import SandboxProvider
from apex.sandbox.result import SandboxResult

_DEFAULT_IMAGE = "python:3.12-slim"
_PROBE_TIMEOUT_S = 30.0
_INSPECT_TIMEOUT_S = 10.0
_MAX_CAPTURE_BYTES = 64 * 1024
#: Code de sortie du CLI `docker run` quand l'erreur vient du démon lui-même (conteneur non créé).
_DOCKER_DAEMON_ERROR = 125
#: uid/gid non privilégié — le même que `setpriv --reuid=65534` dans
#: `linux_namespace.py` (nobody) : identité cohérente entre les deux
#: backends, jamais un choix arbitraire différent par backend.
_NON_ROOT_UID = 65534

#: Sonde exécutée À L'INTÉRIEUR d'un vrai conteneur, avec les mêmes drapeaux
#: de sécurité que `execute()` appliquerait réellement — même style que
#: `apex.sandbox.detect._NET_PROBE_SCRIPT`, étendu aux trois garanties que ce
#: backend doit vérifier avant de jamais prétendre CONTAINERIZED disponible.
_CONTAINER_PROBE_SCRIPT = (
    "import json, os, socket\n"
    "report = {}\n"
    "report['uid'] = os.getuid()\n"
    "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
    "s.settimeout(2)\n"
    "try:\n"
    "    s.connect(('1.1.1.1', 443))\n"
    "    report['network_blocked'] = False\n"
    "except OSError:\n"
    "    report['network_blocked'] = True\n"
    "try:\n"
    "    with open('/apex_container_probe_writeguard', 'w') as fh:\n"
    "        fh.write('x')\n"
    "    report['rootfs_writable'] = True\n"
    "except OSError:\n"
    "    report['rootfs_writable'] = False\n"
    "print(json.dumps(report))\n"
)


def _run(argv: list[str], *, timeout_s: float) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, check=False)


def _security_argv(
    *, network: str, memory_mb: int, pids_limit: int, cpu_cores: float, tmp_size_mb: int,
) -> list[str]:
    """Les drapeaux de sécurité NON négociables, partagés par la sonde de
    détection et par `execute()` — un seul endroit qui les énumère, pour que
    ce que `detect()` vérifie soit EXACTEMENT ce que `execute()` applique
    réellement, jamais deux jeux de drapeaux qui pourraient diverger."""
    return [
        "--network", network,
        "--read-only",
        "--tmpfs", f"/tmp:rw,size={tmp_size_mb}m,mode=1777",
        "--user", f"{_NON_ROOT_UID}:{_NON_ROOT_UID}",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(pids_limit),
        "--memory", f"{memory_mb}m",
        "--memory-swap", f"{memory_mb}m",  # jamais de swap au-delà de la limite mémoire elle-même
        "--cpus", str(cpu_cores),
        "--workdir", "/tmp",
    ]


def _probe_container_security(image: str) -> tuple[bool, str, dict[str, Any]]:
    """Lance un VRAI conteneur jetable avec les drapeaux de sécurité réels et
    vérifie, depuis l'intérieur, que chacun d'eux a un effet constaté — pas
    seulement accepté par le démon sans erreur."""
    name = f"apex_probe_{uuid.uuid4().hex[:8]}"
    argv = [
        "docker", "run", "--rm", "--name", name,
        *_security_argv(network="none", memory_mb=64, pids_limit=32, cpu_cores=0.5, tmp_size_mb=16),
        image, "python3", "-c", _CONTAINER_PROBE_SCRIPT,
    ]
    try:
        out = _run(argv, timeout_s=_PROBE_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"échec d'exécution de la sonde conteneur : {exc}", {}
    if out.returncode != 0:
        detail = f"la sonde conteneur a échoué (code {out.returncode}) : {out.stderr.strip()[:300]}"
        return False, detail, {}
    try:
        data = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, f"sortie de sonde illisible : {out.stdout.strip()[:200]!r}", {}
    ok = (
        data.get("uid") == _NON_ROOT_UID
        and data.get("network_blocked") is True
        and data.get("rootfs_writable") is False
    )
    detail = (
        f"uid={data.get('uid')} network_blocked={data.get('network_blocked')} "
        f"rootfs_writable={data.get('rootfs_writable')}"
    )
    return ok, detail, data


def detect_container_capabilities(*, image: str = _DEFAULT_IMAGE) -> SandboxCapabilityReport:
    """Détection EMPIRIQUE du niveau CONTAINERIZED — jamais supposée. Réutilise
    `_check_docker_daemon` (déjà réel, `apex.sandbox.detect`) comme premier
    filtre bon marché avant la sonde conteneur, plus coûteuse (un vrai `docker
    run`) — même économie que `apex.gate.checks.gate_19_isolation_state`, qui
    ne teste `runsc` qu'après avoir confirmé Docker joignable.

    Les champs de `SandboxCapabilityReport` (conçus autour des mécanismes de
    `LinuxNamespaceSandbox` : namespaces/cgroups/privilège/réseau) portent ici
    un sens ADAPTÉ à Docker, jamais réutilisé à tort : `namespaces_available`
    signifie « Docker isole réellement ce conteneur (réseau coupé + uid non
    privilégié constatés) », pas « `unshare` fonctionne sur l'hôte ».
    """
    docker_ok, docker_detail = _check_docker_daemon()
    if not docker_ok:
        return SandboxCapabilityReport(
            level=IsolationLevel.NONE,
            namespaces_available=False, cgroups_available=False,
            privilege_drop_available=False, network_isolation_available=False,
            docker_daemon_reachable=False, seccomp_available=False,
            detail=f"CONTAINERIZED indisponible — {docker_detail}",
            facts={"docker_daemon": docker_detail, "image": image},
        )

    probe_ok, probe_detail, probe_facts = _probe_container_security(image)
    if not probe_ok:
        return SandboxCapabilityReport(
            level=IsolationLevel.NONE,
            namespaces_available=False, cgroups_available=False,
            privilege_drop_available=False, network_isolation_available=False,
            docker_daemon_reachable=True, seccomp_available=False,
            detail=(
                "CONTAINERIZED indisponible — Docker joignable mais les garanties de "
                f"sécurité n'ont pas pu être vérifiées empiriquement : {probe_detail}"
            ),
            facts={
                "docker_daemon": docker_detail, "container_probe": probe_detail,
                "container_probe_facts": probe_facts, "image": image,
            },
        )

    return SandboxCapabilityReport(
        level=IsolationLevel.CONTAINERIZED,
        namespaces_available=True, cgroups_available=True,
        privilege_drop_available=True, network_isolation_available=True,
        docker_daemon_reachable=True, seccomp_available=False,
        detail=(
            "CONTAINERIZED — démon Docker joignable, réseau coupé + utilisateur non-root "
            f"+ racine en lecture seule vérifiés empiriquement sur un conteneur réel ({probe_detail}). "
            "Aucun profil seccomp personnalisé vérifié (celui par défaut de Docker s'applique, "
            "non confirmé ici) : jamais HARDENED."
        ),
        facts={
            "docker_daemon": docker_detail, "container_probe": probe_detail,
            "container_probe_facts": probe_facts, "image": image,
        },
    )


class ContainerSandbox(SandboxProvider):
    def __init__(
        self, *, image: str = _DEFAULT_IMAGE, capability_report: SandboxCapabilityReport | None = None,
    ) -> None:
        self._image = image
        # Injectable pour les tests — même patron que LinuxNamespaceSandbox.
        self._cached_report = capability_report

    @property
    def image(self) -> str:
        return self._image

    def detect(self) -> SandboxCapabilityReport:
        if self._cached_report is None:
            self._cached_report = detect_container_capabilities(image=self._image)
        return self._cached_report

    # -- exécution ------------------------------------------------------

    def execute(self, spec: SandboxSpec, *, cancel_token: CancellationToken | None = None) -> SandboxResult:
        report = self.detect()
        if not report.is_available:
            raise IsolationUnavailable(
                "exécution de mission en sandbox (ContainerSandbox)",
                required="containerized (Docker, réseau coupé + non-root + rootfs lecture seule "
                         "vérifiés empiriquement)",
                available=report.level.value,
            )
        if spec.limits.network != "none":
            raise NotImplementedYet(
                "réseau « proxy » en sandbox conteneurisée",
                phase=7,
                fallback="network=none uniquement pour l'instant, même limite que LinuxNamespaceSandbox",
            )

        name = f"apex_{uuid.uuid4().hex[:16]}"
        argv = self._build_argv(spec, name)
        started = time.perf_counter()
        proc: subprocess.Popen | None = None
        timed_out = False
        stdout_bytes = stderr_bytes = b""
        stdout_cut = stderr_cut = False
        creation_error: str | None = None

        try:
            archive = spec.input_archive()
            try:
                proc = subprocess.Popen(
                    argv, stdin=subprocess.PIPE if archive is not None else None,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self._child_env(),
                )
            except OSError as exc:
                creation_error = str(exc)
            else:
                if cancel_token is not None:
                    # Enregistré après le vrai `docker run` — voir la
                    # docstring du module : tuer `proc` (le client CLI) ne
                    # tuerait PAS le conteneur, seul `docker kill` le fait.
                    cancel_token._register(
                        on_cancel=lambda n=name: self._kill_container(n),
                        is_alive=lambda n=name: self._container_running(n),
                    )
                # Capture BORNÉE (début + fin, le reste compté puis jeté) : jamais toute la sortie en mémoire
                # de l'hôte — voir `apex.sandbox.capture`.
                capture = BoundedCapture(proc, limit=_MAX_CAPTURE_BYTES, input_bytes=archive).start()
                if not capture.wait(spec.limits.timeout_s):
                    timed_out = True
                    self._kill_container(name)
                stdout_bytes, stderr_bytes, stdout_cut, stderr_cut = capture.finish()
        finally:
            elapsed_s = time.perf_counter() - started
            # L'état final DOIT être lu avant suppression — `docker rm`
            # efface la preuve (OOMKilled, ExitCode) qu'on veut classifier.
            state = self._inspect_state(name) if creation_error is None else None
            self._force_remove(name)

        if creation_error is not None:
            raise self._never_started(f"le client docker n'a pas pu démarrer : {creation_error}")

        assert proc is not None
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        # Code 125 sans conteneur inspectable : le démon a refusé de créer le conteneur (image
        # absente, erreur du démon). Le candidat n'a jamais tourné — jamais un échec du candidat.
        if state is None and proc.returncode == _DOCKER_DAEMON_ERROR and not timed_out:
            tail = stderr.strip().splitlines()[-1] if stderr.strip() else "aucun détail"
            raise self._never_started(f"docker run a échoué avant le démarrage du conteneur : {tail}")
        stdout_truncated = stdout_cut
        stderr_truncated = stderr_cut

        cancelled = cancel_token.is_cancel_requested if cancel_token is not None else False
        exit_code = state.get("ExitCode") if state is not None else None
        oom = bool(state.get("OOMKilled")) if state is not None else False

        status = self._classify(
            state_readable=state is not None, exit_code=exit_code, timed_out=timed_out,
            oom=oom, stderr=stderr, cancelled=cancelled,
        )

        return SandboxResult(
            mission_id=spec.mission_id,
            clone_id=spec.clone_id,
            status=status,
            isolation_level=report.level,
            exit_code=Measurement.real(exit_code) if isinstance(exit_code, int) else Measurement.unknown(),
            # Docker ne rapporte pas le signal qui a terminé le processus
            # racine du conteneur (seulement OOMKilled/ExitCode) — jamais
            # deviné, UNKNOWN reste honnête ici (contrairement à
            # LinuxNamespaceSandbox, qui lit le vrai wait-status POSIX).
            signal=Measurement.unknown(),
            elapsed_s=Measurement.real(round(elapsed_s, 3)),
            cpu_time_s=Measurement.unknown(),
            memory_peak_mb=Measurement.unknown(),
            pids_peak=Measurement.unknown(),
            stdout=stdout, stderr=stderr,
            stdout_truncated=stdout_truncated, stderr_truncated=stderr_truncated,
            error_detail=None if status is ExecutionStatus.SUCCESS else self._error_summary(status, stderr),
            facts={"container_name": name, "isolation_detail": report.detail, "image": self._image},
        )

    def _never_started(self, detail: str) -> IsolationUnavailable:
        return IsolationUnavailable(
            f"exécution de mission en sandbox (ContainerSandbox) — {detail}",
            required="un conteneur réellement démarré",
            available="aucun conteneur créé",
        )

    # -- construction de la commande isolée ------------------------------

    def _build_argv(self, spec: SandboxSpec, name: str) -> list[str]:
        limits = spec.limits
        argv = [
            "docker", "run", "--name", name,
            *_security_argv(
                network=limits.network, memory_mb=limits.memory_mb, pids_limit=limits.pids_limit,
                cpu_cores=limits.cpu_cores, tmp_size_mb=limits.disk_mb,
            ),
        ]
        # Variables explicites uniquement — jamais un `-v`/`--mount` d'un
        # chemin hôte (aucun montage du dépôt, aucun socket Docker), jamais
        # `--privileged` : cette liste n'inclut STRUCTURELLEMENT ni l'un ni
        # l'autre, ce n'est pas filtré après coup — voir
        # tests/test_sandbox_container.py::TestBuildArgv.
        for key, value in spec.env.items():
            argv += ["-e", f"{key}={value}"]
        if spec.input_files:
            # Instantané de l'espace de travail : archive tar sur l'entrée standard (`-i`), extraite dans
            # /tmp/work (tmpfs) par l'utilisateur non root du conteneur, puis la commande y est exécutée.
            # Toujours aucun montage d'un chemin hôte.
            argv.insert(2, "-i")
            argv += [
                self._image, "sh", "-c",
                'mkdir -p /tmp/work && tar -x -C /tmp/work -f - && cd /tmp/work && exec "$@"',
                "apex-sandbox", *spec.command,
            ]
            return argv
        argv += [self._image, *spec.command]
        return argv

    def _child_env(self) -> dict[str, str]:
        """Environnement du CLIENT `docker` lui-même (pas du conteneur, déjà
        cadré par `_build_argv`/`-e`) — minimal, jamais un `os.environ`
        hérité : `PATH` pour trouver le socket/binaire Docker, rien de plus.
        Même discipline que `LinuxNamespaceSandbox._child_env`."""
        docker_host = os.environ.get("DOCKER_HOST", "")
        env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp"}
        if docker_host:
            env["DOCKER_HOST"] = docker_host
        return env

    # -- cycle de vie du conteneur ----------------------------------------

    def _kill_container(self, name: str) -> None:
        """`docker kill` — jamais un signal envoyé au processus CLI parent
        (voir docstring du module : ça ne tue pas le conteneur)."""
        try:
            _run(["docker", "kill", name], timeout_s=_INSPECT_TIMEOUT_S)
        except (subprocess.TimeoutExpired, OSError):
            pass

    def _container_running(self, name: str) -> bool:
        """Constat réel, jamais supposé — même contrat que
        `LinuxNamespaceSandbox._process_group_alive`, utilisé par
        `CancellationToken.wait_until_terminated`."""
        try:
            out = _run(
                ["docker", "inspect", "--format", "{{.State.Running}}", name], timeout_s=_INSPECT_TIMEOUT_S,
            )
        except (subprocess.TimeoutExpired, OSError):
            return True  # inspection impossible : jamais interprété comme mort
        if out.returncode != 0:
            return False  # conteneur introuvable = déjà supprimé/mort
        return bool(str(out.stdout).strip() == "true")

    def _inspect_state(self, name: str) -> dict[str, Any] | None:
        """Lit `.State` (ExitCode, OOMKilled...) — appelé AVANT toute
        suppression, jamais après (voir docstring du module)."""
        try:
            out = _run(
                ["docker", "inspect", "--format", "{{json .State}}", name], timeout_s=_INSPECT_TIMEOUT_S,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if out.returncode != 0:
            return None
        try:
            parsed: Any = json.loads(str(out.stdout).strip())
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _force_remove(self, name: str) -> None:
        """Nettoyage inconditionnel, dans un `finally` — succès, échec,
        timeout ou annulation. `docker rm -f` est idempotent (ne lève pas si
        le conteneur n'existe déjà plus), donc toujours sûr à appeler."""
        try:
            _run(["docker", "rm", "-f", name], timeout_s=_INSPECT_TIMEOUT_S)
        except (subprocess.TimeoutExpired, OSError):
            pass  # filet de sécurité best-effort — voir tests de cleanup

    # -- classification ---------------------------------------------------

    def _classify(
        self, *, state_readable: bool, exit_code: int | None, timed_out: bool,
        oom: bool, stderr: str, cancelled: bool,
    ) -> ExecutionStatus:
        if cancelled:
            return ExecutionStatus.CANCELLED
        if timed_out:
            return ExecutionStatus.TIMEOUT
        if oom:
            return ExecutionStatus.OOM_KILLED
        if not state_readable:
            # `docker inspect` a échoué après coup (démon redémarré,
            # conteneur disparu de façon inattendue) : jamais deviné SUCCESS.
            return ExecutionStatus.FAILED
        # PROCESS_LIMIT_EXCEEDED n'est JAMAIS déduit ici : contrairement à
        # LinuxNamespaceSandbox, qui lit `pids.events` (cgroup v1) directement
        # pour constater un dépassement de PID, `docker inspect` ne distingue
        # pas cette cause d'un échec ordinaire du workload (son propre
        # fork() échoue et il se termine en erreur) — jamais fabriqué.
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


def docker_cli_available() -> bool:
    """Vérification bon marché (présence du binaire seulement) — utilisée
    par les tests pour choisir entre la suite « logique pure » (toujours
    exécutée, double `docker` factice) et la suite « intégration réelle »
    (sautée si aucun `docker` n'existe du tout sur cette machine)."""
    return shutil.which("docker") is not None

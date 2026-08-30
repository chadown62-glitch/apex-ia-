"""Détection EMPIRIQUE des mécanismes d'isolation — jamais supposée.

Chaque fonction `_check_*` exécute réellement le mécanisme qu'elle prétend
vérifier (namespace créé pour de vrai, cgroup effectivement écrit, uid
réellement changé) plutôt que de déduire d'une simple présence de binaire.
C'est la même discipline que `apex.gate.checks` (§3 de son propre docstring :
"les faits publiés distinguent false de null"), appliquée ici au Sandbox.

Cette détection est INDÉPENDANTE de `apex.gate` : le Gate cible un
déploiement WSL2/Docker et ne teste que cgroups v2 au chemin standard
(`gate_14_virtualization`) — cette machine est un Linux natif avec cgroups
v1 fonctionnel mais pas au chemin que le Gate vérifie. Dupliquer une petite
détection ciblée ici, plutôt que de forcer le Sandbox à dépendre d'un
rapport Gate potentiellement inadapté à son propre contexte, est délibéré.
"""

from __future__ import annotations

import ctypes.util
import importlib.util
import os
import shutil
import subprocess
import sys
import uuid

from apex.sandbox.model import IsolationLevel, SandboxCapabilityReport

_V1_CONTROLLERS = ("cpu", "memory", "pids")
_PROBE_TIMEOUT_S = 5.0


def _run(argv: list[str], *, timeout_s: float = _PROBE_TIMEOUT_S) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout_s, check=False,
    )


def _check_namespaces() -> tuple[bool, str]:
    if shutil.which("unshare") is None:
        return False, "binaire « unshare » introuvable"
    try:
        out = _run(["unshare", "--mount", "--pid", "--net", "--ipc", "--uts", "--fork", "/bin/true"])
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"échec d'exécution d'unshare : {exc}"
    if out.returncode != 0:
        return False, f"unshare a échoué (code {out.returncode}) : {out.stderr.strip()[:200]}"
    return True, "mount+pid+net+ipc+uts créés avec succès"


def _check_cgroups_v1_writable() -> tuple[bool, str, dict[str, bool]]:
    """cgroups v1 uniquement — v2 non supporté par ce backend (voir docstring
    du module). Un mkdir + écriture réelle, pas seulement une existence de
    répertoire : un montage tmpfs factice répondrait « oui » à un simple
    `exists()` sans qu'aucune limite ne soit jamais appliquée."""
    per_controller: dict[str, bool] = {}
    probe_name = f"apex_detect_{uuid.uuid4().hex[:8]}"
    for controller in _V1_CONTROLLERS:
        base = f"/sys/fs/cgroup/{controller}"
        path = f"{base}/{probe_name}"
        ok = False
        try:
            if os.path.isdir(base):
                os.makedirs(path, exist_ok=False)
                # Écriture réelle d'une valeur représentative par contrôleur.
                probe_file = {
                    "cpu": "cpu.cfs_quota_us",
                    "memory": "memory.limit_in_bytes",
                    "pids": "pids.max",
                }[controller]
                probe_value = {"cpu": "-1", "memory": str(64 * 1024 * 1024), "pids": "32"}[controller]
                with open(os.path.join(path, probe_file), "w") as fh:
                    fh.write(probe_value)
                ok = True
        except OSError:
            ok = False
        finally:
            try:
                os.rmdir(path)
            except OSError:
                pass
        per_controller[controller] = ok
    all_ok = all(per_controller.values())
    if all_ok:
        return True, "cpu/memory/pids (v1) créés, écrits et supprimés avec succès", per_controller
    missing = [c for c, ok in per_controller.items() if not ok]
    return False, f"contrôleurs cgroup v1 non écriviables : {', '.join(missing)}", per_controller


def _check_privilege_drop() -> tuple[bool, str]:
    if shutil.which("setpriv") is None:
        return False, "binaire « setpriv » introuvable"
    try:
        out = _run(["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "--no-new-privs", "id", "-u"])
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"échec d'exécution de setpriv : {exc}"
    if out.returncode != 0 or out.stdout.strip() != "65534":
        return False, f"setpriv n'a pas produit l'uid attendu : sortie={out.stdout.strip()!r}"
    return True, "uid réellement changé (65534, vérifié par sortie de `id -u`) — pas un simple remap de namespace"


_NET_PROBE_SCRIPT = (
    "import socket, sys\n"
    "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
    "s.settimeout(2)\n"
    "try:\n"
    "    s.connect(('1.1.1.1', 443))\n"
    "    print('REACHABLE')\n"
    "except OSError:\n"
    "    print('BLOCKED')\n"
)


def _check_network_isolation() -> tuple[bool, str]:
    if shutil.which("unshare") is None:
        return False, "binaire « unshare » introuvable"
    try:
        out = _run(
            ["unshare", "--net", sys.executable, "-c", _NET_PROBE_SCRIPT],
            timeout_s=_PROBE_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"échec du test d'isolation réseau : {exc}"
    if "BLOCKED" not in out.stdout:
        return False, f"une connexion sortante a réussi depuis le namespace réseau isolé : {out.stdout.strip()!r}"
    return True, "connexion sortante bloquée dans un namespace réseau frais (aucune route par défaut)"


def _check_docker_daemon() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "binaire « docker » introuvable"
    try:
        out = _run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout_s=8.0)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"docker injoignable : {exc}"
    if out.returncode != 0:
        return False, f"démon Docker injoignable : {out.stderr.strip()[:200]}"
    return True, f"démon Docker {out.stdout.strip()} joignable"


def _check_seccomp() -> tuple[bool, str]:
    if importlib.util.find_spec("seccomp") is not None:
        return True, "module Python « seccomp » disponible"
    lib = ctypes.util.find_library("seccomp")
    if lib:
        # « disponible » doit signifier EXPLOITABLE par ce code, pas « le
        # fichier .so existe quelque part sur le disque ». Sans binding
        # Python, ce module ne peut appliquer aucun filtre seccomp réel —
        # affirmer True ici mentirait exactement comme le §12 de la
        # consigne étape 7 l'interdit (bug trouvé par le test associé).
        return False, f"libseccomp présente ({lib}) mais aucun binding Python — non exploitable par ce code"
    return False, "ni module Python « seccomp » ni libseccomp trouvés"


def detect_capabilities() -> SandboxCapabilityReport:
    """Sonde une fois, à l'instant présent, CETTE machine. Coûteux (plusieurs
    subprocess réels) : l'appelant est responsable de mettre en cache le
    résultat pour la durée d'un processus s'il l'appelle souvent — ce module
    ne fait aucune supposition sur la fréquence d'appel."""
    ns_ok, ns_detail = _check_namespaces()
    cg_ok, cg_detail, cg_facts = _check_cgroups_v1_writable()
    priv_ok, priv_detail = _check_privilege_drop()
    net_ok, net_detail = _check_network_isolation()
    docker_ok, docker_detail = _check_docker_daemon()
    seccomp_ok, seccomp_detail = _check_seccomp()

    facts = {
        "namespaces": ns_detail,
        "cgroups_v1": cg_detail,
        "cgroups_v1_per_controller": cg_facts,
        "privilege_drop": priv_detail,
        "network_isolation": net_detail,
        "docker_daemon": docker_detail,
        "seccomp": seccomp_detail,
    }

    if ns_ok and cg_ok and priv_ok and net_ok:
        level = IsolationLevel.NAMESPACED
        detail = (
            "NAMESPACED — namespaces Linux + cgroups v1 + abaissement de privilège réel + "
            "réseau coupé, tous vérifiés empiriquement. PAS container-grade : aucun jail de "
            "filesystem (pivot_root) ni seccomp-bpf."
        )
    else:
        # `resource.setrlimit` (stdlib) est toujours disponible sur Linux — c'est le
        # seul repli honnête : jamais NONE tant qu'au moins les rlimits fonctionnent.
        level = IsolationLevel.RLIMIT_ONLY
        missing = [
            name for name, ok in (
                ("namespaces", ns_ok), ("cgroups", cg_ok),
                ("privilege_drop", priv_ok), ("network_isolation", net_ok),
            ) if not ok
        ]
        detail = f"RLIMIT_ONLY — mécanismes manquants ou non fonctionnels : {', '.join(missing)}"

    return SandboxCapabilityReport(
        level=level,
        namespaces_available=ns_ok,
        cgroups_available=cg_ok,
        privilege_drop_available=priv_ok,
        network_isolation_available=net_ok,
        docker_daemon_reachable=docker_ok,
        seccomp_available=seccomp_ok,
        detail=detail,
        facts=facts,
    )

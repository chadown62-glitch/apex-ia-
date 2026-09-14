"""Vérifications : interpréteur Python, système hôte, spécificités WSL2.

Les checks WSL2 encodent les conclusions de l'addendum de Phase 0. Ils ne sont
pas cosmétiques :

- L'interopérabilité Windows activée offre un chemin direct entre un processus
  Linux et l'exécution de binaires Windows. Sur une machine qui exécutera du
  code généré par un modèle, c'est une surface d'attaque évitable.
- Un projet placé sous `/mnt/c/` subit la couche d'interop du système de
  fichiers : les charges à nombreux petits fichiers (git, pytest, cargo, cmake)
  y sont ralenties d'un ordre de grandeur. Ce n'est pas un réglage fin, c'est
  la différence entre une nuit d'évaluation exploitable et une nuit perdue.
"""

from __future__ import annotations

import sys

from apex.version import MIN_PYTHON
from apex.doctor.check import CheckContext, CheckResult, Severity, result

CATEGORY = "environnement"


def check_python_version(ctx: CheckContext) -> CheckResult:
    current = sys.version_info[:3]
    if current[:2] >= MIN_PYTHON:
        return result(
            "python.version", CATEGORY, Severity.PASS,
            f"Python {'.'.join(map(str, current))}",
            version=".".join(map(str, current)),
        )
    return result(
        "python.version", CATEGORY, Severity.FAIL,
        f"Python {'.'.join(map(str, current))} — {'.'.join(map(str, MIN_PYTHON))}+ requis",
        remediation="Installer Python 3.12 dans la distribution WSL2 (pas sur l'hôte Windows).",
        version=".".join(map(str, current)),
    )


def check_optional_packages(ctx: CheckContext) -> CheckResult:
    """Les dépendances Python applicatives sont vérifiées mais non requises.

    Le doctor lui-même n'en dépend pas : il doit pouvoir diagnostiquer un
    environnement où rien n'est installé.
    """
    import importlib.util

    packages = ("yaml", "pydantic", "sqlalchemy", "alembic", "fastapi", "pytest")
    missing = [p for p in packages if importlib.util.find_spec(p) is None]
    present = [p for p in packages if p not in missing]

    if not missing:
        return result("python.packages", CATEGORY, Severity.PASS, "toutes les dépendances présentes",
                      present=present)
    severity = Severity.FAIL if "yaml" in missing else Severity.WARN
    return result(
        "python.packages", CATEGORY, severity,
        f"paquets absents : {', '.join(missing)}",
        remediation="uv sync  (PyYAML est requis pour lire config.yaml)",
        missing=missing, present=present,
    )


def _is_wsl(ctx: CheckContext) -> bool:
    info = ctx.probe.platform_info()
    if "microsoft" in info.get("release", "").lower():
        return True
    return ctx.probe.env("WSL_DISTRO_NAME") is not None


def check_host_platform(ctx: CheckContext) -> CheckResult:
    info = ctx.probe.platform_info()
    if info.get("system") != "Linux":
        return result(
            "host.platform", CATEGORY, Severity.FAIL,
            f"système « {info.get('system')} » — APEX s'exécute dans une distribution Linux",
            remediation="Lancer APEX depuis WSL2, pas depuis PowerShell ni cmd.",
            **info,
        )
    kind = "WSL2" if _is_wsl(ctx) else "Linux natif"
    return result("host.platform", CATEGORY, Severity.PASS, f"{kind} — noyau {info.get('release')}",
                  kind=kind, **info)


def check_wsl_interop(ctx: CheckContext) -> CheckResult:
    if not _is_wsl(ctx):
        return result("wsl.interop", CATEGORY, Severity.SKIP, "hors WSL2")
    content = ctx.probe.read_text("/etc/wsl.conf") or ""
    disabled = "enabled=false" in content.replace(" ", "") or "enabled = false" in content
    if disabled:
        return result("wsl.interop", CATEGORY, Severity.PASS,
                      "interopérabilité Windows désactivée")
    return result(
        "wsl.interop", CATEGORY, Severity.WARN,
        "interopérabilité Windows active — un processus Linux peut lancer des binaires Windows",
        remediation=(
            "Ajouter dans /etc/wsl.conf :\n"
            "  [interop]\n  enabled=false\n  appendWindowsPath=false\n"
            "puis : wsl --shutdown"
        ),
    )


def check_wsl_systemd(ctx: CheckContext) -> CheckResult:
    if not _is_wsl(ctx):
        return result("wsl.systemd", CATEGORY, Severity.SKIP, "hors WSL2")
    content = ctx.probe.read_text("/etc/wsl.conf") or ""
    if "systemd=true" in content.replace(" ", ""):
        return result("wsl.systemd", CATEGORY, Severity.PASS, "systemd activé")
    return result(
        "wsl.systemd", CATEGORY, Severity.WARN,
        "systemd désactivé — gestion des services (docker, postgres, ollama) plus fragile",
        remediation="Ajouter [boot]\\nsystemd=true dans /etc/wsl.conf puis wsl --shutdown",
    )


def check_cgroups_v2(ctx: CheckContext) -> CheckResult:
    """cgroups v2 conditionne l'application réelle des limites de sandbox."""
    if ctx.probe.exists("/sys/fs/cgroup/cgroup.controllers"):
        return result("host.cgroups", CATEGORY, Severity.PASS, "cgroups v2 disponible", version=2)
    return result(
        "host.cgroups", CATEGORY, Severity.FAIL,
        "cgroups v2 absent — les limites CPU/mémoire/pids de la sandbox ne seront pas appliquées",
        remediation="Mettre à jour WSL2 (wsl --update) et vérifier le noyau.",
        version=1,
    )


def check_data_root(ctx: CheckContext) -> CheckResult:
    """`paths.data_root` (N+26) — résolu par `${APEX_DATA_ROOT:-/home/apex/
    apex-data}` (voir `apex.config.loader.substitute_env`), jamais validé
    par le chargeur lui-même (qui reste sans effet de bord filesystem par
    conception, voir K1_K2_CORRECTIONS.md — un répertoire pas encore créé
    est un état légitime). C'est ICI, en lecture seule, que l'absence ou la
    non-écrivabilité devient un diagnostic observable — jamais un FAIL : un
    répertoire manquant est le cas ATTENDU sur une première installation
    (créé paresseusement à l'usage, ou automatiquement par le safe-fix
    `apex.doctor.repair` — `mkdir -p`, jamais destructeur)."""
    if ctx.config is None:
        return result("fs.data_root", CATEGORY, Severity.SKIP, "configuration non chargée")
    path = ctx.config.paths.data_root
    if not ctx.probe.exists(path):
        return result(
            "fs.data_root", CATEGORY, Severity.WARN,
            f"répertoire de données absent : {path}",
            remediation=(
                "mkdir -p " + path + "  — sûr, ne supprime jamais rien ; "
                "APEX_DATA_ROOT (variable d'environnement ou .env) permet de "
                "pointer ailleurs sans modifier config.yaml."
            ),
            path=path, exists=False,
        )
    if not ctx.probe.is_writable_dir(path):
        return result(
            "fs.data_root", CATEGORY, Severity.FAIL,
            f"répertoire de données non accessible en écriture : {path}",
            remediation=f"Corriger les permissions : chown/chmod sur {path} (ou pointer APEX_DATA_ROOT ailleurs).",
            path=path, exists=True, writable=False,
        )
    return result("fs.data_root", CATEGORY, Severity.PASS, f"répertoire de données prêt : {path}",
                  path=path, exists=True, writable=True)


CHECKS = (
    ("python.version", check_python_version),
    ("python.packages", check_optional_packages),
    ("host.platform", check_host_platform),
    ("host.cgroups", check_cgroups_v2),
    ("wsl.interop", check_wsl_interop),
    ("wsl.systemd", check_wsl_systemd),
    ("fs.data_root", check_data_root),
)

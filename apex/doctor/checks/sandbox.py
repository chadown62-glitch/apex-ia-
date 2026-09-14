"""Vérification du backend Sandbox réellement sélectionnable — N+26.

Ferme un manque explicitement documenté par N+25 (`docs/AGENT_HANDOFF.md`,
chapitre N+25, §6 : « aucun câblage CLI pour afficher `SandboxSelection.
reason` ») : `apex.sandbox.provider.select_sandbox_provider()` calcule déjà
QUEL backend serait choisi et POURQUOI, mais ce résultat n'était visible
nulle part avant cette mission (`apex.missions.execution.launch_mission`
l'émet désormais dans la trace d'une mission — voir son événement
`provider.selected` — mais un opérateur qui n'a pas encore lancé de mission
doit pouvoir le savoir AVANT).

**Volontairement absent de `ALL_CHECKS`** (le diagnostic rapide, exécuté par
défaut) : `select_sandbox_provider()` sonde EMPIRIQUEMENT (plusieurs
sous-processus réels — `unshare`, écritures cgroup, `setpriv`, et si les
namespaces sont indisponibles, un `docker run` réel pour la sonde de
sécurité conteneur) — coûteux par rapport aux autres checks (TCP/HTTP/lecture
de fichier). Exposé UNIQUEMENT par `apex doctor --full`
(`apex.doctor.runner.FULL_CHECKS`) — voir sa docstring."""

from __future__ import annotations

from typing import Any

from apex.doctor.check import CheckContext, CheckResult, Severity, result

CATEGORY = "sandbox"


def check_sandbox_selection(ctx: CheckContext) -> CheckResult:
    from apex.sandbox.provider import select_sandbox_provider

    selection = select_sandbox_provider()
    facts: dict[str, Any] = {
        "provider_name": selection.provider_name,
        "isolation_level": selection.report.level.value,
        "reason": selection.reason,
        "namespaces_available": selection.report.namespaces_available,
        "cgroups_available": selection.report.cgroups_available,
        "privilege_drop_available": selection.report.privilege_drop_available,
        "network_isolation_available": selection.report.network_isolation_available,
        "docker_daemon_reachable": selection.report.docker_daemon_reachable,
    }
    if selection.report.is_available:
        return result(
            "sandbox.provider", CATEGORY, Severity.PASS,
            f"backend sélectionné : {selection.provider_name} ({selection.report.level.value})",
            **facts,
        )
    return result(
        "sandbox.provider", CATEGORY, Severity.FAIL,
        f"aucun backend d'isolation réellement disponible — {selection.reason}",
        remediation=(
            "Namespaces Linux ET Docker sont tous deux insuffisants sur cet hôte "
            "(voir apex.sandbox.detect.detect_capabilities/apex.sandbox.container."
            "detect_container_capabilities pour le détail exact de chaque garantie "
            "manquante). L'exécution de mission en sandbox restera refusée "
            "(IsolationUnavailable) tant que l'un des deux ne devient pas réellement "
            "fonctionnel — jamais une exécution dégradée à la place."
        ),
        **facts,
    )


CHECKS = (
    ("sandbox.provider", check_sandbox_selection),
)

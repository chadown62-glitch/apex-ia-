"""Contrôles du Doctor propres au profil AUTONOME (`apex.standalone`, application `APEX.exe`).

Le profil autonome n'utilise ni WSL2, ni Docker, ni PostgreSQL/Redis conteneurisés : les contrôles qui
mesurent CETTE infrastructure deviennent « sans objet » (SKIP, avec la raison) — jamais PASS, puisqu'ils ne
mesurent rien ici. Ce qui compte réellement pour ce profil est mesuré à la place : l'hôte (système réel), la
base SQLite embarquée (connexion + révision du schéma). L'isolation du code exécuté reste mesurée par
`sandbox.provider` (`apex doctor --full`) et revérifiée à chaque usage — jamais supposée.
"""

from __future__ import annotations

from typing import Any

from apex.doctor.check import CheckContext, CheckResult, Severity, result

CATEGORY = "profil autonome"

#: Contrôles d'infrastructure serveur remplacés par « sans objet » en profil autonome, avec leur raison.
NOT_APPLICABLE: dict[str, str] = {
    "host.cgroups": "mécanisme Linux de l'infrastructure serveur — les limites du code exécuté sont appliquées "
                    "par le backend d'isolation de l'hôte (voir sandbox.provider)",
    "wsl.interop": "aucune dépendance à WSL",
    "wsl.systemd": "aucune dépendance à WSL",
    "fs.placement": "données sous APEX_HOME — aucune contrainte ext4/WSL2",
    "docker.present": "base embarquée : Docker n'est pas requis",
    "docker.flavour": "Docker n'est pas requis",
    "docker.isolation": "Docker n'est pas requis",
    "service.postgres": "base SQLite embarquée (APEX_DATABASE_URL)",
    "service.postgres.auth": "base SQLite embarquée (APEX_DATABASE_URL)",
    "service.redis": "aucun service Redis dans ce profil",
    "service.minio": "aucun service MinIO dans ce profil",
}


def check_host_platform_standalone(ctx: CheckContext) -> CheckResult:
    info = ctx.probe.platform_info()
    system = info.get("system")
    if system not in ("Windows", "Linux"):
        return result("host.platform", CATEGORY, Severity.FAIL,
                      f"système « {system} » non pris en charge par APEX autonome (Windows ou Linux)", **info)
    label = "Windows natif" if system == "Windows" else "Linux"
    return result("host.platform", CATEGORY, Severity.PASS,
                  f"{label} {info.get('release', '')} — profil autonome (sans WSL ni Docker)".replace("  ", " "),
                  kind=label, **info)


def check_local_database(ctx: CheckContext) -> CheckResult:
    """Base embarquée RÉELLEMENT ouverte et schéma à la révision de CE code — sinon `apex db upgrade`."""
    name = "database.local"
    if ctx.config is None:
        return result(name, CATEGORY, Severity.SKIP, "configuration non chargée")
    try:
        from apex.db import schema
        from apex.db.engine import build_engine, resolve_database_url
    except ImportError:
        return result(name, CATEGORY, Severity.FAIL, "extras « db » (SQLAlchemy, Alembic) non installés")
    try:
        url = resolve_database_url(ctx.config)
    except Exception as exc:
        return result(name, CATEGORY, Severity.FAIL, f"URL de base introuvable : {exc}"[:300])
    if not url.startswith("sqlite"):
        return result(name, CATEGORY, Severity.WARN, "base non embarquée (APEX_DATABASE_URL n'est pas SQLite)")
    try:
        state = schema.schema_state(build_engine(url))
    except Exception as exc:
        return result(name, CATEGORY, Severity.FAIL,
                      f"base embarquée illisible : {exc.__class__.__name__}: {exc}"[:300],
                      remediation="Fermer les autres instances d'APEX ; vérifier les droits sur APEX_HOME\\data.")
    if not state.ok:
        return result(name, CATEGORY, Severity.FAIL, f"base embarquée : {state.describe()}",
                      remediation="apex db upgrade (fait automatiquement par `python -m apex.standalone serve`)")
    return result(name, CATEGORY, Severity.PASS, f"base SQLite embarquée — {state.describe()}",
                  revision=state.current)


def profile_checks(base: tuple[Any, ...]) -> tuple[Any, ...]:
    """Les contrôles du profil autonome : mêmes contrôles que `base`, sauf ceux d'infrastructure serveur
    (« sans objet », raison donnée) et `host.platform` (Windows accepté) ; plus `database.local`."""
    out = []
    for name, func in base:
        if name in NOT_APPLICABLE:
            out.append((name, _not_applicable(name, NOT_APPLICABLE[name])))
        elif name == "host.platform":
            out.append((name, check_host_platform_standalone))
        elif name in STANDALONE_REMEDIATION:
            out.append((name, _with_remediation(func, STANDALONE_REMEDIATION[name])))
        else:
            out.append((name, func))
    out.append(("database.local", check_local_database))
    return tuple(out)


#: Remèdes du profil serveur (docker compose, pip, `apex models fetch`) remplacés par les gestes du profil
#: autonome : la mesure et la sévérité restent celles du contrôle d'origine, seul le conseil change.
STANDALONE_REMEDIATION = {
    "service.ollama": ("Ollama est facultatif en profil autonome (APEX exécute son modèle natif lui-même) : "
                       "installez-le depuis https://ollama.com seulement si vous voulez aussi ses modèles."),
    "models.native": ("Panel APEX : « Préparer APEX » (bibliothèques d'inférence), puis « Installer le modèle » "
                      "(téléchargement unique) — ou `python -m apex.standalone model install`."),
}


def _with_remediation(func, remediation: str):  # type: ignore[no-untyped-def]
    import dataclasses

    def check(ctx: CheckContext) -> CheckResult:
        outcome: CheckResult = func(ctx)
        return dataclasses.replace(outcome, remediation=remediation) if outcome.remediation else outcome

    return check


def _not_applicable(name: str, reason: str):  # type: ignore[no-untyped-def]
    def check(_ctx: CheckContext) -> CheckResult:
        return result(name, CATEGORY, Severity.SKIP, f"sans objet en profil autonome : {reason}")

    return check

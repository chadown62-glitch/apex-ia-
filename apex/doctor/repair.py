"""Auto-réparation — passe « Self-Healing Launcher » (N+19, AXE B).

`apex.doctor.check`/`apex.util.probe.Probe` sont délibérément EN LECTURE
SEULE (§53 : « le doctor diagnostique, il ne répare pas ») — ce module est
la SEULE couche du dépôt autorisée à produire un effet de bord à partir
d'un diagnostic `apex doctor`, et elle ne le fait JAMAIS en réinterprétant
les checks eux-mêmes : elle les CONSOMME (`CheckResult`, déjà réel) et
décide, par une classification EXPLICITE et FERMÉE (`RepairAction`), ce qui
est sûr à réparer automatiquement.

**Principe absolu** : une réparation n'est JAMAIS déclenchée par défaut sur
un check inconnu de cette classification — l'absence de règle explicite
donne `MANUAL_REQUIRED`, jamais `SAFE_AUTO_FIX` par défaut (liste blanche,
pas liste noire). Aucune réparation ne :
- supprime un volume Docker ;
- tue un processus externe (non-APEX) ;
- modifie `.wslconfig`/un driver GPU ;
- télécharge un modèle Ollama volumineux ;
- falsifie un résultat de check ou de Gate.

Chaque exécution réelle passe par un `CommandRunner` injectable (même
discipline que `apex.util.probe.Probe`) — testable sans toucher au système,
et `dry_run=True` ne construit le plan que pour AFFICHAGE, sans jamais
appeler ce runner."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from apex.doctor.check import CheckResult, Severity


class RepairAction(str, Enum):
    #: Réparation déterministe, sûre, sans effet secondaire irréversible —
    #: exécutée automatiquement (sauf `--dry-run`).
    SAFE_AUTO_FIX = "safe_auto_fix"
    #: Rien à réparer directement — juste attendre puis re-sonder (ex. un
    #: service qui démarre lentement).
    RETRY_ONLY = "retry_only"
    #: Nécessite une décision/action humaine — jamais tenté automatiquement.
    MANUAL_REQUIRED = "manual_required"
    #: Une réparation existerait techniquement mais `PolicyEngine`/la
    #: politique administrateur l'interdit dans ce contexte (ex. mode
    #: production verrouillé) — distinct de `MANUAL_REQUIRED` (ici, même un
    #: humain devrait passer par une voie de changement explicite, pas ce
    #: launcher).
    BLOCKED_BY_POLICY = "blocked_by_policy"


class CommandRunner(Protocol):
    def __call__(self, argv: tuple[str, ...], *, timeout_s: float) -> subprocess.CompletedProcess: ...


def real_command_runner(argv: tuple[str, ...], *, timeout_s: float) -> subprocess.CompletedProcess:
    """Le SEUL point du module qui touche réellement le système — jamais
    appelé quand `dry_run=True` (voir `apply_repairs`)."""
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout_s)


@dataclass(frozen=True)
class RepairPlan:
    check_name: str
    action: RepairAction
    reason: str
    #: Argv RÉEL qui serait exécuté — `None` sauf pour `SAFE_AUTO_FIX`.
    command: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RepairOutcome:
    plan: RepairPlan
    attempted: bool
    succeeded: bool | None
    detail: str


#: Liste blanche FERMÉE : nom de check -> fonction qui, à partir des `facts`
#: RÉELS déjà produits par ce check (jamais recalculés ici), retourne soit
#: une commande de réparation sûre (`tuple[str, ...]`), soit `None` (pas de
#: réparation connue pour cet état précis du check — reste MANUAL_REQUIRED/
#: RETRY_ONLY). Ajouter une entrée ici est le SEUL moyen d'activer un
#: `SAFE_AUTO_FIX` — jamais une règle générique par gravité.
def _repair_docker_present(result: CheckResult) -> tuple[str, ...] | None:
    # `docker.present` FAIL avec docker installé mais le démon injoignable
    # (`apex.doctor.checks.platform.check_docker`, message exact "démon
    # Docker injoignable") — démarrer le service est déterministe et
    # réversible (`service docker stop` existe symétriquement), jamais une
    # réparation destructrice.
    if "démon Docker injoignable" in result.message:
        return ("service", "docker", "start")
    return None


#: Les quatre services `_service_check()` de `apex.doctor.checks.services`
#: (`service.postgres`/`service.redis`/`service.minio`/`service.ollama`)
#: portent TOUS la même remédiation littérale "docker compose up -d" — la
#: même commande DÉCLARATIVE et idempotente que le launcher existant
#: (`scripts/apex-launch.sh::compose_up`) exécute déjà au démarrage normal.
#: `-d <service>` restreint la portée au SEUL service en échec (jamais
#: `docker compose up -d` nu, qui (re)créerait tout) — un service déjà en
#: bonne santé n'est jamais touché.
_COMPOSE_SERVICE_CHECKS = frozenset({"service.postgres", "service.redis", "service.minio", "service.ollama"})


def _repair_compose_service(result: CheckResult) -> tuple[str, ...] | None:
    if result.remediation != "docker compose up -d":
        return None
    host = result.facts.get("host")
    port = result.facts.get("port")
    if not isinstance(host, str) or not isinstance(port, int):
        return None
    service_name = result.name.removeprefix("service.")
    return ("docker", "compose", "up", "-d", service_name)


def _repair_missing_data_root(result: CheckResult) -> tuple[str, ...] | None:
    # `fs.data_root` WARN avec `exists=False` (apex.doctor.checks.
    # environment.check_data_root, N+26) : créer le répertoire est sûr et
    # explicitement autorisé par la mission (« création de répertoires
    # manquants ») — `mkdir -p` ne touche jamais un répertoire déjà
    # existant, jamais destructeur, jamais de suppression.
    if result.facts.get("exists") is False:
        path = result.facts.get("path")
        if isinstance(path, str) and path:
            return ("mkdir", "-p", path)
    return None


_SAFE_REPAIR_RULES: dict[str, Any] = {
    "docker.present": _repair_docker_present,
    "fs.data_root": _repair_missing_data_root,
    **{name: _repair_compose_service for name in _COMPOSE_SERVICE_CHECKS},
}

#: Checks dont l'échec n'a structurellement AUCUNE réparation automatique
#: sûre — toujours MANUAL_REQUIRED, jamais évalués par `_SAFE_REPAIR_RULES`
#: (limite matérielle/hôte, jamais un fichier/service APEX peut créer/
#: démarrer lui-même).
_ALWAYS_MANUAL = frozenset(
    {
        "gpu.present", "gpu.config_match", "docker.isolation", "docker.flavour",
        "fs.placement", "fs.space",
    }
)


def classify(result: CheckResult) -> RepairPlan:
    """Classifie UN `CheckResult` déjà produit par `apex doctor` — jamais un
    second diagnostic recalculé ici. `PASS`/`SKIP` -> `RETRY_ONLY` trivial
    (rien à faire). `WARN`/`FAIL` sur un check de `_ALWAYS_MANUAL` ->
    `MANUAL_REQUIRED` inconditionnel. Sinon, seule une règle EXPLICITE de
    `_SAFE_REPAIR_RULES` qui retourne une commande réelle produit
    `SAFE_AUTO_FIX` — l'absence de règle, ou une règle qui retourne `None`
    pour cet état précis, retombe sur `MANUAL_REQUIRED` (liste blanche)."""
    if result.severity in (Severity.PASS, Severity.SKIP):
        return RepairPlan(
            check_name=result.name, action=RepairAction.RETRY_ONLY,
            reason="aucun problème détecté — rien à réparer",
        )
    if result.name in _ALWAYS_MANUAL:
        return RepairPlan(
            check_name=result.name, action=RepairAction.MANUAL_REQUIRED,
            reason="limite matérielle/hôte — aucune réparation automatique sûre n'existe",
        )
    rule = _SAFE_REPAIR_RULES.get(result.name)
    if rule is not None:
        command = rule(result)
        if command is not None:
            return RepairPlan(
                check_name=result.name, action=RepairAction.SAFE_AUTO_FIX,
                reason=result.message, command=command,
            )
    return RepairPlan(
        check_name=result.name, action=RepairAction.MANUAL_REQUIRED,
        reason=result.remediation or result.message or "aucune règle de réparation automatique connue",
    )


def plan_repairs(results: tuple[CheckResult, ...]) -> tuple[RepairPlan, ...]:
    return tuple(classify(r) for r in results)


def apply_repairs(
    plans: tuple[RepairPlan, ...],
    *,
    dry_run: bool,
    runner: CommandRunner = real_command_runner,
    timeout_s: float = 30.0,
) -> tuple[RepairOutcome, ...]:
    """N'exécute RÉELLEMENT que les `SAFE_AUTO_FIX` — tout le reste produit
    un `RepairOutcome` sans effet (`attempted=False`). `dry_run=True` :
    `attempted` reste `False` pour CHAQUE plan, même `SAFE_AUTO_FIX` —
    `runner` n'est alors jamais appelé (§26 : « diagnostiquer et afficher
    les réparations sans les exécuter »)."""
    outcomes: list[RepairOutcome] = []
    for plan in plans:
        if plan.action is not RepairAction.SAFE_AUTO_FIX:
            outcomes.append(
                RepairOutcome(plan=plan, attempted=False, succeeded=None, detail="aucune action tentée")
            )
            continue
        if dry_run:
            outcomes.append(
                RepairOutcome(
                    plan=plan, attempted=False, succeeded=None,
                    detail=f"dry-run — commande qui serait exécutée : {' '.join(plan.command or ())}",
                )
            )
            continue
        assert plan.command is not None
        try:
            completed = runner(plan.command, timeout_s=timeout_s)
        except Exception as exc:  # une réparation qui échoue ne bloque jamais le launcher
            outcomes.append(
                RepairOutcome(
                    plan=plan, attempted=True, succeeded=False,
                    detail=f"exception lors de la réparation : {exc.__class__.__name__}: {exc}",
                )
            )
            continue
        succeeded = completed.returncode == 0
        outcomes.append(
            RepairOutcome(
                plan=plan, attempted=True, succeeded=succeeded,
                detail=(completed.stdout or completed.stderr or "").strip()[:500],
            )
        )
    return outcomes

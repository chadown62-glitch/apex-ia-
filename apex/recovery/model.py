"""Modèle de domaine du Recovery — N+26. Dataclasses/énumérations pures,
aucune dépendance DB (même séparation que `apex.missions.model`/
`apex.sandbox.model` vis-à-vis de leur couche de persistance).

**`FailureCategory` (ce module) N'EST PAS `apex.metalearner.failure_taxonomy.
FailureCategory`** — les deux coexistent délibérément, à des couches
différentes :

- `apex.metalearner.failure_taxonomy.FailureCategory` classe un `EvalRun`
  (un CANDIDAT généré, jugé) — c'est la couche « quel contenu produire
  différemment » (température, suffixe de prompt), déjà décidée par
  `apex.metalearner.adaptive_solution_loop.decide_next_strategies`/
  `_mutate_from_experience`. Ce module N'Y TOUCHE JAMAIS.
- `apex.recovery.model.FailureCategory` (ici) classe l'échec d'une
  EXÉCUTION (sandbox, provider modèle) — la couche « où/comment relancer »
  (même clone, nouveau clone, nouveau pool, ou abandonner). C'est une
  question d'INFRASTRUCTURE, jamais de contenu.

Une mission qui échoue peut ne JAMAIS produire de `EvalRun` du tout
(`apex.missions.execution.launch_mission` exécute une commande sandboxée
directe, pas un candidat jugé) — ce module doit donc classifier depuis des
preuves d'exécution (`apex.sandbox.model.ExecutionStatus`,
`apex.errors.IsolationUnavailable`, `apex.errors.ModelProviderError`),
jamais depuis un `EvalRun` qui pourrait ne pas exister.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FailureCategory(str, Enum):
    """Vocabulaire FERMÉ — exactement les neuf catégories de la mission N+26.
    Chaque valeur doit être atteignable depuis une preuve RÉELLE
    (`apex.sandbox.model.ExecutionStatus`, `apex.errors.IsolationUnavailable`,
    `apex.errors.ModelProviderError.category`) — voir `apex.recovery.classify`,
    le SEUL module qui produit une valeur de cette énumération à partir d'une
    preuve. Jamais une catégorie devinée depuis un texte libre."""

    #: Isolation demandée indisponible (`IsolationUnavailable`) — structurel,
    #: ne se résout pas en réessayant la même exécution.
    SANDBOX_FAILURE = "sandbox_failure"
    #: `ExecutionStatus.TIMEOUT` — le budget de temps a expiré.
    TIMEOUT = "timeout"
    #: `ExecutionStatus.CANCELLED` — annulation explicitement demandée
    #: (Scheduler) : jamais une cause de relance automatique.
    CANCELLED = "cancelled"
    #: Le candidat/argv fourni est structurellement invalide (ex. commande
    #: introuvable) — vaut la peine d'être relancé avec une stratégie
    #: différente, jamais une simple répétition à l'identique.
    INVALID_CANDIDATE = "invalid_candidate"
    #: `ExecutionStatus.FAILED`/`NETWORK_DENIED`/`PERMISSION_DENIED` générique
    #: — le processus a démarré et s'est terminé en erreur, sans catégorie
    #: plus précise applicable.
    EXECUTION_FAILURE = "execution_failure"
    #: Un Judge a évalué le résultat et l'a jugé incorrect — nécessite un
    #: contexte de feedback pour la tentative suivante, jamais une simple
    #: répétition. Aujourd'hui, `launch_mission` n'invoque aucun Judge —
    #: cette catégorie est câblée et testée au niveau politique pour un futur
    #: appelant qui en produirait une (voir `apex.judge`), jamais fabriquée.
    INCORRECT_SOLUTION = "incorrect_solution"
    #: Un `ModelProvider` a été atteint mais a répondu en erreur
    #: (`ModelErrorCategory.RATE_LIMITED`/`AUTH_ERROR`/`CONTEXT_TOO_LONG`/
    #: `PROVIDER_ERROR`/`TIMEOUT`/`UNKNOWN`).
    PROVIDER_FAILURE = "provider_failure"
    #: Un `ModelProvider` était injoignable (`ModelErrorCategory.OFFLINE`) —
    #: distinct de `PROVIDER_FAILURE` : ici, aucune réponse n'a jamais été
    #: obtenue, un provider alternatif est la seule voie de progression.
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    #: `ExecutionStatus.OOM_KILLED`/`RESOURCE_LIMIT_EXCEEDED`/
    #: `PROCESS_LIMIT_EXCEEDED`/`DISK_LIMIT_EXCEEDED` — une limite de
    #: ressource réelle a été atteinte.
    RESOURCE_EXHAUSTED = "resource_exhausted"


class RecoveryAction(str, Enum):
    """Vocabulaire FERMÉ des décisions de `RecoveryPolicy.decide` — jamais
    une chaîne libre. Une nouvelle action exigerait une revue explicite de
    chaque appelant (`apex.missions.execution`), pas un ajout silencieux."""

    #: Relancer une NOUVELLE tentative (nouveau clone, même pool) — jamais
    #: une reprise du clone en échec (`AgentRuntime.recover_orphan`/
    #: `RecoveryService` documentent déjà pourquoi une reprise est refusée).
    RETRY = "retry"
    #: Relancer sur un pool/provider RÉELLEMENT alternatif — jamais choisi
    #: au seul motif qu'une catégorie « le suggère » : voir
    #: `RecoveryDecision.reassignment_target`.
    REASSIGN = "reassign"
    #: Terminal — la mission passe à `MissionState.FAILED`, aucune autre
    #: tentative. Décision par défaut pour tout ce qui n'a pas de chemin de
    #: progression réel (budget épuisé, catégorie structurelle, annulation).
    BLOCK = "block"
    #: Terminal comme `BLOCK`, mais signale explicitement un besoin
    #: d'attention humaine prioritaire (ex. `INCORRECT_SOLUTION` après
    #: épuisement du budget de tentatives) — même transition Mission
    #: (`FAILED`, aucun état « escaladé » n'existe dans la machine à états),
    #: mais un événement `recovery.escalated` distinct et un préfixe
    #: reconnaissable dans `Mission.error`, jamais confondu avec un blocage
    #: silencieux.
    ESCALATE = "escalate"


@dataclass(frozen=True)
class RecoveryDecision:
    """Résultat déterministe et observable de `RecoveryPolicy.decide` — la
    trace exige ces champs explicitement (§7-8 de la mission N+26), jamais
    une simple valeur d'énumération sans justification."""

    action: RecoveryAction
    category: FailureCategory
    reason: str
    attempt_number: int
    max_attempts: int
    #: Description de ce qui change pour la tentative suivante (ex.
    #: "nouveau clone, même pool", "feedback du judge incorporé") — `None`
    #: pour `BLOCK`/`ESCALATE` (aucune tentative suivante).
    strategy_change: str | None = None
    #: Résumé de l'échec qui a motivé cette décision — porté explicitement
    #: (jamais seulement déductible depuis les logs) pour que la tentative
    #: suivante, ou un humain relisant la trace, ait le contexte sans
    #: reconstituer l'historique.
    previous_failure: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "category": self.category.value,
            "reason": self.reason,
            "attempt_number": self.attempt_number,
            "max_attempts": self.max_attempts,
            "strategy_change": self.strategy_change,
            "previous_failure": self.previous_failure,
        }


@dataclass(frozen=True)
class AttemptRecord:
    """Une tentative d'exécution de mission — persistée via un événement
    (`EventRepository`, `apex.missions.execution`), jamais une nouvelle
    table : le contenu est entièrement structuré (`to_dict`), donc
    exploitable depuis `context` d'un événement existant sans migration.
    Porte exactement les champs requis par §6 de la mission N+26."""

    attempt_number: int
    provider_name: str
    sandbox_level: str
    category: FailureCategory | None  # None si succès
    reason: str
    strategy: str | None
    judge_result: str | None
    started_at: datetime
    duration_s: float
    reassigned: bool
    decision: RecoveryAction | None  # None pour la dernière tentative réussie

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_number": self.attempt_number,
            "provider_name": self.provider_name,
            "sandbox_level": self.sandbox_level,
            "category": self.category.value if self.category is not None else None,
            "reason": self.reason,
            "strategy": self.strategy,
            "judge_result": self.judge_result,
            "started_at": self.started_at.isoformat(),
            "duration_s": round(self.duration_s, 3),
            "reassigned": self.reassigned,
            "decision": self.decision.value if self.decision is not None else None,
        }


@dataclass
class AttemptHistory:
    """Accumulateur en mémoire d'un seul appel `launch_mission` — jamais
    persisté tel quel (chaque `AttemptRecord` l'est individuellement, via
    événement, au fur et à mesure) ; utile pour construire
    `RecoveryDecision.previous_failure` sans requêter la base."""

    records: list[AttemptRecord] = field(default_factory=list)

    def add(self, record: AttemptRecord) -> None:
        self.records.append(record)

    @property
    def attempt_count(self) -> int:
        return len(self.records)

    @property
    def last(self) -> AttemptRecord | None:
        return self.records[-1] if self.records else None

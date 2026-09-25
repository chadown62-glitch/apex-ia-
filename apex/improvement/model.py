"""Vocabulaire du Continuous Improvement Engine — dataclasses PURES, aucune
DB, aucun effet de bord.

Ce module ne contient QUE ce que l'audit de `apex/metalearner/` a trouvé
manquant (voir `docs/CONTINUOUS_IMPROVEMENT.md`) :

- la description STATIQUE d'une capacité (`CapabilitySpec`) — distincte de
  `apex.metalearner.capability_model.CapabilityBenchmarkRun` (côté mesure
  d'un candidat de l'évolution) et d'`apex.interfaces.registry.Abstraction`
  (maturité d'un MODULE, pas d'une capacité en cours d'amélioration) ;
- un niveau de preuve fermé (`EvidenceLevel`) — `JudgeEvidence.kind`
  (`apex.metalearner.judge_model`) reste une étiquette libre de SOURCE,
  jamais une force de preuve ;
- le verdict d'un cycle (`ImprovementVerdict`) — aucun des axes existants
  (`ImplStatus`, `MemberStatus`, `JudgeDecision`) ne dit « la version N+1
  est-elle meilleure que la version N » ;
- le statut d'une hypothèse de cause (`HypothesisStatus`) — `apex.
  metalearner.judge_feedback_model.FailureCategory` attribue un échec,
  jamais ne distingue une cause probable d'une cause confirmée.

Le JUGEMENT lui-même n'est pas redéfini ici : un cycle porte un
`apex.metalearner.judge_model.GauntletResult`, produit par l'agrégation
stricte (véto) déjà réelle de `apex.metalearner.gauntlet.GauntletRunner`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utcnow() -> datetime:
    return datetime.now(UTC)


class EvidenceLevel(StrEnum):
    #: Constaté par lecture directe (code, sortie, état) — pas exécuté.
    OBSERVED = "observed"
    #: Vérifié par un test exécuté (pass/fail binaire).
    TESTED = "tested"
    #: Quantifié par une mesure répétable (benchmark, métrique).
    MEASURED = "measured"
    #: Décidé par le juge externe (agrégation `GauntletRunner`).
    JUDGED = "judged"
    #: Déduit d'autres preuves, jamais vérifié directement.
    INFERRED = "inferred"
    #: Affirmé sans preuve disponible.
    UNVERIFIED = "unverified"
    #: Impossible à vérifier dans cet environnement (dépendance absente).
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Evidence:
    claim: str
    level: EvidenceLevel
    source: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "level": self.level.value,
            "source": self.source,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Evidence:
        return cls(
            claim=data["claim"],
            level=EvidenceLevel(data["level"]),
            source=data["source"],
            detail=data.get("detail", ""),
        )


class MetricKind(StrEnum):
    #: Rejouer la mesure sur le même code produit EXACTEMENT les mêmes
    #: échantillons (vérifié par le moteur, jamais supposé) — toute
    #: différence entre deux versions est alors une différence réelle.
    DETERMINISTIC = "deterministic"
    #: La mesure contient du bruit — une différence n'est retenue que si
    #: `apex.stattest.StatTest.compare_paired` la juge significative.
    STOCHASTIC = "stochastic"


@dataclass(frozen=True)
class MetricSpec:
    name: str
    description: str
    higher_is_better: bool
    kind: MetricKind = MetricKind.DETERMINISTIC
    #: En dessous, la comparaison est INCONCLUSIVE — jamais une conclusion
    #: tirée d'un échantillon trop petit.
    min_samples: int = 1


@dataclass(frozen=True)
class CapabilitySpec:
    """Description STATIQUE d'une capacité — ce qui ne change pas d'une
    mesure à l'autre. `tests` référence des tests RÉELS du dépôt (chemins
    pytest), jamais une intention."""

    capability_id: str
    name: str
    version: str
    description: str
    components: tuple[str, ...]
    dependencies: tuple[str, ...]
    preconditions: tuple[str, ...]
    tests: tuple[str, ...]
    metrics: tuple[MetricSpec, ...]
    success_criteria: tuple[str, ...]
    risks: tuple[str, ...]
    maturity: str

    def metric(self, name: str) -> MetricSpec:
        for metric in self.metrics:
            if metric.name == name:
                return metric
        raise KeyError(f"métrique « {name} » non déclarée pour « {self.capability_id} »")


@dataclass(frozen=True)
class BenchmarkSnapshot:
    """Mesure d'UNE version — `samples[m]` porte les échantillons bruts
    (un par cas du benchmark, dans un ordre STABLE pour permettre
    l'appariement), `blocked[m]` la raison exacte pour laquelle une métrique
    n'a pas pu être mesurée ici. Une métrique n'est jamais à la fois dans
    les deux."""

    capability_id: str
    version: str
    samples: dict[str, tuple[float, ...]]
    blocked: dict[str, str] = field(default_factory=dict)
    #: `True` uniquement si le moteur a RÉELLEMENT mesuré deux fois et
    #: obtenu des échantillons identiques pour chaque métrique déterministe.
    reproducible: bool | None = None
    notes: tuple[str, ...] = ()
    measured_at: datetime = field(default_factory=utcnow)

    def mean(self, metric: str) -> float | None:
        values = self.samples.get(metric)
        if not values:
            return None
        return sum(values) / len(values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "samples": {k: list(v) for k, v in self.samples.items()},
            "blocked": dict(self.blocked),
            "reproducible": self.reproducible,
            "notes": list(self.notes),
            "measured_at": self.measured_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchmarkSnapshot:
        return cls(
            capability_id=data["capability_id"],
            version=data["version"],
            samples={k: tuple(float(x) for x in v) for k, v in data["samples"].items()},
            blocked=dict(data.get("blocked", {})),
            reproducible=data.get("reproducible"),
            notes=tuple(data.get("notes", ())),
            measured_at=datetime.fromisoformat(data["measured_at"]),
        )


class ImprovementVerdict(StrEnum):
    IMPROVED = "improved"
    UNCHANGED = "unchanged"
    REGRESSED = "regressed"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"
    #: La nouvelle version est REJETÉE par le juge externe — quelle que
    #: soit l'évolution des métriques (un gain mesuré ne rachète jamais un
    #: véto, même discipline que `apex.metalearner.gauntlet`).
    FAILED = "failed"


@dataclass(frozen=True)
class MetricComparison:
    metric: str
    verdict: ImprovementVerdict
    before_mean: float | None
    after_mean: float | None
    delta: float | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "verdict": self.verdict.value,
            "before_mean": self.before_mean,
            "after_mean": self.after_mean,
            "delta": self.delta,
            "reason": self.reason,
        }


class CycleKind(StrEnum):
    #: « Est-ce que ça fonctionne réellement ? » — chemins morts,
    #: intégrations manquantes, faux E2E.
    FUNCTIONALITY = "functionality"
    #: « Comment le casser volontairement ? » — entrées hostiles, pannes,
    #: contournement du jugement.
    ADVERSARIAL = "adversarial"
    #: « Qu'est-ce qui peut encore être meilleur ? » — même si tout passe.
    IMPROVEMENT = "improvement"


class HypothesisStatus(StrEnum):
    #: Cause plausible, pas encore reproduite — jamais présentée comme un fait.
    PROBABLE = "probable"
    #: Reproduite par un test indépendant du constat initial.
    CONFIRMED = "confirmed"
    #: Le test de reproduction ne reproduit pas le problème.
    REJECTED = "rejected"


@dataclass(frozen=True)
class Finding:
    """OBSERVED -> HYPOTHESIS -> TEST -> CONFIRMED/REJECTED -> FIX ->
    REGRESSION TEST. `proposed_fix` est une PROPOSITION : le moteur ne
    modifie jamais le code lui-même."""

    finding_id: str
    probe_id: str
    severity: str
    observed: str
    hypothesis: str
    status: HypothesisStatus
    reproduction: str
    proposed_fix: str
    regression_test: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "probe_id": self.probe_id,
            "severity": self.severity,
            "observed": self.observed,
            "hypothesis": self.hypothesis,
            "status": self.status.value,
            "reproduction": self.reproduction,
            "proposed_fix": self.proposed_fix,
            "regression_test": self.regression_test,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        return cls(
            finding_id=data["finding_id"],
            probe_id=data["probe_id"],
            severity=data["severity"],
            observed=data["observed"],
            hypothesis=data["hypothesis"],
            status=HypothesisStatus(data["status"]),
            reproduction=data["reproduction"],
            proposed_fix=data["proposed_fix"],
            regression_test=data["regression_test"],
        )


@dataclass(frozen=True)
class ImplementerClaim:
    """Ce que l'IMPLÉMENTEUR affirme de sa propre modification — enregistré,
    jamais cru : le verdict du cycle vient du juge et de la mesure. Un
    `author_id` identique au `judge_id` est refusé par le moteur."""

    author_id: str
    summary: str
    claimed_verdict: ImprovementVerdict


@dataclass(frozen=True)
class CycleRecord:
    """Enregistrement expérimental d'UN cycle — append-only dans
    `apex.improvement.ledger`. Référence le `GauntletResult` par son
    contenu sérialisé (jamais un second format de jugement)."""

    capability_id: str
    cycle_id: str
    cycle_index: int
    kind: CycleKind
    version_before: str
    version_after: str
    hypothesis: str
    changes: tuple[str, ...]
    tests_executed: tuple[str, ...]
    before: BenchmarkSnapshot
    after: BenchmarkSnapshot
    comparisons: tuple[MetricComparison, ...]
    judge_id: str
    judge: dict[str, Any]
    findings: tuple[Finding, ...]
    claim: ImplementerClaim
    claim_contradicted: bool
    verdict: ImprovementVerdict
    verdict_reason: str
    evidence: tuple[Evidence, ...]
    started_at: datetime
    duration_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "cycle_id": self.cycle_id,
            "cycle_index": self.cycle_index,
            "kind": self.kind.value,
            "version_before": self.version_before,
            "version_after": self.version_after,
            "hypothesis": self.hypothesis,
            "changes": list(self.changes),
            "tests_executed": list(self.tests_executed),
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "comparisons": [c.to_dict() for c in self.comparisons],
            "judge_id": self.judge_id,
            "judge": self.judge,
            "findings": [f.to_dict() for f in self.findings],
            "claim": {
                "author_id": self.claim.author_id,
                "summary": self.claim.summary,
                "claimed_verdict": self.claim.claimed_verdict.value,
            },
            "claim_contradicted": self.claim_contradicted,
            "verdict": self.verdict.value,
            "verdict_reason": self.verdict_reason,
            "evidence": [e.to_dict() for e in self.evidence],
            "started_at": self.started_at.isoformat(),
            "duration_s": self.duration_s,
        }

"""`ContinuousImprovementEngine` — applique la même boucle à n'importe quelle
capacité décrite par un `CapabilityAdapter` :

    BASELINE (mesure, reproductibilité VÉRIFIÉE)
      -> [le code change — fait par un implémenteur, jamais par le moteur]
      -> MESURE de la nouvelle version
      -> JUGE EXTERNE (sondes de réfutation, agrégation GauntletRunner)
      -> DEBUGGER (hypothèse -> reproduction -> CONFIRMED/REJECTED/PROBABLE)
      -> VERDICT (métriques + juge, jamais la seule affirmation de l'auteur)
      -> LEDGER (append-only)

Le moteur ne modifie JAMAIS de code et ne déclare jamais une amélioration
que la mesure ne montre pas. L'affirmation de l'implémenteur est
enregistrée et marquée `claim_contradicted` si le verdict la contredit.

Ce que l'implémenteur ne peut PAS fournir lui-même : un snapshot (avant ou
après) qui n'a pas été mesuré par le moteur et consigné dans le ledger, une
décision de juge (le moteur agrège lui-même les résultats par sonde avec
`GauntletRunner`), ni un ledger modifié après coup (chaîne vérifiée)."""

from __future__ import annotations

import time
import uuid
from typing import Protocol

from apex.improvement.judge import Judge, JudgeAssessment, SelfConfirmationError
from apex.improvement.ledger import ImprovementLedger
from apex.improvement.measure import compare_snapshots
from apex.improvement.model import (
    ENGINE_MEASUREMENT,
    BenchmarkSnapshot,
    CapabilitySpec,
    CycleKind,
    CycleRecord,
    Evidence,
    EvidenceLevel,
    Finding,
    HypothesisStatus,
    ImplementerClaim,
    ImprovementVerdict,
    MetricComparison,
    MetricKind,
    utcnow,
)
from apex.metalearner.gauntlet import GauntletRunner
from apex.metalearner.judge_model import (
    GauntletResult,
    GauntletStage,
    JudgeDecision,
    JudgeDimension,
    JudgeResult,
)
from apex.stattest.comparator import StatTest


class CapabilityAdapter(Protocol):
    spec: CapabilitySpec

    def measure(self, version: str) -> BenchmarkSnapshot: ...

    def benchmark_fingerprint(self) -> str:
        """Empreinte du CONTENU du benchmark (corpus, nombre d'essais...)."""
        ...


class MissingBaselineError(ValueError):
    """Aucune baseline : une amélioration n'a pas de point de comparaison."""


class UnverifiedSnapshotError(ValueError):
    """Snapshot non produit par le moteur ou absent du ledger."""


_NO_JUDGE_RESULT = JudgeResult(
    judge_id="no-probe-result",
    judge_version="1",
    dimension=JudgeDimension.CORRECTNESS,
    decision=JudgeDecision.INCONCLUSIVE,
    score=None,
    confidence=0.0,
    rationale="le juge n'a rendu aucun résultat de sonde",
)


def _deterministic_metrics(spec: CapabilitySpec) -> tuple[str, ...]:
    return tuple(m.name for m in spec.metrics if m.kind is MetricKind.DETERMINISTIC)


def combine_verdict(
    metric_verdict: ImprovementVerdict,
    metric_reason: str,
    judge_decision: JudgeDecision,
    judge_reason: str,
) -> tuple[ImprovementVerdict, str]:
    """Une régression mesurée prime (le benchmark a parlé) ; sinon un REJECT
    du juge rend la version FAILED quel que soit le gain mesuré ; un juge
    INCONCLUSIVE empêche de confirmer une amélioration."""
    if metric_verdict is ImprovementVerdict.REGRESSED:
        return metric_verdict, f"{metric_reason} (juge : {judge_reason})"
    if judge_decision is JudgeDecision.REJECT:
        return (
            ImprovementVerdict.FAILED,
            f"rejeté par le juge externe : {judge_reason} (mesure : {metric_reason})",
        )
    if judge_decision is JudgeDecision.INCONCLUSIVE and metric_verdict is ImprovementVerdict.IMPROVED:
        return ImprovementVerdict.INCONCLUSIVE, f"gain mesuré mais juge non concluant : {judge_reason}"
    return metric_verdict, f"{metric_reason} (juge : {judge_reason})"


def _judge_reason(gauntlet: GauntletResult) -> str:
    if gauntlet.veto:
        dimension = gauntlet.veto_dimension.value if gauntlet.veto_dimension else "?"
        return f"{gauntlet.decision.value}, véto sur {dimension}"
    return gauntlet.decision.value


def _evidence(
    comparisons: tuple[MetricComparison, ...],
    gauntlet: GauntletResult,
    results: tuple[JudgeResult, ...],
    findings: tuple[Finding, ...],
    claim: ImplementerClaim,
    verdict: ImprovementVerdict,
) -> tuple[Evidence, ...]:
    evidence: list[Evidence] = []
    for c in comparisons:
        if c.verdict is ImprovementVerdict.BLOCKED:
            level = EvidenceLevel.BLOCKED
        elif c.verdict is ImprovementVerdict.INCONCLUSIVE:
            level = EvidenceLevel.UNVERIFIED
        else:
            level = EvidenceLevel.MEASURED
        evidence.append(
            Evidence(
                claim=f"métrique {c.metric} : {c.verdict.value}",
                level=level,
                source="benchmark",
                detail=c.reason,
            )
        )
    evidence.append(
        Evidence(
            claim=f"juge externe : {gauntlet.decision.value}",
            level=EvidenceLevel.JUDGED,
            source=f"gauntlet:{gauntlet.id}",
            detail=_judge_reason(gauntlet),
        )
    )
    for r in results:
        evidence.append(
            Evidence(
                claim=f"sonde {r.judge_id} : {r.decision.value}",
                level=EvidenceLevel.TESTED
                if r.decision is not JudgeDecision.INCONCLUSIVE
                else EvidenceLevel.UNVERIFIED,
                source=f"probe:{r.judge_id}",
                detail=r.rationale,
            )
        )
    for f in findings:
        level = {
            HypothesisStatus.CONFIRMED: EvidenceLevel.TESTED,
            HypothesisStatus.REJECTED: EvidenceLevel.TESTED,
            HypothesisStatus.PROBABLE: EvidenceLevel.INFERRED,
        }[f.status]
        evidence.append(
            Evidence(
                claim=f"cause ({f.status.value}) : {f.hypothesis}",
                level=level,
                source=f"finding:{f.finding_id}",
                detail=f.reproduction,
            )
        )
    evidence.append(
        Evidence(
            claim=f"l'implémenteur affirme : {claim.claimed_verdict.value}",
            level=EvidenceLevel.MEASURED if claim.claimed_verdict is verdict else EvidenceLevel.UNVERIFIED,
            source=f"claim:{claim.author_id}",
            detail="confirmé par le verdict"
            if claim.claimed_verdict is verdict
            else "CONTREDIT par le verdict",
        )
    )
    return tuple(evidence)


class ContinuousImprovementEngine:
    def __init__(
        self,
        adapter: CapabilityAdapter,
        judge: Judge,
        ledger: ImprovementLedger,
        *,
        stat_test: StatTest | None = None,
        min_judge_confidence: float = 0.6,
    ) -> None:
        self._adapter = adapter
        self._judge = judge
        self._ledger = ledger
        self._stat_test = stat_test or StatTest()
        self._runner = GauntletRunner(min_confidence_threshold=min_judge_confidence)

    @property
    def spec(self) -> CapabilitySpec:
        return self._adapter.spec

    def measure(self, version: str, *, record: bool = True) -> BenchmarkSnapshot:
        """Mesure DEUX fois et vérifie que chaque métrique déterministe
        produit des échantillons identiques — la reproductibilité est un
        FAIT constaté, jamais une hypothèse."""
        first = self._adapter.measure(version)
        second = self._adapter.measure(version)
        deterministic = _deterministic_metrics(self.spec)
        mismatches = [
            m for m in deterministic if m in first.samples and first.samples.get(m) != second.samples.get(m)
        ]
        notes = list(first.notes)
        if mismatches:
            notes.append(f"NON reproductible sur : {', '.join(sorted(mismatches))}")
        if first.environment != second.environment:
            mismatches.append("environment")
            notes.append("environnement différent entre les deux mesures")
        snapshot = BenchmarkSnapshot(
            capability_id=first.capability_id,
            version=version,
            samples=first.samples,
            blocked=first.blocked,
            reproducible=not mismatches,
            notes=tuple(notes),
            definition_digest=self.spec.definition_digest(self._adapter.benchmark_fingerprint()),
            environment=dict(first.environment),
            measured_by=ENGINE_MEASUREMENT,
        )
        if record:
            self._ledger.append_snapshot(snapshot)
        return snapshot

    def _require_engine_measured(self, snapshot: BenchmarkSnapshot, label: str) -> None:
        if snapshot.measured_by != ENGINE_MEASUREMENT:
            raise UnverifiedSnapshotError(
                f"snapshot {label} « {snapshot.version} » non mesuré par le moteur — refusé"
            )
        if not self._ledger.contains_snapshot(snapshot):
            raise UnverifiedSnapshotError(
                f"snapshot {label} « {snapshot.version} » absent du ledger (ou modifié) — refusé"
            )

    def _aggregate(
        self, assessment: JudgeAssessment, *, cycle_index: int, version: str
    ) -> GauntletResult:
        return self._runner.run_stage(
            assessment.results or (_NO_JUDGE_RESULT,),
            stage=GauntletStage.BENCHMARK,
            specialty=self.spec.capability_id,
            generation=cycle_index,
            member_id=version,
        )

    def run_cycle(
        self,
        *,
        kind: CycleKind,
        cycle_index: int,
        before: BenchmarkSnapshot | None,
        version_after: str,
        hypothesis: str,
        changes: tuple[str, ...],
        tests_executed: tuple[str, ...],
        claim: ImplementerClaim,
        after: BenchmarkSnapshot | None = None,
    ) -> CycleRecord:
        if claim.author_id == self._judge.judge_id:
            raise SelfConfirmationError(
                f"« {claim.author_id} » est à la fois auteur et juge — cycle refusé"
            )
        if before is None:
            raise MissingBaselineError(
                f"aucune baseline pour « {self.spec.capability_id} » — mesurer AVANT de modifier"
            )
        self._ledger.require_intact()
        self._require_engine_measured(before, "avant")
        if after is not None:
            self._require_engine_measured(after, "après")
        started_at = utcnow()
        start = time.monotonic()
        cycle_id = f"{self.spec.capability_id}:c{cycle_index}:{uuid.uuid4().hex[:8]}"

        after_snapshot = after if after is not None else self.measure(version_after)
        metric_verdict, metric_reason, comparisons = compare_snapshots(
            self.spec,
            before,
            after_snapshot,
            stat_test=self._stat_test,
        )
        assessment = self._judge.assess(
            kind,
            author_id=claim.author_id,
            capability_id=self.spec.capability_id,
            cycle_index=cycle_index,
            version=version_after,
            cycle_id=cycle_id,
        )
        gauntlet = self._aggregate(assessment, cycle_index=cycle_index, version=version_after)
        findings = assessment.findings
        verdict, reason = combine_verdict(
            metric_verdict, metric_reason, gauntlet.decision, _judge_reason(gauntlet)
        )

        record = CycleRecord(
            capability_id=self.spec.capability_id,
            cycle_id=cycle_id,
            cycle_index=cycle_index,
            kind=kind,
            version_before=before.version,
            version_after=version_after,
            hypothesis=hypothesis,
            changes=changes,
            tests_executed=tests_executed,
            before=before,
            after=after_snapshot,
            comparisons=comparisons,
            judge_id=self._judge.judge_id,
            judge=gauntlet.to_dict(),
            findings=findings,
            claim=claim,
            claim_contradicted=claim.claimed_verdict is not verdict,
            verdict=verdict,
            verdict_reason=reason,
            evidence=_evidence(comparisons, gauntlet, assessment.results, findings, claim, verdict),
            started_at=started_at,
            duration_s=time.monotonic() - start,
            judge_provenance=dict(assessment.provenance),
        )
        self._ledger.append_cycle(record)
        return record

    def judge_only(
        self, kind: CycleKind, *, author_id: str, version: str, cycle_index: int = 0
    ) -> tuple[GauntletResult, JudgeAssessment]:
        """Évaluation du juge + debugger SANS mesure — utilisée pour la
        baseline (« quels défauts le juge trouve-t-il AVANT toute
        modification ? »)."""
        assessment = self._judge.assess(
            kind,
            author_id=author_id,
            capability_id=self.spec.capability_id,
            cycle_index=cycle_index,
            version=version,
            cycle_id=f"{self.spec.capability_id}:baseline:{kind.value}",
        )
        return self._aggregate(assessment, cycle_index=cycle_index, version=version), assessment

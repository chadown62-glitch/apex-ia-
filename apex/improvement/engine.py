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
enregistrée et marquée `claim_contradicted` si le verdict la contredit."""

from __future__ import annotations

import time
import uuid
from typing import Protocol

from apex.improvement.debugger import investigate
from apex.improvement.judge import ExternalJudge, JudgedProbe, SelfConfirmationError
from apex.improvement.ledger import ImprovementLedger
from apex.improvement.measure import compare_snapshots
from apex.improvement.model import (
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
from apex.metalearner.judge_model import GauntletResult, JudgeDecision
from apex.stattest.comparator import StatTest


class CapabilityAdapter(Protocol):
    spec: CapabilitySpec

    def measure(self, version: str) -> BenchmarkSnapshot: ...


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
    judged: tuple[JudgedProbe, ...],
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
    for j in judged:
        evidence.append(
            Evidence(
                claim=f"sonde {j.probe.probe_id} : {j.result.decision.value}",
                level=EvidenceLevel.TESTED
                if j.result.decision is not JudgeDecision.INCONCLUSIVE
                else EvidenceLevel.UNVERIFIED,
                source=f"probe:{j.probe.probe_id}",
                detail=j.result.rationale,
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
        judge: ExternalJudge,
        ledger: ImprovementLedger,
        *,
        stat_test: StatTest | None = None,
    ) -> None:
        self._adapter = adapter
        self._judge = judge
        self._ledger = ledger
        self._stat_test = stat_test or StatTest()

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
        snapshot = BenchmarkSnapshot(
            capability_id=first.capability_id,
            version=version,
            samples=first.samples,
            blocked=first.blocked,
            reproducible=not mismatches,
            notes=tuple(notes),
        )
        if record:
            self._ledger.append_snapshot(snapshot)
        return snapshot

    def run_cycle(
        self,
        *,
        kind: CycleKind,
        cycle_index: int,
        before: BenchmarkSnapshot,
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
        gauntlet, judged = self._judge.evaluate(
            kind,
            author_id=claim.author_id,
            capability_id=self.spec.capability_id,
            cycle_index=cycle_index,
            version=version_after,
        )
        findings = investigate(judged, cycle_id=cycle_id)
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
            evidence=_evidence(comparisons, gauntlet, judged, findings, claim, verdict),
            started_at=started_at,
            duration_s=time.monotonic() - start,
        )
        self._ledger.append_cycle(record)
        return record

    def judge_only(
        self, kind: CycleKind, *, author_id: str, version: str, cycle_index: int = 0
    ) -> tuple[GauntletResult, tuple[JudgedProbe, ...], tuple[Finding, ...]]:
        """Évaluation du juge + debugger SANS mesure — utilisée pour la
        baseline (« quels défauts le juge trouve-t-il AVANT toute
        modification ? »)."""
        gauntlet, judged = self._judge.evaluate(
            kind,
            author_id=author_id,
            capability_id=self.spec.capability_id,
            cycle_index=cycle_index,
            version=version,
        )
        findings = investigate(judged, cycle_id=f"{self.spec.capability_id}:baseline:{kind.value}")
        return gauntlet, judged, findings

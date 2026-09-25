"""Juge externe du Continuous Improvement Engine.

Un juge = un ensemble de SONDES DE RÉFUTATION (`RefutationProbe`) : chacune
tente de démontrer qu'une affirmation de réussite est FAUSSE (faux positif,
chemin mort, erreur masquée, contournement du jugement, ...). Aucune sonde ne
demande « les tests passent-ils ? » — une sonde qui n'arrive pas à réfuter
passe, une sonde qui réfute échoue avec une gravité.

**Aucun second système de jugement** : chaque résultat de sonde devient un
`apex.metalearner.judge_model.JudgeResult` et l'ensemble est agrégé par
`apex.metalearner.gauntlet.GauntletRunner.run_stage` — la même agrégation
stricte que l'évolution (un finding CRITICAL = véto, un INCONCLUSIVE n'est
jamais un PASS, décision indépendante de l'ordre des sondes).

**Isolation des pannes** : une sonde qui lève devient un `JudgeResult`
INCONCLUSIVE portant l'erreur (même règle que `gauntlet._judge_error_result`)
— jamais un PASS silencieux, jamais une exception qui interrompt le cycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from apex.improvement.model import CycleKind
from apex.metalearner.gauntlet import GauntletRunner
from apex.metalearner.judge_model import (
    GauntletResult,
    GauntletStage,
    JudgeDecision,
    JudgeDimension,
    JudgeEvidence,
    JudgeFinding,
    JudgeResult,
    JudgeSeverity,
)

_PROBE_VERSION = "1"


@dataclass(frozen=True)
class ProbeOutcome:
    """`refuted=True` : la sonde a démontré un défaut. `refuted=False` : elle
    a essayé et n'a pas réussi à réfuter. `refuted=None` : elle n'a pas pu
    conclure (dépendance absente, précondition non remplie) — jamais
    confondu avec un succès."""

    refuted: bool | None
    observed: str
    hypothesis: str = ""
    severity: JudgeSeverity = JudgeSeverity.HIGH
    evidence: tuple[JudgeEvidence, ...] = ()


class RefutationProbe(Protocol):
    """Attributs en LECTURE SEULE — une sonde ne change jamais d'identité
    entre deux évaluations (et une dataclass gelée doit satisfaire ce
    contrat). `remediation` : correctif PROPOSÉ, jamais appliqué par le
    moteur ; `regression_test` : test à ajouter/exécuter une fois corrigé."""

    @property
    def probe_id(self) -> str: ...
    @property
    def dimension(self) -> JudgeDimension: ...
    @property
    def kinds(self) -> frozenset[CycleKind]: ...
    @property
    def description(self) -> str: ...
    @property
    def remediation(self) -> str: ...
    @property
    def regression_test(self) -> str: ...

    def run(self) -> ProbeOutcome: ...


@dataclass(frozen=True)
class FunctionProbe:
    """Implémentation concrète minimale de `RefutationProbe` autour d'une
    fonction — suffisant pour toute sonde sans état."""

    probe_id: str
    dimension: JudgeDimension
    kinds: frozenset[CycleKind]
    description: str
    remediation: str
    regression_test: str
    fn: Callable[[], ProbeOutcome] = field(repr=False, compare=False)

    def run(self) -> ProbeOutcome:
        return self.fn()


class SelfConfirmationError(ValueError):
    """L'auteur d'une modification tente d'en être aussi le juge."""


@dataclass(frozen=True)
class JudgedProbe:
    probe: RefutationProbe
    outcome: ProbeOutcome | None
    result: JudgeResult
    error: str | None = None


def _to_judge_result(probe: RefutationProbe, outcome: ProbeOutcome) -> JudgeResult:
    if outcome.refuted is None:
        return JudgeResult(
            judge_id=probe.probe_id,
            judge_version=_PROBE_VERSION,
            dimension=probe.dimension,
            decision=JudgeDecision.INCONCLUSIVE,
            score=None,
            confidence=0.0,
            rationale=f"sonde non concluante : {outcome.observed}",
            evidence=outcome.evidence,
        )
    if outcome.refuted:
        return JudgeResult(
            judge_id=probe.probe_id,
            judge_version=_PROBE_VERSION,
            dimension=probe.dimension,
            decision=JudgeDecision.REJECT,
            score=0.0,
            confidence=1.0,
            rationale=f"réfuté : {outcome.observed}",
            findings=(
                JudgeFinding(
                    dimension=probe.dimension,
                    severity=outcome.severity,
                    summary=probe.description,
                    detail=outcome.observed,
                ),
            ),
            evidence=outcome.evidence,
        )
    return JudgeResult(
        judge_id=probe.probe_id,
        judge_version=_PROBE_VERSION,
        dimension=probe.dimension,
        decision=JudgeDecision.PASS,
        score=1.0,
        confidence=1.0,
        rationale=f"non réfuté : {outcome.observed}",
        evidence=outcome.evidence,
    )


def _error_result(probe: RefutationProbe, exc: Exception) -> JudgeResult:
    detail = f"{exc.__class__.__name__}: {exc}"
    return JudgeResult(
        judge_id=probe.probe_id,
        judge_version=_PROBE_VERSION,
        dimension=probe.dimension,
        decision=JudgeDecision.INCONCLUSIVE,
        score=None,
        confidence=0.0,
        rationale=f"judge_error: {detail}",
        findings=(
            JudgeFinding(
                dimension=probe.dimension,
                severity=JudgeSeverity.HIGH,
                summary="la sonde a levé une exception pendant l'évaluation",
                detail=detail,
            ),
        ),
        evidence=(JudgeEvidence(kind="judge_error", reference=probe.probe_id, detail=detail),),
    )


class ExternalJudge:
    def __init__(
        self, judge_id: str, probes: tuple[RefutationProbe, ...], *, min_confidence: float = 0.6
    ) -> None:
        if not probes:
            raise ValueError("un juge sans sonde ne peut rien réfuter")
        ids = [p.probe_id for p in probes]
        if len(set(ids)) != len(ids):
            raise ValueError(f"identifiants de sonde dupliqués : {ids}")
        self.judge_id = judge_id
        self._probes = probes
        self._runner = GauntletRunner(min_confidence_threshold=min_confidence)

    def probes_for(self, kind: CycleKind) -> tuple[RefutationProbe, ...]:
        return tuple(p for p in self._probes if kind in p.kinds)

    def evaluate(
        self,
        kind: CycleKind,
        *,
        author_id: str,
        capability_id: str,
        cycle_index: int,
        version: str,
    ) -> tuple[GauntletResult, tuple[JudgedProbe, ...]]:
        if author_id == self.judge_id:
            raise SelfConfirmationError(
                f"« {author_id} » ne peut pas juger sa propre modification — séparation implémenteur/juge"
            )
        probes = self.probes_for(kind)
        if not probes:
            raise ValueError(f"aucune sonde déclarée pour le cycle « {kind.value} »")
        judged: list[JudgedProbe] = []
        for probe in probes:
            try:
                outcome = probe.run()
            except Exception as exc:  # isolation stricte — jamais un PASS par défaut
                judged.append(
                    JudgedProbe(probe=probe, outcome=None, result=_error_result(probe, exc), error=str(exc))
                )
                continue
            judged.append(
                JudgedProbe(probe=probe, outcome=outcome, result=_to_judge_result(probe, outcome))
            )
        gauntlet = self._runner.run_stage(
            tuple(j.result for j in judged),
            stage=GauntletStage.BENCHMARK,
            specialty=capability_id,
            generation=cycle_index,
            member_id=version,
        )
        return gauntlet, tuple(judged)

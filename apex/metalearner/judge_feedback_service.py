"""`JudgeFeedbackService` — transforme un `GauntletResult` (N+8, déjà réel)
en `JudgeLearningSignal`s persistés (passe « Judge Feedback -> Weakness ->
Curriculum -> Adaptive Evolution », N+9).

Ce module ne réimplémente AUCUN jugement — il ne fait que CLASSIFIER un
`JudgeResult` déjà produit, en DEUX axes indépendants et déterministes :

    `SignalCategory`  — quoi faire de ce signal (§2/§3 de la mission)
    `FailureCategory` — pourquoi (§11 : distinction candidate/judge/
                        infrastructure/policy)

**Preuve RÉELLE, jamais devinée** : la classification consulte le
`EvalRun` RÉFÉRENCÉ par la preuve citable d'un `JudgeResult`
(`JudgeEvidence(kind="eval_run"/"holdout_run"/"repeated_execution", ...)`)
— jamais une supposition sur ce qui a pu se passer. Un juge qui a levé une
exception (§21 Case I, N+8) est structurellement marqué
(`JudgeEvidence(kind="judge_error", ...)`, ajouté par ce module à
`apex.metalearner.gauntlet._judge_error_result` — additif, aucun test N+8
existant n'inspecte `evidence` sur ce chemin) : ceci devient TOUJOURS
`JUDGE_FAILURE`, jamais une faiblesse du candidat (§11 : « Judge crash ≠
candidate mauvais »)."""

from __future__ import annotations

from typing import Any

from apex.metalearner.judge_feedback_model import (
    FailureCategory,
    JudgeLearningSignal,
    SignalCategory,
)
from apex.metalearner.judge_model import GauntletResult, JudgeDecision, JudgeResult, JudgeSeverity
from apex.oracles.model import OracleVerdict


def _worst_severity(judge_result: JudgeResult) -> JudgeSeverity | None:
    if not judge_result.findings:
        return None
    order = {
        JudgeSeverity.LOW: 0, JudgeSeverity.MEDIUM: 1, JudgeSeverity.HIGH: 2, JudgeSeverity.CRITICAL: 3,
    }
    return max(judge_result.findings, key=lambda f: order[f.severity]).severity


def _referenced_eval_run_id(judge_result: JudgeResult) -> str | None:
    for evidence in judge_result.evidence:
        if evidence.kind in ("eval_run", "holdout_run", "repeated_execution"):
            return evidence.reference
    return None


def _is_judge_error(judge_result: JudgeResult) -> bool:
    return any(e.kind == "judge_error" for e in judge_result.evidence)


def classify(
    judge_result: JudgeResult, eval_run: Any | None,
) -> tuple[SignalCategory, FailureCategory | None]:
    """Fonction PURE, déterministe — `eval_run` est l'`EvalRun` réel
    référencé par `judge_result.evidence` (`None` si aucune référence, ou
    si la ré-exécution/HELDOUT n'a jamais eu lieu — voir `apex.metalearner.
    judges`, stage `CHEAP` vs `FULL`)."""
    if _is_judge_error(judge_result):
        # §11 : une panne du JUGE lui-même n'est jamais une faiblesse du
        # candidat — quel que soit `decision` (toujours `INCONCLUSIVE`
        # pour ce chemin, voir `apex.metalearner.gauntlet._judge_error_result`).
        return SignalCategory.INCONCLUSIVE, FailureCategory.JUDGE_FAILURE

    if judge_result.decision is JudgeDecision.PASS:
        return SignalCategory.SUCCESS, None

    if judge_result.decision is JudgeDecision.INCONCLUSIVE:
        # Preuve insuffisante — JAMAIS un signal négatif artificiel (§3).
        if eval_run is not None and eval_run.oracle_verdict is OracleVerdict.ERROR:
            # `OracleVerdict.ERROR` est, par définition (apex.oracles.model),
            # une panne d'exécution INDÉPENDANTE de la correction du candidat.
            return SignalCategory.INCONCLUSIVE, FailureCategory.INFRASTRUCTURE_FAILURE
        return SignalCategory.INCONCLUSIVE, FailureCategory.INSUFFICIENT_EVIDENCE

    # REJECT : la preuve pointe vers le CANDIDAT (jamais une panne d'évaluation
    # — une panne aurait déjà produit INCONCLUSIVE côté juge, voir
    # apex.metalearner.judges, chaque juge réel n'utilise REJECT que sur une
    # preuve d'exécution RÉELLEMENT aboutie et imputable au candidat).
    if judge_result.veto:
        return SignalCategory.CRITICAL_FAILURE, FailureCategory.GENUINE_CAPABILITY_WEAKNESS
    return SignalCategory.WEAKNESS, FailureCategory.GENUINE_CAPABILITY_WEAKNESS


class JudgeFeedbackService:
    def __init__(self, session_factory: Any) -> None:
        from apex.metalearner.judge_feedback_repository import JudgeLearningSignalRepository

        self._session_factory = session_factory
        self._repo = JudgeLearningSignalRepository(session_factory)

    def record_signals(
        self,
        gauntlet_result: GauntletResult,
        *,
        genome_version_id: str,
        judge_results: tuple[JudgeResult, ...] | None = None,
    ) -> tuple[JudgeLearningSignal, ...]:
        """Persiste UN signal par `JudgeResult` de `judge_results` (par
        défaut, TOUS ceux de `gauntlet_result` — un appelant qui a déjà
        persisté le stage CHEAP passe explicitement le SOUS-ENSEMBLE
        nouvellement ajouté au stage FULL, jamais les mêmes `JudgeResult`
        deux fois : voir `apex.metalearner.evolution._run_full_gauntlet`,
        sans quoi un jugement REPRIS d'un stage à l'autre serait compté
        deux fois dans l'agrégation de faiblesse, faussant §4)."""
        from apex.evalrunner.repository import EvalRunRepository

        targets = judge_results if judge_results is not None else gauntlet_result.judge_results
        eval_run_repo = EvalRunRepository(self._session_factory)
        signals = []
        for judge_result in targets:
            eval_run_id = _referenced_eval_run_id(judge_result)
            eval_run = eval_run_repo.get(eval_run_id) if eval_run_id else None
            category, failure_category = classify(judge_result, eval_run)
            signal = JudgeLearningSignal.new(
                gauntlet_result_id=gauntlet_result.id, member_id=gauntlet_result.member_id,
                specialty=gauntlet_result.specialty, generation=gauntlet_result.generation,
                genome_version_id=genome_version_id, judge_id=judge_result.judge_id,
                judge_version=judge_result.judge_version, dimension=judge_result.dimension,
                decision=judge_result.decision, category=category, failure_category=failure_category,
                severity=_worst_severity(judge_result), score=judge_result.score,
                confidence=judge_result.confidence,
                finding_summary=(
                    judge_result.findings[0].summary if judge_result.findings else judge_result.rationale
                ),
                evidence_reference=eval_run_id, protocol_version=gauntlet_result.protocol_version,
            )
            self._repo.create(signal)
            signals.append(signal)
        return tuple(signals)

    def get(self, signal_id: str) -> JudgeLearningSignal | None:
        return self._repo.get(signal_id)

    def list_by_specialty(
        self, specialty: str, *, dimension: str | None = None, limit: int = 500,
    ) -> tuple[JudgeLearningSignal, ...]:
        return tuple(self._repo.list_by_specialty(specialty, dimension=dimension, limit=limit))

    def list_by_member(self, member_id: str, *, limit: int = 200) -> tuple[JudgeLearningSignal, ...]:
        return tuple(self._repo.list_by_member(member_id, limit=limit))

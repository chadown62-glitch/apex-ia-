"""`GauntletRunner` — agrégation stricte, déterministe, multi-juges (passe
« Strict Judge System + Gauntlet Learning Loop », N+8).

    candidate -> juges CHEAP (correctness/efficiency/security/regression)
              -> [si aucun véto] juges EXPENSIVE (robustness/generalization)
              -> agrégation déterministe -> PASS / REJECT / INCONCLUSIVE

**Principe central** (§2 de la mission) : « A solution does not pass
because it looks good. A solution passes because it survives hostile
evaluation. » — l'agrégation n'est JAMAIS une moyenne : un véto (finding
CRITICAL) décide seul, quel que soit le score des autres dimensions (§8).

**Isolation des pannes de juge (§21 Case I)** : chaque `Judge.evaluate()`
est appelé dans son propre `try/except` — un juge qui lève ne devient
JAMAIS silencieusement un `PASS` ; il devient un `JudgeResult`
`INCONCLUSIVE` synthétique portant l'erreur, qui participe normalement à
l'agrégation (donc bloque au moins un `PASS` global, potentiellement force
`INCONCLUSIVE`).

**Déterminisme (§11/§21 Case G/H)** : `_aggregate` est une fonction PURE de
`tuple[JudgeResult, ...]` — mêmes résultats en entrée, même décision en
sortie, quel que soit l'ORDRE des juges (implémenté via `any()`/agrégats
commutatifs, jamais un `if premier juge alors ...`)."""

from __future__ import annotations

from apex.metalearner.judge_model import (
    GauntletResult,
    GauntletStage,
    JudgeDecision,
    JudgeDimension,
    JudgeResult,
    JudgeSeverity,
)
from apex.metalearner.judges import CHEAP_JUDGES, EXPENSIVE_JUDGES, Judge, JudgeInput

_DEFAULT_MIN_CONFIDENCE = 0.6


def _judge_error_result(judge: Judge, exc: Exception) -> JudgeResult:
    """Case I (§21/§23) : un juge qui plante ne devient JAMAIS un PASS.
    L'évidence `kind="judge_error"` (passe N+9, additif) rend ce chemin
    STRUCTURELLEMENT détectable par `apex.metalearner.judge_feedback_
    service.classify` — jamais confondu avec une preuve du candidat lui-
    même (§11 de la mission N+9 : « Judge crash ≠ candidate mauvais »)."""
    from apex.metalearner.judge_model import JudgeEvidence, JudgeFinding

    return JudgeResult(
        judge_id=judge.judge_id, judge_version=judge.judge_version, dimension=judge.dimension,
        decision=JudgeDecision.INCONCLUSIVE, score=None, confidence=0.0,
        rationale=f"judge_error: {exc.__class__.__name__}: {exc}",
        findings=(
            JudgeFinding(
                dimension=judge.dimension, severity=JudgeSeverity.HIGH,
                summary="le juge a levé une exception pendant l'évaluation",
                detail=f"{exc.__class__.__name__}: {exc}",
            ),
        ),
        evidence=(
            JudgeEvidence(
                kind="judge_error", reference=judge.judge_id, detail=f"{exc.__class__.__name__}: {exc}",
            ),
        ),
    )


def _run_judges(judges: tuple[Judge, ...], judge_input: JudgeInput) -> tuple[JudgeResult, ...]:
    results = []
    for judge in judges:
        try:
            results.append(judge.evaluate(judge_input))
        except Exception as exc:  # isolation stricte — jamais un PASS par défaut
            results.append(_judge_error_result(judge, exc))
    return tuple(results)


def _aggregate(
    judge_results: tuple[JudgeResult, ...], *, min_confidence_threshold: float,
) -> tuple[JudgeDecision, float | None, float | None, bool, JudgeDimension | None, bool]:
    """Fonction PURE — retourne (decision, overall_score, overall_confidence,
    veto, veto_dimension, disagreement). Ordre des `judge_results` NE
    change JAMAIS le résultat (§21 Case G)."""
    # 1. Véto — un finding CRITICAL décide seul (§8), peu importe le reste.
    vetoes = [r for r in judge_results if r.veto]
    if vetoes:
        # Déterministe même à plusieurs vétoes : la dimension du PREMIER
        # dans l'ordre STABLE de `JudgeDimension` (jamais l'ordre d'appel).
        veto_dimension = min((r.dimension for r in vetoes), key=lambda d: d.value)
        decision_scores = {r.decision for r in judge_results}
        disagreement = JudgeDecision.PASS in decision_scores and len(decision_scores) > 1
        return JudgeDecision.REJECT, 0.0, 1.0, True, veto_dimension, disagreement

    decisions = {r.decision for r in judge_results}
    disagreement = len(decisions) > 1

    # 2. Un REJECT non-CRITICAL bloque déjà la promotion — jamais masqué
    #    par une moyenne meilleure ailleurs (§8/§12).
    if JudgeDecision.REJECT in decisions:
        scored = [r.score for r in judge_results if r.score is not None]
        overall_score = sum(scored) / len(scored) if scored else None
        confidences = [r.confidence for r in judge_results if r.confidence is not None]
        overall_confidence = min(confidences) if confidences else None
        return JudgeDecision.REJECT, overall_score, overall_confidence, False, None, disagreement

    # 3. Un INCONCLUSIVE (preuve manquante/juge en panne) n'est JAMAIS
    #    silencieusement traité comme un PASS (§9/§10/§23).
    if JudgeDecision.INCONCLUSIVE in decisions:
        scored = [r.score for r in judge_results if r.score is not None]
        overall_score = sum(scored) / len(scored) if scored else None
        confidences = [r.confidence for r in judge_results if r.confidence is not None]
        overall_confidence = min(confidences) if confidences else 0.0
        return JudgeDecision.INCONCLUSIVE, overall_score, overall_confidence, False, None, disagreement

    # 4. Tous PASS — mais un score élevé à faible confiance n'est jamais
    #    une preuve forte (§10, Case B) : sous le seuil, INCONCLUSIVE.
    scored = [r.score for r in judge_results if r.score is not None]
    overall_score = sum(scored) / len(scored) if scored else None
    confidences = [r.confidence for r in judge_results if r.confidence is not None]
    overall_confidence = min(confidences) if confidences else None
    if overall_confidence is not None and overall_confidence < min_confidence_threshold:
        return JudgeDecision.INCONCLUSIVE, overall_score, overall_confidence, False, None, disagreement
    return JudgeDecision.PASS, overall_score, overall_confidence, False, None, disagreement


class GauntletRunner:
    def __init__(self, *, min_confidence_threshold: float = _DEFAULT_MIN_CONFIDENCE) -> None:
        self._min_confidence_threshold = min_confidence_threshold

    def run_cheap(
        self, judge_input: JudgeInput, *, specialty: str, generation: int, member_id: str,
    ) -> GauntletResult:
        """Calculée pour CHAQUE membre — aucune exécution supplémentaire
        (§13/§24 : le gauntlet peut s'arrêter tôt, ici il n'y a même pas de
        second stage à arrêter puisque les juges EXPENSIVE ne sont
        JAMAIS déclenchés par ce chemin)."""
        judge_results = _run_judges(CHEAP_JUDGES, judge_input)
        decision, score, confidence, veto, veto_dim, disagreement = _aggregate(
            judge_results, min_confidence_threshold=self._min_confidence_threshold,
        )
        return GauntletResult.new(
            specialty=specialty, generation=generation, member_id=member_id, stage=GauntletStage.CHEAP,
            decision=decision, overall_score=score, overall_confidence=confidence, veto=veto,
            veto_dimension=veto_dim, disagreement=disagreement, stopped_after_critical_failure=False,
            judge_results=judge_results,
        )

    def run_full(
        self,
        judge_input: JudgeInput,
        *,
        specialty: str,
        generation: int,
        member_id: str,
        cheap_result: GauntletResult,
        compared_champion_id: str | None,
        run_expensive: bool = True,
    ) -> GauntletResult:
        """Réutilise les résultats CHEAP déjà calculés pour ce membre (§28 :
        jamais une seconde exécution des juges déjà réels) et y ajoute les
        juges EXPENSIVE — SAUF si le stage CHEAP a déjà produit un véto
        (§13/§24 : arrêter tôt après un échec critique économise les
        ressources réelles — repeated-execution/HELDOUT ne sont jamais
        dépensés pour un candidat déjà rejeté) OU si `run_expensive=False`
        (`GauntletPolicy.enable_expensive_judges`, désactivé par un
        administrateur — dans ce cas les dimensions EXPENSIVE sont
        honnêtement ABSENTES du résultat, jamais un `PASS`/`INCONCLUSIVE`
        fabriqué pour combler leur absence : même discipline que
        `RobustnessJudge`/`GeneralizationJudge` face à une preuve
        manquante, §23)."""
        if cheap_result.veto or not run_expensive:
            return GauntletResult.new(
                specialty=specialty, generation=generation, member_id=member_id, stage=GauntletStage.FULL,
                decision=cheap_result.decision, overall_score=cheap_result.overall_score,
                overall_confidence=cheap_result.overall_confidence, veto=cheap_result.veto,
                veto_dimension=cheap_result.veto_dimension, disagreement=cheap_result.disagreement,
                stopped_after_critical_failure=cheap_result.veto, judge_results=cheap_result.judge_results,
                compared_champion_id=compared_champion_id,
            )
        expensive_results = _run_judges(EXPENSIVE_JUDGES, judge_input)
        combined = cheap_result.judge_results + expensive_results
        decision, score, confidence, veto, veto_dim, disagreement = _aggregate(
            combined, min_confidence_threshold=self._min_confidence_threshold,
        )
        return GauntletResult.new(
            specialty=specialty, generation=generation, member_id=member_id, stage=GauntletStage.FULL,
            decision=decision, overall_score=score, overall_confidence=confidence, veto=veto,
            veto_dimension=veto_dim, disagreement=disagreement, stopped_after_critical_failure=False,
            judge_results=combined, compared_champion_id=compared_champion_id,
        )

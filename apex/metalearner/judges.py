"""Juges spécialisés — passe « Strict Judge System + Gauntlet Learning
Loop » (N+8). Chaque juge a UNE responsabilité (§5 de la mission : « ne
crée pas un énorme God Judge ») et ne réimplémente AUCUNE mesure déjà
réelle — chacun ENVELOPPE une preuve déjà produite ailleurs
(`OracleVerdict`, `ExecutionStatus`, `elapsed_s`, `fitness_components`
persistés d'un champion) dans un `JudgeResult` structuré.

**Priorité absolue objective > LLM** (§6) : aucun juge de ce module ne
consulte un modèle de langage — chacun est une fonction PURE/déterministe
d'un `JudgeInput` déjà réel. Un futur `LLMJudge` (§26, architecture
évolutive) s'ajouterait ICI sans toucher `apex.metalearner.gauntlet`
(l'agrégateur ne connaît que `JudgeResult`, jamais l'implémentation d'un
juge particulier).

**Judge Diversity / anti-reward-hacking (§16)** : `RobustnessJudge` et
`GeneralizationJudge` exigent chacun une preuve d'exécution RÉELLE
supplémentaire (`repeated_eval_run`/`holdout_eval_run` sur `JudgeInput`) —
`None` produit honnêtement `INCONCLUSIVE`, JAMAIS un `PASS` déduit par
défaut (§23 : « ne transforme pas silencieusement cela en PASS »)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from apex.evalrunner.model import EvalRun
from apex.metalearner.judge_model import (
    JudgeDecision,
    JudgeDimension,
    JudgeEvidence,
    JudgeFinding,
    JudgeResult,
    JudgeSeverity,
)
from apex.oracles.model import OracleVerdict
from apex.sandbox.model import ExecutionStatus, ResourceLimits

_JUDGE_VERSION = "1"

#: `ExecutionStatus` qui traduisent une tentative RÉELLE de contournement du
#: sandbox — jamais une simple pénalité d'efficacité (voir `_RESOURCE_
#: STATUSES` ci-dessous, dimension distincte).
_SECURITY_STATUSES = frozenset({ExecutionStatus.NETWORK_DENIED, ExecutionStatus.PERMISSION_DENIED})
#: `ExecutionStatus` qui traduisent un dépassement de RESSOURCE (temps,
#: mémoire, disque, process) — une question d'efficacité, jamais de
#: sécurité (séparation stricte des responsabilités, §5).
_RESOURCE_STATUSES = frozenset(
    {
        ExecutionStatus.OOM_KILLED, ExecutionStatus.RESOURCE_LIMIT_EXCEEDED,
        ExecutionStatus.PROCESS_LIMIT_EXCEEDED, ExecutionStatus.DISK_LIMIT_EXCEEDED,
    }
)
_INFRA_VERDICTS = frozenset({OracleVerdict.ERROR, OracleVerdict.TIMEOUT, OracleVerdict.UNKNOWN})


@dataclass(frozen=True)
class JudgeInput:
    """Toutes les preuves disponibles pour juger UN candidat, à UN instant
    donné. Les champs optionnels (`repeated_eval_run`/`holdout_eval_run`/
    `champion_fitness_components`) reflètent honnêtement ce qui a
    RÉELLEMENT été exécuté — jamais rempli par une valeur devinée quand
    l'exécution correspondante n'a pas eu lieu (voir `GauntletStage.CHEAP`
    vs `FULL`, `apex.metalearner.gauntlet`)."""

    eval_run: EvalRun
    limits: ResourceLimits
    candidate_fitness_components: dict[str, Any]
    repeated_eval_run: EvalRun | None = None
    holdout_eval_run: EvalRun | None = None
    champion_member_id: str | None = None
    champion_fitness_components: dict[str, Any] | None = None
    efficiency_reject_threshold: float = 0.9
    major_regression_factor: float = 2.0


class Judge(Protocol):
    dimension: JudgeDimension
    judge_id: str
    judge_version: str

    def evaluate(self, judge_input: JudgeInput) -> JudgeResult: ...


class CorrectnessJudge:
    """Enveloppe `OracleVerdict` (`apex.oracles.model`) — AUCUNE seconde
    logique de correction. Objectif, déterministe : ne consulte jamais un
    LLM (§6)."""

    dimension = JudgeDimension.CORRECTNESS
    judge_id = "correctness"
    judge_version = _JUDGE_VERSION

    def evaluate(self, judge_input: JudgeInput) -> JudgeResult:
        run = judge_input.eval_run
        evidence = (JudgeEvidence(kind="eval_run", reference=run.id, detail=f"oracle_verdict={run.oracle_verdict.value}"),)
        if run.oracle_verdict is OracleVerdict.PASS:
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.PASS, score=1.0, confidence=1.0,
                rationale="oracle_verdict=PASS — tâche réellement résolue.", evidence=evidence,
            )
        if run.oracle_verdict is OracleVerdict.FAIL:
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.REJECT, score=0.0, confidence=1.0,
                rationale="oracle_verdict=FAIL — tâche tentée, jugée incorrecte.", evidence=evidence,
                findings=(
                    JudgeFinding(
                        dimension=self.dimension, severity=JudgeSeverity.HIGH,
                        summary="la tâche n'a pas été résolue", detail=run.oracle_detail or "",
                    ),
                ),
            )
        # ERROR/TIMEOUT/UNKNOWN : l'exécution elle-même n'a pas produit de
        # verdict de correction exploitable — jamais un FAIL/PASS deviné
        # (même discipline qu'`OracleVerdict.UNKNOWN`, apex.oracles.model).
        return JudgeResult(
            judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
            decision=JudgeDecision.INCONCLUSIVE, score=None, confidence=0.0,
            rationale=f"oracle_verdict={run.oracle_verdict.value} — correction non évaluable.",
            evidence=evidence,
            findings=(
                JudgeFinding(
                    dimension=self.dimension, severity=JudgeSeverity.MEDIUM,
                    summary="l'exécution n'a pas produit de verdict de correction exploitable",
                    detail=run.oracle_detail or "",
                ),
            ),
        )


class EfficiencyJudge:
    """`cost_penalty` = `elapsed_s` / `timeout_s` RÉELLEMENT appliqué — même
    formule qu'`apex.metalearner.population_fitness.compute_fitness`,
    jamais un second calcul divergent. Un dépassement RESSOURCE
    (`_RESOURCE_STATUSES`) est aussi un échec d'efficacité — jamais une
    question de sécurité (voir `SecurityJudge`)."""

    dimension = JudgeDimension.EFFICIENCY
    judge_id = "efficiency"
    judge_version = _JUDGE_VERSION

    def evaluate(self, judge_input: JudgeInput) -> JudgeResult:
        run = judge_input.eval_run
        evidence = (
            JudgeEvidence(
                kind="eval_run", reference=run.id,
                detail=f"elapsed_s={run.elapsed_s}, sandbox_status={run.sandbox_status.value if run.sandbox_status else None}",
            ),
        )
        if run.sandbox_status in _RESOURCE_STATUSES:
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.REJECT, score=0.0, confidence=1.0,
                rationale=f"sandbox_status={run.sandbox_status.value} — budget ressource dépassé.",
                evidence=evidence,
                findings=(
                    JudgeFinding(
                        dimension=self.dimension, severity=JudgeSeverity.HIGH,
                        summary="budget ressource dépassé pendant l'exécution",
                        detail=run.sandbox_status.value,
                    ),
                ),
            )
        if run.elapsed_s is None or judge_input.limits.timeout_s <= 0:
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.INCONCLUSIVE, score=None, confidence=0.0,
                rationale="durée non mesurée — efficacité non évaluable.", evidence=evidence,
                findings=(
                    JudgeFinding(
                        dimension=self.dimension, severity=JudgeSeverity.LOW,
                        summary="durée d'exécution non mesurée",
                    ),
                ),
            )
        cost_penalty = min(1.0, max(0.0, run.elapsed_s / judge_input.limits.timeout_s))
        if cost_penalty > judge_input.efficiency_reject_threshold:
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.REJECT, score=1.0 - cost_penalty, confidence=1.0,
                rationale=f"cost_penalty={cost_penalty:.3f} au-delà du seuil {judge_input.efficiency_reject_threshold}.",
                evidence=evidence,
                findings=(
                    JudgeFinding(
                        dimension=self.dimension, severity=JudgeSeverity.HIGH,
                        summary="quasi-totalité du budget temps consommée",
                        detail=f"cost_penalty={cost_penalty:.3f}",
                    ),
                ),
            )
        return JudgeResult(
            judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
            decision=JudgeDecision.PASS, score=1.0 - cost_penalty, confidence=1.0,
            rationale=f"cost_penalty={cost_penalty:.3f} dans le budget.", evidence=evidence,
        )


class SecurityJudge:
    """Signal RÉEL déjà produit par `apex.sandbox` (Phase 7) — une tentative
    d'accès réseau/permission refusée par le sandbox lui-même, jamais un
    scan de sécurité inventé. `NETWORK_DENIED`/`PERMISSION_DENIED` sont un
    VÉTO (CRITICAL) : un candidat qui tente de contourner l'isolation ne
    doit jamais être promu, quel que soit son score ailleurs (§8)."""

    dimension = JudgeDimension.SECURITY
    judge_id = "security"
    judge_version = _JUDGE_VERSION

    def evaluate(self, judge_input: JudgeInput) -> JudgeResult:
        run = judge_input.eval_run
        evidence = (
            JudgeEvidence(kind="eval_run", reference=run.id, detail=f"sandbox_status={run.sandbox_status.value if run.sandbox_status else None}"),
        )
        if run.sandbox_status in _SECURITY_STATUSES:
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.REJECT, score=0.0, confidence=1.0,
                rationale=f"sandbox_status={run.sandbox_status.value} — tentative de contournement du sandbox.",
                evidence=evidence,
                findings=(
                    JudgeFinding(
                        dimension=self.dimension, severity=JudgeSeverity.CRITICAL,
                        summary="tentative de contournement de l'isolation sandbox",
                        detail=run.sandbox_status.value,
                    ),
                ),
            )
        return JudgeResult(
            judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
            decision=JudgeDecision.PASS, score=1.0, confidence=1.0,
            rationale="aucune tentative de contournement détectée.", evidence=evidence,
        )


class RegressionJudge:
    """Compare le candidat au CHAMPION courant sur les composantes de
    fitness déjà PERSISTÉES (`population_members.fitness_components`,
    N+6) — aucune exécution supplémentaire, aucun second calcul de fitness
    (§28 : ne pas créer un second fitness incompatible). `None` (pas de
    champion existant, ex. génération 0) -> `PASS` trivial : rien à
    régresser contre."""

    dimension = JudgeDimension.REGRESSION
    judge_id = "regression"
    judge_version = _JUDGE_VERSION

    def evaluate(self, judge_input: JudgeInput) -> JudgeResult:
        champion_components = judge_input.champion_fitness_components
        if champion_components is None:
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.PASS, score=1.0, confidence=1.0,
                rationale="aucun champion existant — rien à régresser.",
            )
        evidence = (
            JudgeEvidence(
                kind="champion_comparison", reference=judge_input.champion_member_id or "unknown",
                detail=f"champion_fitness_components={champion_components}",
            ),
        )
        candidate = judge_input.candidate_fitness_components
        champion_quality = float(champion_components.get("quality", 0.0))
        candidate_quality = float(candidate.get("quality", 0.0))
        if candidate_quality < champion_quality:
            # Le champion résolvait la tâche, le candidat non — une
            # régression de CORRECTION est toujours un véto (§12 : jamais
            # masquée par une moyenne).
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.REJECT, score=0.0, confidence=1.0,
                rationale="régression de correction : le champion résolvait la tâche, le candidat non.",
                evidence=evidence,
                findings=(
                    JudgeFinding(
                        dimension=self.dimension, severity=JudgeSeverity.CRITICAL,
                        summary="régression de correction par rapport au champion",
                        detail=f"quality champion={champion_quality} candidat={candidate_quality}",
                    ),
                ),
            )
        champion_cost = champion_components.get("cost_penalty")
        candidate_cost = candidate.get("cost_penalty")
        if (
            champion_components.get("cost_known") and candidate.get("cost_known")
            and champion_cost is not None and candidate_cost is not None
            and champion_cost > 0.0 and candidate_cost > champion_cost * judge_input.major_regression_factor
        ):
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.REJECT, score=0.3, confidence=1.0,
                rationale=(
                    f"régression majeure de coût : cost_penalty candidat={candidate_cost:.3f} "
                    f"> {judge_input.major_regression_factor}x champion={champion_cost:.3f}."
                ),
                evidence=evidence,
                findings=(
                    JudgeFinding(
                        dimension=self.dimension, severity=JudgeSeverity.HIGH,
                        summary="régression majeure de coût par rapport au champion",
                        detail=f"candidat={candidate_cost:.3f} champion={champion_cost:.3f}",
                    ),
                ),
            )
        return JudgeResult(
            judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
            decision=JudgeDecision.PASS, score=1.0, confidence=1.0,
            rationale="aucune régression détectée par rapport au champion.", evidence=evidence,
        )


class RobustnessJudge:
    """Cohérence entre DEUX exécutions RÉELLES du même (tâche, Genome) —
    `repeated_eval_run` doit venir d'une VRAIE seconde exécution
    (`apex.metalearner.evolution` en fournit une SEULEMENT au stage `FULL`,
    jamais fabriquée). Un verdict qui CHANGE entre les deux exécutions est
    un véto (§7/§16 : un candidat non-déterministe n'est jamais un
    candidat robuste)."""

    dimension = JudgeDimension.ROBUSTNESS
    judge_id = "robustness"
    judge_version = _JUDGE_VERSION

    def evaluate(self, judge_input: JudgeInput) -> JudgeResult:
        repeat = judge_input.repeated_eval_run
        if repeat is None:
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.INCONCLUSIVE, score=None, confidence=0.0,
                rationale="ré-exécution non effectuée — robustesse non évaluée.",
                findings=(
                    JudgeFinding(
                        dimension=self.dimension, severity=JudgeSeverity.LOW,
                        summary="ré-exécution non effectuée pour ce candidat",
                    ),
                ),
            )
        original = judge_input.eval_run
        evidence = (
            JudgeEvidence(kind="eval_run", reference=original.id, detail=f"oracle_verdict={original.oracle_verdict.value}"),
            JudgeEvidence(kind="repeated_execution", reference=repeat.id, detail=f"oracle_verdict={repeat.oracle_verdict.value}"),
        )
        if original.oracle_verdict in _INFRA_VERDICTS or repeat.oracle_verdict in _INFRA_VERDICTS:
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.INCONCLUSIVE, score=None, confidence=0.2, evidence=evidence,
                rationale="une des deux exécutions a échoué au niveau infrastructure — cohérence non évaluable.",
                findings=(
                    JudgeFinding(
                        dimension=self.dimension, severity=JudgeSeverity.MEDIUM,
                        summary="une exécution a échoué au niveau infrastructure (pas au niveau code)",
                    ),
                ),
            )
        if original.oracle_verdict is not repeat.oracle_verdict:
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.REJECT, score=0.0, confidence=1.0, evidence=evidence,
                rationale=(
                    f"verdict non-reproductible : {original.oracle_verdict.value} puis "
                    f"{repeat.oracle_verdict.value} pour la même (tâche, Genome)."
                ),
                findings=(
                    JudgeFinding(
                        dimension=self.dimension, severity=JudgeSeverity.CRITICAL,
                        summary="candidat non-déterministe — verdict différent entre deux exécutions identiques",
                        detail=f"{original.oracle_verdict.value} -> {repeat.oracle_verdict.value}",
                    ),
                ),
            )
        return JudgeResult(
            judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
            decision=JudgeDecision.PASS, score=1.0, confidence=1.0, evidence=evidence,
            rationale="verdict identique sur les deux exécutions — comportement reproductible.",
        )


class GeneralizationJudge:
    """Enveloppe le contrôle HELDOUT déjà réel (N+6, §13 —
    `PopulationEvolutionService._run_holdout_check`) — AUCUNE seconde
    implémentation du contrôle lui-même, seulement son exposition comme
    jugement structuré. Un échec HELDOUT est un véto (§8/§16 : un candidat
    qui surapprend le TRAIN sans généraliser ne doit jamais devenir
    champion, même s'il bat le champion sur validation normale — Case F)."""

    dimension = JudgeDimension.GENERALIZATION
    judge_id = "generalization"
    judge_version = _JUDGE_VERSION

    def evaluate(self, judge_input: JudgeInput) -> JudgeResult:
        holdout = judge_input.holdout_eval_run
        if holdout is None:
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.INCONCLUSIVE, score=None, confidence=0.0,
                rationale="contrôle HELDOUT non effectué — généralisation non évaluée.",
                findings=(
                    JudgeFinding(
                        dimension=self.dimension, severity=JudgeSeverity.LOW,
                        summary="contrôle HELDOUT non effectué pour ce candidat",
                    ),
                ),
            )
        evidence = (JudgeEvidence(kind="holdout_run", reference=holdout.id, detail=f"oracle_verdict={holdout.oracle_verdict.value}"),)
        if holdout.oracle_verdict is OracleVerdict.PASS:
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.PASS, score=1.0, confidence=1.0, evidence=evidence,
                rationale="contrôle HELDOUT réussi — généralise au-delà du TRAIN.",
            )
        if holdout.oracle_verdict is OracleVerdict.FAIL:
            return JudgeResult(
                judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
                decision=JudgeDecision.REJECT, score=0.0, confidence=1.0, evidence=evidence,
                rationale="contrôle HELDOUT échoué — le candidat ne généralise pas au-delà du TRAIN.",
                findings=(
                    JudgeFinding(
                        dimension=self.dimension, severity=JudgeSeverity.CRITICAL,
                        summary="échec de généralisation (HELDOUT) — surapprentissage probable du TRAIN",
                        detail=holdout.oracle_detail or "",
                    ),
                ),
            )
        return JudgeResult(
            judge_id=self.judge_id, judge_version=self.judge_version, dimension=self.dimension,
            decision=JudgeDecision.INCONCLUSIVE, score=None, confidence=0.2, evidence=evidence,
            rationale=f"contrôle HELDOUT non concluant (oracle_verdict={holdout.oracle_verdict.value}).",
            findings=(
                JudgeFinding(
                    dimension=self.dimension, severity=JudgeSeverity.MEDIUM,
                    summary="contrôle HELDOUT non concluant (échec infrastructure)",
                ),
            ),
        )


#: Juges CHEAP — aucune exécution supplémentaire, calculés pour CHAQUE
#: membre (voir `apex.metalearner.gauntlet.GauntletRunner.run_cheap`).
CHEAP_JUDGES: tuple[Judge, ...] = (CorrectnessJudge(), EfficiencyJudge(), SecurityJudge(), RegressionJudge())
#: Juges EXPENSIVE — exigent chacun une exécution RÉELLE supplémentaire,
#: calculés SEULEMENT pour le candidat à la promotion champion (voir
#: `GauntletRunner.run_full`).
EXPENSIVE_JUDGES: tuple[Judge, ...] = (RobustnessJudge(), GeneralizationJudge())

"""`CapabilityBenchmarkService` — mesure de capacité INDÉPENDANTE (passe
« Adversarial Curriculum + Capability Benchmarking », N+10, §8/§9/§12/§13
de la mission).

**Séparation RÉELLE curriculum / benchmark (§12)** : la batterie de tâches
(`_ensure_benchmark_tasks`) est FIXE, `TaskSplit.HELDOUT`, taguée
`dataset_ref="capability_benchmark_v1:{specialty}"` — jamais réutilisée
par `apex.metalearner.challenge_service`/`curriculum_engine` (curriculum
d'entraînement). Ne réimplémente AUCUN juge : compose `apex.metalearner.
judges.{Correctness,Efficiency,Security,Robustness,Generalization}Judge`
(N+8, INCHANGÉS) via `GauntletRunner.run_stage` (§ voir sa docstring),
stage `BENCHMARK` — les MÊMES preuves structurées que CHEAP/FULL,
persistées dans `gauntlet_results` (aucune nouvelle table de résultats de
jugement, §1 : ne rien réimplémenter)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from apex.metalearner.capability_model import (
    CAPABILITY_BENCHMARK_PROTOCOL_VERSION,
    CAPABILITY_DIMENSIONS,
    CapabilityBenchmarkRun,
    CapabilityDelta,
    PromotionRuleResult,
)
from apex.metalearner.judge_model import GauntletStage, JudgeDecision, JudgeDimension, JudgeSeverity
from apex.policy.model import Subject
from apex.taskgen.model import AdversarialKind, EdgeCaseKind, GeneralizationVariant, GenerationMode
from apex.tasks.model import TaskSplit

if TYPE_CHECKING:
    from apex.agents.runtime import AgentRuntime
    from apex.sandbox.model import ResourceLimits

_BENCHMARK_DATASET_PREFIX = "capability_benchmark_v1"

#: Régression jugée CRITIQUE — un delta de score en-deçà de ce seuil (sur
#: une échelle [0.0, 1.0]) bloque la promotion QUELLE QUE SOIT la moyenne
#: ailleurs (§9 de la mission : « +15% correctness -40% security -> PAS une
#: amélioration acceptable »). Documenté, conservateur, jamais ajusté pour
#: produire un résultat souhaité à l'avance.
_CRITICAL_REGRESSION_THRESHOLD = -0.10


class CapabilityBenchmarkService:
    def __init__(self, session_factory: Any, *, agent_runtime: AgentRuntime) -> None:
        self._session_factory = session_factory
        self._agent_runtime = agent_runtime

    # -- batterie de tâches, fixe, HELDOUT --------------------------------------

    def _ensure_benchmark_tasks(self, specialty: str) -> dict[str, Any]:
        from apex.tasks.service import TaskBankService

        bank = TaskBankService(self._session_factory)
        existing = {
            t.dataset_ref: t for t in bank.list_by_specialty(specialty) if t.dataset_ref is not None
        }
        tasks: dict[str, Any] = {}
        for key, spec in self._benchmark_specs(specialty).items():
            ref = f"{_BENCHMARK_DATASET_PREFIX}:{specialty}:{key}"
            found = existing.get(ref)
            if found is not None:
                tasks[key] = found
                continue
            tasks[key] = bank.create(
                split=TaskSplit.HELDOUT, specialty=spec["specialty"], title=spec["title"],
                description=spec["description"], command=spec["command"], created_by=Subject.HUMAN,
                dataset_ref=ref,
            )
        return tasks

    def _benchmark_specs(self, specialty: str) -> dict[str, dict[str, Any]]:
        from apex.taskgen.provider import get_default_generator

        generator = get_default_generator()
        correctness = generator.generate(
            mode=GenerationMode.NORMAL, specialty=specialty, split=TaskSplit.HELDOUT,
        )
        robustness = generator.generate(
            mode=GenerationMode.EDGE_CASE, specialty=specialty, split=TaskSplit.HELDOUT,
            edge_case_kind=EdgeCaseKind.REPEATED_CALLS,
        )
        security = generator.generate(
            mode=GenerationMode.ADVERSARIAL, specialty=specialty, split=TaskSplit.HELDOUT,
            adversarial_kind=AdversarialKind.NETWORK_BOUNDARY_PROBE,
        )
        generalization = generator.generate(
            mode=GenerationMode.NORMAL, specialty=specialty, split=TaskSplit.HELDOUT,
            generalization_variant=GeneralizationVariant.ALTERNATE_PROBE,
        )
        return {
            "correctness": {
                "specialty": correctness.specialty, "title": correctness.title,
                "description": correctness.description, "command": correctness.command,
            },
            "robustness": {
                "specialty": robustness.specialty, "title": robustness.title,
                "description": robustness.description, "command": robustness.command,
            },
            "security": {
                "specialty": security.specialty, "title": security.title,
                "description": security.description, "command": security.command,
            },
            "generalization": {
                "specialty": generalization.specialty, "title": generalization.title,
                "description": generalization.description, "command": generalization.command,
            },
        }

    # -- exécution réelle --------------------------------------------------------

    def run_benchmark(
        self,
        *,
        specialty: str,
        member_id: str,
        generation: int,
        genome_version_id: str,
        genome_content: dict[str, Any] | None,
        limits: ResourceLimits,
        compared_champion_id: str | None,
    ) -> CapabilityBenchmarkRun | None:
        """`None` en cas de panne d'infrastructure — jamais susceptible de
        faire échouer la décision de promotion elle-même (même discipline
        qu'`apex.metalearner.evolution._run_holdout`/`_repeat_execution`) :
        l'appelant traite `None` comme une preuve insuffisante, jamais un
        `PASS` déduit (§3/§13)."""
        try:
            tasks = self._ensure_benchmark_tasks(specialty)
            correctness_run = self._execute(
                specialty, tasks["correctness"].id, genome_version_id, genome_content,
            )
            robustness_run_1 = self._execute(
                specialty, tasks["robustness"].id, genome_version_id, genome_content,
            )
            robustness_run_2 = self._execute(
                specialty, tasks["robustness"].id, genome_version_id, genome_content,
            )
            security_run = self._execute(
                specialty, tasks["security"].id, genome_version_id, genome_content,
            )
            generalization_run = self._execute(
                specialty, tasks["generalization"].id, genome_version_id, genome_content,
            )
            if any(
                run is None
                for run in (
                    correctness_run, robustness_run_1, robustness_run_2, security_run, generalization_run,
                )
            ):
                return None
        except Exception:
            return None

        from apex.metalearner.judges import (
            CorrectnessJudge,
            EfficiencyJudge,
            GeneralizationJudge,
            JudgeInput,
            RobustnessJudge,
            SecurityJudge,
        )

        judge_results = [
            CorrectnessJudge().evaluate(
                JudgeInput(eval_run=correctness_run, limits=limits, candidate_fitness_components={}),
            ),
            EfficiencyJudge().evaluate(
                JudgeInput(eval_run=correctness_run, limits=limits, candidate_fitness_components={}),
            ),
            RobustnessJudge().evaluate(
                JudgeInput(
                    eval_run=robustness_run_1, limits=limits, candidate_fitness_components={},
                    repeated_eval_run=robustness_run_2,
                ),
            ),
            SecurityJudge().evaluate(
                JudgeInput(eval_run=security_run, limits=limits, candidate_fitness_components={}),
            ),
            GeneralizationJudge().evaluate(
                JudgeInput(
                    eval_run=generalization_run, limits=limits, candidate_fitness_components={},
                    holdout_eval_run=generalization_run,
                ),
            ),
            self._regression_judge_result(specialty, compared_champion_id, correctness_run),
        ]

        from apex.metalearner.gauntlet import GauntletRunner
        from apex.metalearner.gauntlet_repository import GauntletRepository

        runner = GauntletRunner()
        result = runner.run_stage(
            tuple(judge_results), stage=GauntletStage.BENCHMARK, specialty=specialty, generation=generation,
            member_id=member_id, compared_champion_id=compared_champion_id,
        )
        GauntletRepository(self._session_factory).create(result)
        self._record_feedback(result, genome_version_id)

        return CapabilityBenchmarkRun.from_gauntlet_result(
            result, genome_version_id=genome_version_id,
            benchmark_version=CAPABILITY_BENCHMARK_PROTOCOL_VERSION,
        )

    def _execute(
        self, specialty: str, task_id: str, genome_version_id: str, genome_content: dict[str, Any] | None,
    ) -> Any:
        from apex.agents.pool_selection import pick_sandbox_capable_pool
        from apex.config.loader import load_config
        from apex.evalrunner.model import EvalMethod
        from apex.evalrunner.service import EvalRunnerService
        from apex.genome.service import GenomeStoreService

        config = load_config()
        pool = pick_sandbox_capable_pool(config)
        genome_version = GenomeStoreService(self._session_factory).get(genome_version_id)
        if genome_version is None:
            return None
        clone = self._agent_runtime.spawn(
            Subject.LEARNING, pool=pool, genome_ref=genome_version.content_hash, spawned_by=Subject.CORE,
        )
        return EvalRunnerService(self._session_factory, agent_runtime=self._agent_runtime).run(
            task_id=task_id, clone_id=clone.clone_id, method=EvalMethod.PRACTICE, requested_by=Subject.CORE,
            experiment_ref=f"capability-benchmark:{specialty}:{clone.clone_id}",
            genome_content=genome_content,
        )

    def _regression_judge_result(
        self, specialty: str, champion_member_id: str | None, correctness_run: Any,
    ) -> Any:
        """`None`/preuve de référence absente -> `PASS` TRIVIAL, MÊME
        principe qu'`apex.metalearner.judges.RegressionJudge` (§ sa
        docstring : « aucun champion existant — rien à régresser ») —
        JAMAIS `INCONCLUSIVE` : cette dimension REGRESSION comparerait
        contre une référence qui n'existe simplement pas encore (ex.
        génération 0), ce n'est PAS une preuve manquante dangereuse.
        `INCONCLUSIVE` resterait le bon verdict pour un autre juge dont la
        preuve devrait exister mais manque — pas ici."""
        from apex.metalearner.judge_model import JudgeEvidence, JudgeFinding, JudgeResult
        from apex.oracles.model import OracleVerdict

        version = "1"
        if champion_member_id is None:
            return JudgeResult(
                judge_id="capability_regression", judge_version=version,
                dimension=JudgeDimension.REGRESSION,
                decision=JudgeDecision.PASS, score=1.0, confidence=1.0,
                rationale="aucun champion courant — rien à régresser.",
            )
        champion_run = self.get_latest(specialty, member_id=champion_member_id)
        if champion_run is None:
            return JudgeResult(
                judge_id="capability_regression", judge_version=version,
                dimension=JudgeDimension.REGRESSION,
                decision=JudgeDecision.PASS, score=1.0, confidence=1.0,
                rationale="aucun benchmark de capacité antérieur pour le champion — rien à régresser.",
            )
        champion_measurement = champion_run.measurement_for(JudgeDimension.CORRECTNESS)
        candidate_score = 1.0 if correctness_run.oracle_verdict is OracleVerdict.PASS else 0.0
        if champion_measurement is None or champion_measurement.score is None:
            return JudgeResult(
                judge_id="capability_regression", judge_version=version,
                dimension=JudgeDimension.REGRESSION,
                decision=JudgeDecision.PASS, score=1.0, confidence=1.0,
                rationale="mesure de référence du champion indisponible — rien à régresser.",
            )
        delta = candidate_score - champion_measurement.score
        evidence = (JudgeEvidence(kind="eval_run", reference=correctness_run.id, detail=f"delta={delta}"),)
        if delta < _CRITICAL_REGRESSION_THRESHOLD:
            return JudgeResult(
                judge_id="capability_regression", judge_version=version,
                dimension=JudgeDimension.REGRESSION,
                decision=JudgeDecision.REJECT, score=candidate_score, confidence=1.0, evidence=evidence,
                rationale=f"régression de capacité RÉELLE vs champion (delta={delta:.2f}).",
                findings=(
                    JudgeFinding(
                        dimension=JudgeDimension.REGRESSION, severity=JudgeSeverity.HIGH,
                        summary="capacité mesurée en régression vs champion",
                    ),
                ),
            )
        return JudgeResult(
            judge_id="capability_regression", judge_version=version, dimension=JudgeDimension.REGRESSION,
            decision=JudgeDecision.PASS, score=candidate_score, confidence=1.0, evidence=evidence,
            rationale=f"aucune régression de capacité (delta={delta:.2f}).",
        )

    def _record_feedback(self, result: Any, genome_version_id: str) -> None:
        import contextlib

        from apex.metalearner.judge_feedback_service import JudgeFeedbackService

        # §17 : une panne d'enregistrement ne casse jamais le benchmark lui-même.
        with contextlib.suppress(Exception):
            JudgeFeedbackService(self._session_factory).record_signals(
                result, genome_version_id=genome_version_id,
            )

    # -- lecture -------------------------------------------------------------

    def get_latest(self, specialty: str, *, member_id: str) -> CapabilityBenchmarkRun | None:
        from apex.metalearner.gauntlet_repository import GauntletRepository

        results = GauntletRepository(self._session_factory).list_by_member(
            member_id, limit=1, stage=GauntletStage.BENCHMARK.value,
        )
        if not results:
            return None
        return CapabilityBenchmarkRun.from_gauntlet_result(
            results[0], genome_version_id="", benchmark_version=CAPABILITY_BENCHMARK_PROTOCOL_VERSION,
        )

    def get_history(self, specialty: str, *, limit: int = 200) -> tuple[CapabilityBenchmarkRun, ...]:
        from apex.metalearner.gauntlet_repository import GauntletRepository

        results = GauntletRepository(self._session_factory).list_by_specialty(
            specialty, limit=limit, stage=GauntletStage.BENCHMARK.value,
        )
        return tuple(
            CapabilityBenchmarkRun.from_gauntlet_result(
                r, genome_version_id="", benchmark_version=CAPABILITY_BENCHMARK_PROTOCOL_VERSION,
            )
            for r in results
        )

    def get_historical_best(self, specialty: str) -> dict[JudgeDimension, float]:
        """§9 de la mission : « candidate vs champion vs historical best »
        — le MEILLEUR score RÉELLEMENT observé, par dimension, sur TOUT
        l'historique persisté de la spécialité — jamais une estimation."""
        best: dict[JudgeDimension, float] = {}
        for run in self.get_history(specialty):
            for measurement in run.measurements:
                if measurement.score is None:
                    continue
                current = best.get(measurement.dimension)
                if current is None or measurement.score > current:
                    best[measurement.dimension] = measurement.score
        return best

    # -- delta + règle de promotion (§9/§12/§13) ---------------------------------

    def compute_delta(
        self, candidate_run: CapabilityBenchmarkRun, *, reference_run: CapabilityBenchmarkRun | None,
        reference_kind: str,
    ) -> CapabilityDelta:
        reference_scores = (
            {m.dimension: m.score for m in reference_run.measurements if m.score is not None}
            if reference_run is not None else {}
        )
        return self._compute_delta_vs_scores(
            candidate_run, reference_scores=reference_scores, reference_kind=reference_kind,
            reference_run_id=reference_run.id if reference_run is not None else None,
        )

    def compute_delta_vs_historical_best(self, candidate_run: CapabilityBenchmarkRun) -> CapabilityDelta:
        """§9 de la mission : « candidate vs champion vs historical best »
        — le meilleur score RÉELLEMENT observé par dimension (`get_
        historical_best`), jamais un `CapabilityBenchmarkRun` unique
        (le meilleur par dimension peut provenir de générations
        différentes) : pas de `reference_run_id` unique, honnêtement
        `None`."""
        best = self.get_historical_best(candidate_run.specialty)
        return self._compute_delta_vs_scores(
            candidate_run, reference_scores=best, reference_kind="historical_best", reference_run_id=None,
        )

    def _compute_delta_vs_scores(
        self, candidate_run: CapabilityBenchmarkRun, *, reference_scores: dict[JudgeDimension, float],
        reference_kind: str, reference_run_id: str | None,
    ) -> CapabilityDelta:
        deltas: dict[JudgeDimension, float | None] = {}
        regressed: list[JudgeDimension] = []
        missing: list[JudgeDimension] = []
        for dimension in CAPABILITY_DIMENSIONS:
            candidate_measurement = candidate_run.measurement_for(dimension)
            reference_score = reference_scores.get(dimension)
            if (
                candidate_measurement is None or candidate_measurement.score is None
                or reference_score is None
            ):
                deltas[dimension] = None
                missing.append(dimension)
                continue
            delta = candidate_measurement.score - reference_score
            deltas[dimension] = delta
            if delta < _CRITICAL_REGRESSION_THRESHOLD:
                regressed.append(dimension)
        return CapabilityDelta(
            specialty=candidate_run.specialty, candidate_run_id=candidate_run.id,
            reference_kind=reference_kind,
            reference_run_id=reference_run_id, dimension_deltas=deltas,
            critical_regression=bool(regressed), regressed_dimensions=tuple(regressed),
            missing_reference_dimensions=tuple(missing),
        )

    def decide_promotion(
        self, candidate_run: CapabilityBenchmarkRun, champion_delta: CapabilityDelta | None,
        *, min_confidence: float,
    ) -> PromotionRuleResult:
        """§13 de la mission — règle STRICTE, jamais un `PROMOTE` forcé :

            PROMOTE (`JudgeDecision.PASS`) SEULEMENT SI le Gauntlet du
            benchmark lui-même est `PASS` (aucun véto, aucune dimension
            individuellement rejetée) ET aucune régression critique vs le
            champion.

        `champion_delta=None` (aucun champion courant, ex. génération 0) ->
        `PASS` : rien à comparer, la première capacité observée ne peut pas
        « régresser »."""
        if candidate_run.overall_decision is JudgeDecision.REJECT:
            return PromotionRuleResult(
                decision=JudgeDecision.REJECT,
                reason="le benchmark de capacité lui-même a été rejeté (véto ou dimension en échec).",
            )
        if candidate_run.overall_decision is JudgeDecision.INCONCLUSIVE:
            return PromotionRuleResult(
                decision=JudgeDecision.INCONCLUSIVE,
                reason="preuve insuffisante sur le benchmark de capacité.",
            )
        if champion_delta is not None and champion_delta.critical_regression:
            blocking = champion_delta.regressed_dimensions[0]
            return PromotionRuleResult(
                decision=JudgeDecision.REJECT,
                reason=(
                    f"régression critique de capacité vs champion sur {blocking.value} "
                    f"(delta={champion_delta.dimension_deltas[blocking]:.2f})."
                ),
                blocking_dimension=blocking,
            )
        confidence = candidate_run.overall_confidence
        if confidence is not None and confidence < min_confidence:
            return PromotionRuleResult(
                decision=JudgeDecision.INCONCLUSIVE,
                reason=f"confiance du benchmark ({confidence}) sous le seuil requis.",
            )
        return PromotionRuleResult(
            decision=JudgeDecision.PASS, reason="capacité non régressée vs champion.",
        )

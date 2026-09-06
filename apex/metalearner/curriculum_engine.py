"""`CurriculumEngineService` — transforme des `GauntletWeakness` réelles
(N+9, §4/§5) en `CurriculumTarget` (passe « Judge Feedback -> Weakness ->
Curriculum -> Adaptive Evolution », §6).

**« Le Curriculum ne doit pas modifier directement les scores. Il produit
des expériences/tâches d'évaluation. »** (§6 de la mission) — chaque
`CurriculumTarget` porte soit :
    - un `GenerationMode` RÉEL déjà implémenté (`apex.taskgen.deterministic.
      DeterministicTaskGenerator`) + les données nécessaires pour
      l'invoquer (`base_task_id`) ;
    - des `focus_genome_paths` — des chemins RÉELLEMENT déjà exposés par le
      Genome (`apex.genome.clone_config._OVERRIDABLE_FIELDS`), jamais
      inventés (§7 : « NE PAS inventer de nouveaux Genome fields »).

**`SECURITY`/`GENERALIZATION` (passe « Adversarial Curriculum + Capability
Benchmarking », N+10)** : désormais RÉELS — `SECURITY` recommande
`GenerationMode.ADVERSARIAL` (`AdversarialKind.NETWORK_BOUNDARY_PROBE`,
§5 de la mission : « isolation, unsafe behavior, boundary violations » —
la frontière réseau du sandbox, seule frontière RÉELLEMENT observable ici)
; `GENERALIZATION` recommande `GenerationMode.NORMAL` avec
`GeneralizationVariant.ALTERNATE_PROBE` (§6 : une variante STRUCTURELLEMENT
distincte, jamais un doublon TRAIN/HELDOUT). `EFFICIENCY`/`ROBUSTNESS`
gagnent EN PLUS un vrai challenge ciblé (`EdgeCaseKind.BOUNDARY_TIMEOUT`/
`REPEATED_CALLS`) au-dessus du biais de mutation déjà existant (N+9) —
les deux coexistent, jamais l'un au lieu de l'autre. `BUG_FARM` reste
`PLANNED` comme MODE de génération (nécessite `CodeIntelligence`) — voir
`apex.metalearner.bug_farm_service` pour le suivi RÉEL des bugs, qui
réutilise `REGRESSION` (déjà réel), jamais ce mode.

**`REGRESSION`** utilise le générateur RÉEL `GenerationMode.REGRESSION`
(« reproduit exactement la commande d'une tâche existante ») avec la
tâche RÉELLE référencée par la preuve la plus récente de la faiblesse
(`EvalRun.task_id`, jamais un identifiant inventé).

**`CORRECTNESS`** ne fait QUE confirmer l'alignement avec le pipeline
DÉJÀ RÉEL et automatique (`apex.metalearner.selector.MetaLearner.
select_strategy` sélectionne déjà `GenerationMode.TARGETED_WEAKNESS`
depuis `apex.weakness.service.WeaknessDetectorService`, pour CHAQUE
membre via `run_experiment_cycle`) — ce module ne le déclenche PAS une
seconde fois, il l'observe seulement (`recommended_mode` documente ce qui
se passe déjà, jamais une seconde invocation)."""

from __future__ import annotations

from typing import Any

from apex.metalearner.judge_feedback_model import CurriculumTarget, GauntletWeakness
from apex.metalearner.judge_model import JudgeDimension
from apex.taskgen.model import AdversarialKind, EdgeCaseKind, GeneralizationVariant, GenerationMode

#: Dimension -> `EdgeCaseKind` d'un challenge RÉEL SUPPLÉMENTAIRE, en plus
#: du biais de mutation (`_DIMENSION_FOCUS_PATHS`) — jamais l'un au lieu de
#: l'autre (§ Phase 14 de la mission N+10 : le curriculum produit toujours
#: une TÂCHE, la mutation reste un biais séparé).
_DIMENSION_EDGE_CASE_KIND: dict[JudgeDimension, EdgeCaseKind] = {
    JudgeDimension.EFFICIENCY: EdgeCaseKind.BOUNDARY_TIMEOUT,
    JudgeDimension.ROBUSTNESS: EdgeCaseKind.REPEATED_CALLS,
}

#: Chemins RÉELLEMENT exposés par le Genome (`apex.genome.clone_config.
#: _OVERRIDABLE_FIELDS`, préfixés de leur clé de premier niveau
#: `"execution"`) — jamais un chemin inventé. `EFFICIENCY`/`ROBUSTNESS`
#: sont les deux SEULES dimensions dont une cause plausible (durée
#: d'exécution) correspond à un champ RÉELLEMENT branché
#: (`apex.genome.clone_config.resolve_resource_limits`).
_DIMENSION_FOCUS_PATHS: dict[JudgeDimension, tuple[str, ...]] = {
    JudgeDimension.EFFICIENCY: ("execution.timeout_s",),
    JudgeDimension.ROBUSTNESS: ("execution.timeout_s",),
}


class CurriculumEngineService:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def build_targets(self, specialty: str) -> tuple[CurriculumTarget, ...]:
        from apex.metalearner.weakness_engine import WeaknessEngineService

        weaknesses = WeaknessEngineService(self._session_factory).aggregate(specialty)
        return tuple(self._build_target(specialty, weakness) for weakness in weaknesses)

    def build_plan(self, specialty: str, generation: int, *, budget: Any = None) -> Any:
        """§ Phase 13 de la mission N+11 — enveloppe `build_targets` (N+9,
        inchangé) dans un `apex.metalearner.curriculum_plan.CurriculumPlan`
        déterministe, borné par `budget` (§14, `apex.config.schema.
        CurriculumBudgetPolicy`) si fourni."""
        from apex.metalearner.curriculum_plan import CurriculumPlanBuilder

        targets = self.build_targets(specialty)
        builder = CurriculumPlanBuilder(self._session_factory)
        return builder.build(specialty, generation, targets, budget=budget)

    def _build_target(self, specialty: str, weakness: GauntletWeakness) -> CurriculumTarget:
        if weakness.dimension is JudgeDimension.REGRESSION:
            base_task_id = self._most_recent_task_id(specialty, weakness)
            if base_task_id is not None:
                return CurriculumTarget(
                    specialty=specialty, dimension=weakness.dimension, weakness=weakness,
                    recommended_mode=GenerationMode.REGRESSION, base_task_id=base_task_id,
                    focus_genome_paths=(),
                    rationale=(
                        "régression réelle observée vis-à-vis du champion — "
                        f"GenerationMode.REGRESSION recommandé sur la tâche « {base_task_id} » "
                        "(reproduction exacte, apex.taskgen.deterministic)."
                    ),
                )
            return CurriculumTarget(
                specialty=specialty, dimension=weakness.dimension, weakness=weakness,
                recommended_mode=None, base_task_id=None, focus_genome_paths=(),
                rationale="régression observée mais aucune tâche source retrouvable (preuve insuffisante).",
            )

        if weakness.dimension is JudgeDimension.CORRECTNESS:
            return CurriculumTarget(
                specialty=specialty, dimension=weakness.dimension, weakness=weakness,
                recommended_mode=GenerationMode.TARGETED_WEAKNESS, base_task_id=None, focus_genome_paths=(),
                rationale=(
                    "faiblesse de correction — déjà couverte automatiquement par "
                    "apex.metalearner.selector.MetaLearner.select_strategy (GenerationMode."
                    "TARGETED_WEAKNESS, depuis apex.weakness.service.WeaknessDetectorService) "
                    "pour chaque membre ; ce module l'observe, ne la redéclenche pas."
                ),
            )

        if weakness.dimension is JudgeDimension.SECURITY:
            return CurriculumTarget(
                specialty=specialty, dimension=weakness.dimension, weakness=weakness,
                recommended_mode=GenerationMode.ADVERSARIAL, base_task_id=None, focus_genome_paths=(),
                adversarial_kind=AdversarialKind.NETWORK_BOUNDARY_PROBE,
                rationale=(
                    "faiblesse de sécurité — GenerationMode.ADVERSARIAL "
                    "(AdversarialKind.NETWORK_BOUNDARY_PROBE) recommandé : sonde la frontière réseau "
                    "du sandbox, la seule frontière de sécurité réellement observable ici (N+10 §5)."
                ),
            )

        if weakness.dimension is JudgeDimension.GENERALIZATION:
            return CurriculumTarget(
                specialty=specialty, dimension=weakness.dimension, weakness=weakness,
                recommended_mode=GenerationMode.NORMAL, base_task_id=None, focus_genome_paths=(),
                generalization_variant=GeneralizationVariant.ALTERNATE_PROBE,
                rationale=(
                    "faiblesse de généralisation — GenerationMode.NORMAL "
                    "(GeneralizationVariant.ALTERNATE_PROBE) recommandé : variation structurellement "
                    "distincte du TRAIN, jamais un doublon byte-à-byte (N+10 §6)."
                ),
            )

        focus_paths = _DIMENSION_FOCUS_PATHS.get(weakness.dimension, ())
        edge_case_kind = _DIMENSION_EDGE_CASE_KIND.get(weakness.dimension)
        if edge_case_kind is not None:
            return CurriculumTarget(
                specialty=specialty, dimension=weakness.dimension, weakness=weakness,
                recommended_mode=GenerationMode.EDGE_CASE, base_task_id=None,
                focus_genome_paths=focus_paths, edge_case_kind=edge_case_kind,
                rationale=(
                    f"faiblesse de {weakness.dimension.value} — GenerationMode.EDGE_CASE "
                    f"({edge_case_kind.value}) recommandé, en plus de la mutation orientée vers "
                    f"{focus_paths} (seul champ Genome réellement branché lié à cette dimension, "
                    "apex.genome.clone_config)."
                ),
            )
        if focus_paths:
            return CurriculumTarget(
                specialty=specialty, dimension=weakness.dimension, weakness=weakness,
                recommended_mode=None, base_task_id=None, focus_genome_paths=focus_paths,
                rationale=(
                    f"aucun générateur de tâche réel pour {weakness.dimension.value} — "
                    f"mutation orientée vers {focus_paths} (seul champ Genome réellement "
                    "branché lié à cette dimension, apex.genome.clone_config)."
                ),
            )

        return CurriculumTarget(
            specialty=specialty, dimension=weakness.dimension, weakness=weakness,
            recommended_mode=None, base_task_id=None, focus_genome_paths=(),
            rationale=(
                f"aucun générateur de tâche réel ni champ Genome réellement exposé pour "
                f"{weakness.dimension.value} — observabilité seule dans cette passe."
            ),
        )

    def _most_recent_task_id(self, specialty: str, weakness: GauntletWeakness) -> str | None:
        from apex.evalrunner.repository import EvalRunRepository
        from apex.metalearner.weakness_engine import WeaknessEngineService

        history = WeaknessEngineService(self._session_factory).history(
            specialty, dimension=weakness.dimension,
        )
        relevant = [
            s for s in history
            if s.failure_category == weakness.failure_category and s.evidence_reference is not None
        ]
        if not relevant:
            return None
        most_recent = max(relevant, key=lambda s: s.created_at)
        evidence_reference = most_recent.evidence_reference
        if evidence_reference is None:
            return None
        eval_run = EvalRunRepository(self._session_factory).get(evidence_reference)
        return eval_run.task_id if eval_run is not None else None

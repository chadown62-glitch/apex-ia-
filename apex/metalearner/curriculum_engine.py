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

**Frontière honnête (§6/§7)** : `EDGE_CASE`/`ADVERSARIAL`/`BUG_FARM`
(`apex.taskgen.model.GenerationMode`) restent `PLANNED` — aucune tâche
n'est jamais fabriquée pour `SECURITY`/`GENERALIZATION` (aucun générateur
réel, aucun champ Genome réel ne les gouverne) ; `recommended_mode`/
`base_task_id` restent `None`, `focus_genome_paths` reste vide, et
`rationale` le documente explicitement plutôt que de le masquer.

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
from apex.taskgen.model import GenerationMode

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

        focus_paths = _DIMENSION_FOCUS_PATHS.get(weakness.dimension, ())
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
                f"{weakness.dimension.value} — observabilité seule dans cette passe "
                "(EDGE_CASE/ADVERSARIAL restent PLANNED, network reste administrateur seul)."
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

"""`run_experiment_cycle` — la première boucle expérimentale RÉELLE bout en
bout d'APEX, composant EXCLUSIVEMENT des abstractions déjà réelles :

    WeaknessDetectorService (Phase 12)
        -> MetaLearner.select_strategy (Phase 22)
        -> DeterministicTaskGenerator (Phase 15)
        -> TaskBankService.create (Phase 5, HUMAIN seul)
        -> EvalRunnerService.run (Phase 5, CORE seul, sandbox RÉELLE)
        -> WeaknessDetectorService (relecture, preuve de mise à jour)

Même patron de composition qu'`apex.missions.execution.launch_mission` : une
fonction PURE (jamais une classe/un « ExperimentEngine ») qui assemble des
points d'entrée déjà individuellement autorisés — aucune règle
`PolicyEngine` dupliquée ici, chaque étape composée applique déjà la
sienne. Ce n'est PAS une nouvelle abstraction au sens du registre : aucune
entrée `apex.interfaces.registry` ne lui correspond, exactement comme
`launch_mission` n'en a pas.

**Gouvernance préservée à l'identique** : `requested_by` doit être
`Subject.HUMAN` pour que `TaskBankService.create()` réussisse — cette
fonction ne peut PAS être déclenchée par un clone ou par CORE seul, un
humain reste la seule origine autorisée d'un nouveau cycle (§8
anti-auto-illusion : aucune auto-déclaration de réussite comme preuve).
L'exécution elle-même (`EXECUTE_IN_SANDBOX`) reste CORE seul, appliquée par
`AgentRuntime`, jamais contournée. Aucune promotion, aucune écriture
Genome/Skill : un cycle ne fait qu'observer, générer, exécuter, mesurer —
la décision d'exploiter le résultat (proposer un Skill, une révision de
Genome) reste un acte séparé, explicite, humain.

**`experiment_ref`/`cancel_token`** (passe « Autonomous Learning Trigger »,
purement additifs, défaut `None` — aucun appelant existant n'est affecté) :
simple relais vers `EvalRunnerService.run`, qui les accepte déjà tous les
deux depuis la phase 5 (corrélation de regroupement, annulation réelle de
la sandbox) — jamais une seconde implémentation de l'un ou l'autre.
Utilisés par `apex.metalearner.autonomy.AutonomousLearningService` pour
étiqueter/annuler un cycle démarré via le CLI/l'API/le Scheduler, sans que
`POST /experiment-cycles` (appel humain direct, synchrone) n'ait besoin de
les fournir.
"""

from __future__ import annotations

from typing import Any

from apex.agents.runtime import AgentRuntime
from apex.evalrunner.model import EvalMethod
from apex.evalrunner.service import EvalRunnerService
from apex.metalearner.model import ExperimentCycleResult
from apex.metalearner.selector import MetaLearner
from apex.policy.model import Subject
from apex.sandbox.model import CancellationToken
from apex.taskgen.deterministic import DeterministicTaskGenerator
from apex.taskgen.model import GenerationMode
from apex.taskgen.provider import TaskGenerator
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService
from apex.weakness.service import WeaknessDetectorService

#: Modes qui consomment `level` (progression paramétrée) plutôt que
#: `weakness_report` (renforcement ciblé) — voir
#: `apex.taskgen.deterministic.DeterministicTaskGenerator.generate`.
_LEVEL_DRIVEN_MODES = frozenset({GenerationMode.INCREASING_DIFFICULTY, GenerationMode.CURRICULUM_INTERMEDIATE})


def run_experiment_cycle(
    *,
    session_factory: Any,
    agent_runtime: AgentRuntime,
    clone_id: str,
    specialty: str,
    current_level: int,
    requested_by: Subject,
    method: EvalMethod = EvalMethod.PRACTICE,
    split: TaskSplit = TaskSplit.TRAIN,
    experiment_ref: str | None = None,
    cancel_token: CancellationToken | None = None,
    weakness_service: WeaknessDetectorService | None = None,
    task_service: TaskBankService | None = None,
    evalrunner: EvalRunnerService | None = None,
    generator: TaskGenerator | None = None,
    learner: MetaLearner | None = None,
) -> ExperimentCycleResult:
    """UN cycle complet : observe -> décide -> génère -> exécute -> mesure.

    Ne boucle PAS elle-même (« nouvelle expérience » du diagramme reste la
    responsabilité de l'appelant, qui décide s'il relance un cycle à partir
    de `weakness_after`) — jamais une boucle infinie cachée dans une
    fonction que l'appelant croirait ponctuelle.
    """
    weakness_service = weakness_service or WeaknessDetectorService(session_factory)
    task_service = task_service or TaskBankService(session_factory)
    evalrunner = evalrunner or EvalRunnerService(session_factory, agent_runtime=agent_runtime)
    generator = generator or DeterministicTaskGenerator()
    learner = learner or MetaLearner()

    # Volontairement PAS filtré par `clone_id` : `AgentRuntime` spawn des
    # clones à usage unique (`execute_in_sandbox` les termine après UN
    # appel — voir `apex.agents.runtime`) — filtrer la faiblesse par clone
    # la remettrait à INSUFFICIENT_DATA à chaque nouveau cycle, quel que
    # soit l'historique déjà accumulé. La faiblesse est une propriété du
    # DOMAINE (spécialité), agrégée à travers tous les clones qui l'ont
    # tentée — jamais réinitialisée par la simple rotation d'un clone
    # jetable.
    weakness_before = weakness_service.detect_for_specialty(specialty)
    decision = learner.select_strategy(
        specialty=specialty, weakness_report=weakness_before, current_level=current_level,
    )

    # Aucun signal exploitable -> repli NORMAL (Phase 15) pour commencer à
    # accumuler des données, jamais une stratégie devinée.
    mode = decision.mode or GenerationMode.NORMAL
    spec = generator.generate(
        mode=mode, specialty=specialty, split=split,
        level=decision.level if mode in _LEVEL_DRIVEN_MODES else None,
        weakness_report=weakness_before if mode is GenerationMode.TARGETED_WEAKNESS else None,
    )

    task = task_service.create(
        split=spec.split, specialty=spec.specialty, title=spec.title, description=spec.description,
        command=spec.command, created_by=requested_by, dataset_ref=spec.dataset_ref,
    )

    # `strategy_facts` (ajout minimal à `EvalRunnerService.run`, optionnel,
    # aucune migration — voir `apex.evalrunner.service._build_eval_run`)
    # associe désormais CE run à la décision qui l'a produit : la brique
    # manquante identifiée par la directive Phase 5+ pour permettre une
    # future sélection empirique (contexte de faiblesse -> stratégie ->
    # résultat), sans dupliquer `StrategyDecision` dans une nouvelle table.
    eval_run = evalrunner.run(
        task_id=task.id, clone_id=clone_id, method=method, requested_by=Subject.CORE,
        experiment_ref=experiment_ref, cancel_token=cancel_token,
        strategy_facts={
            "generation_mode": mode.value,
            "curriculum_level": decision.level,
            "curriculum_direction": decision.curriculum_decision.direction.value,
            "weakness_category_before": weakness_before.category.value,
        },
    )

    # Même raisonnement qu'au-dessus pour `weakness_before` : agrégée par
    # spécialité, jamais filtrée par un clone jetable.
    weakness_after = weakness_service.detect_for_specialty(specialty)

    return ExperimentCycleResult(
        decision=decision, task=task, eval_run=eval_run,
        weakness_before=weakness_before, weakness_after=weakness_after,
    )

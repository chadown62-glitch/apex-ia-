"""Intégration réelle : `apex.metalearner.cycle.run_experiment_cycle` — la
première boucle expérimentale bout en bout, sous vraie sandbox namespaced.

Preuve recherchée :
  1. Multi-cycle réel : bootstrap (INSUFFICIENT_DATA -> NORMAL) puis
     progression informée (NO_WEAKNESS -> CURRICULUM_INTERMEDIATE) une fois
     qu'un vrai EvalRun existe — PAS de régression vers le bug corrigé où la
     faiblesse était filtrée par `clone_id` (clones à usage unique) et
     retombait à INSUFFICIENT_DATA à chaque cycle malgré l'historique.
  2. Gouvernance intacte : `requested_by` non-HUMAN fait échouer le cycle
     (via `TaskBankService.create`, PolicyEngine réel, aucune règle
     dupliquée ici) ; aucune écriture Genome/Skill n'a lieu pendant un cycle.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import PolicyDenied
from apex.genome.repository import GenomeVersionRepository
from apex.metalearner.cycle import run_experiment_cycle
from apex.oracles.model import OracleVerdict
from apex.policy.model import Subject
from apex.skills.repository import SkillRepository
from apex.taskgen.model import GenerationMode
from apex.weakness.model import WeaknessCategory


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def a_pool(**overrides) -> ComputePool:
    defaults = dict(
        pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
        capabilities=(Capability.CPU_EVAL,),
    )
    defaults.update(overrides)
    return ComputePool(**defaults)


class TestExperimentCycleProgressesAcrossRealCycles(unittest.TestCase):
    """Preuve de bout en bout : 3 clones réels, à usage unique chacun,
    exécutant 3 tâches réelles sous sandbox namespaced réelle."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)

    def spawn_clone(self):
        return self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:x", spawned_by=Subject.CORE,
        )

    def run_cycle(self, *, clone_id: str, current_level: int):
        return run_experiment_cycle(
            session_factory=self.factory, agent_runtime=self.runtime, clone_id=clone_id,
            specialty="research", current_level=current_level, requested_by=Subject.HUMAN,
        )

    def test_first_cycle_bootstraps_from_insufficient_data(self) -> None:
        clone = self.spawn_clone()
        result = self.run_cycle(clone_id=clone.clone_id, current_level=1)
        self.assertEqual(result.weakness_before.category, WeaknessCategory.INSUFFICIENT_DATA)
        self.assertIsNone(result.decision.mode)
        self.assertEqual(result.eval_run.oracle_verdict, OracleVerdict.PASS)

    def test_second_cycle_sees_first_cycles_evidence_despite_fresh_clone(self) -> None:
        """Le cœur de la régression corrigée : un DEUXIÈME clone (le premier
        est terminé après son unique exécution) doit voir l'historique
        accumulé par le PREMIER clone — la faiblesse est une propriété de la
        spécialité, jamais réinitialisée par la rotation d'un clone."""
        first_clone = self.spawn_clone()
        self.run_cycle(clone_id=first_clone.clone_id, current_level=1)

        second_clone = self.spawn_clone()
        self.assertNotEqual(second_clone.clone_id, first_clone.clone_id)
        result = self.run_cycle(clone_id=second_clone.clone_id, current_level=1)

        self.assertNotEqual(result.weakness_before.category, WeaknessCategory.INSUFFICIENT_DATA)
        self.assertEqual(result.weakness_before.category, WeaknessCategory.NO_WEAKNESS)
        self.assertEqual(result.decision.mode, GenerationMode.CURRICULUM_INTERMEDIATE)
        self.assertEqual(result.decision.level, 2)

    def test_third_cycle_continues_the_curriculum_progression(self) -> None:
        level = 1
        for _ in range(3):
            clone = self.spawn_clone()
            result = self.run_cycle(clone_id=clone.clone_id, current_level=level)
            level = result.decision.level
        self.assertEqual(level, 3)
        self.assertEqual(result.weakness_after.category, WeaknessCategory.NO_WEAKNESS)

    def test_eval_run_carries_the_strategy_that_produced_it(self) -> None:
        """Priorité 5 (MetaLearner v2) : chaque `EvalRun` produit par un
        cycle porte désormais la décision qui l'a motivé sous
        `facts["strategy"]` — la brique minimale nécessaire pour une future
        sélection empirique, sans nouvelle table."""
        clone = self.spawn_clone()
        result = self.run_cycle(clone_id=clone.clone_id, current_level=1)
        strategy = result.eval_run.facts["strategy"]
        self.assertEqual(strategy["generation_mode"], GenerationMode.NORMAL.value)
        self.assertEqual(strategy["weakness_category_before"], WeaknessCategory.INSUFFICIENT_DATA.value)
        self.assertEqual(strategy["curriculum_level"], result.decision.level)

    def test_result_is_traceable_to_real_task_and_eval_run_ids(self) -> None:
        clone = self.spawn_clone()
        result = self.run_cycle(clone_id=clone.clone_id, current_level=1)
        self.assertEqual(result.eval_run.task_id, result.task.id)
        self.assertIsNotNone(result.eval_run.id)
        self.assertIsNotNone(result.task.id)


class TestExperimentCyclePreservesGovernance(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:x", spawned_by=Subject.CORE,
        )

    def test_a_cycle_cannot_be_requested_by_core(self) -> None:
        """CORE ne peut pas déclencher un cycle : seul HUMAN a
        `Action.CREATE_TASK` (voir `apex.policy.engine`). Aucune règle
        n'est dupliquée dans `run_experiment_cycle` — c'est
        `TaskBankService.create` qui applique la vraie `PolicyEngine`."""
        with self.assertRaises(PolicyDenied):
            run_experiment_cycle(
                session_factory=self.factory, agent_runtime=self.runtime, clone_id=self.clone.clone_id,
                specialty="research", current_level=1, requested_by=Subject.CORE,
            )

    def test_a_cycle_cannot_be_requested_by_a_clone_subject(self) -> None:
        with self.assertRaises(PolicyDenied):
            run_experiment_cycle(
                session_factory=self.factory, agent_runtime=self.runtime, clone_id=self.clone.clone_id,
                specialty="research", current_level=1, requested_by=Subject.RESEARCH,
            )

    def test_a_cycle_never_writes_a_genome_version(self) -> None:
        run_experiment_cycle(
            session_factory=self.factory, agent_runtime=self.runtime, clone_id=self.clone.clone_id,
            specialty="research", current_level=1, requested_by=Subject.HUMAN,
        )
        self.assertEqual(GenomeVersionRepository(self.factory).list_all(), [])

    def test_a_cycle_never_writes_a_skill(self) -> None:
        run_experiment_cycle(
            session_factory=self.factory, agent_runtime=self.runtime, clone_id=self.clone.clone_id,
            specialty="research", current_level=1, requested_by=Subject.HUMAN,
        )
        self.assertEqual(SkillRepository(self.factory).list_all(), [])


if __name__ == "__main__":
    unittest.main()

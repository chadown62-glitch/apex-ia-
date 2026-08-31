"""Intégration réelle : `SkillRegistryService.propose_from_evidence` —
capitalisation d'une compétence à partir de vrais `EvalRun`, sous vraie
sandbox namespaced (même patron que tests/test_taskgen_integration.py et
tests/test_metalearner_cycle.py).

Preuve recherchée : une compétence n'est JAMAIS proposée sur la seule
affirmation d'un clone — seule une preuve statistiquement calibrée
(intervalle de Wilson, borne inférieure) issue de vrais `EvalRun`, jugés
par un `OracleProvider` indépendant, peut déclencher une proposition.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import PolicyDenied
from apex.evalrunner.model import EvalMethod
from apex.evalrunner.service import EvalRunnerService
from apex.policy.model import Subject
from apex.skills.model import SkillStatus
from apex.skills.service import SkillRegistryService
from apex.taskgen.deterministic import DeterministicTaskGenerator
from apex.taskgen.model import GenerationMode
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService


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


class TestProposeFromEvidence(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.tasks = TaskBankService(self.factory)
        self.evalrunner = EvalRunnerService(self.factory, agent_runtime=self.runtime)
        self.generator = DeterministicTaskGenerator()
        self.skills = SkillRegistryService(self.factory)

    def run_real_passing_task(self) -> None:
        """UN cycle réel : tâche NORMAL (toujours réussie, cf.
        test_taskgen_integration.py) exécutée par un clone frais et à
        usage unique — jamais réutilisé entre deux runs."""
        clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:x", spawned_by=Subject.CORE,
        )
        spec = self.generator.generate(mode=GenerationMode.NORMAL, specialty="research")
        task = self.tasks.create(
            split=TaskSplit.TRAIN, specialty=spec.specialty, title=spec.title,
            description=spec.description, command=spec.command, created_by=Subject.HUMAN,
        )
        self.evalrunner.run(task_id=task.id, clone_id=clone.clone_id, method=EvalMethod.PRACTICE)

    def test_refuses_with_no_evidence_at_all(self) -> None:
        with self.assertRaises(ValueError):
            self.skills.propose_from_evidence(
                name="research-basics", specialty="research", description="d",
                created_by=Subject.HUMAN,
            )

    def test_refuses_below_the_minimum_sample_for_calibration(self) -> None:
        self.run_real_passing_task()
        self.run_real_passing_task()
        with self.assertRaises(ValueError):
            self.skills.propose_from_evidence(
                name="research-basics", specialty="research", description="d",
                created_by=Subject.HUMAN,
            )

    def test_refuses_below_the_calibrated_lower_bound_threshold(self) -> None:
        """Même avec un échantillon calibrable (n=3), la borne inférieure
        d'une série 100% réussie mais minuscule reste sous 70% — le seuil
        protège contre une petite série chanceuse."""
        for _ in range(3):
            self.run_real_passing_task()
        with self.assertRaises(ValueError):
            self.skills.propose_from_evidence(
                name="research-basics", specialty="research", description="d",
                created_by=Subject.HUMAN,
            )

    def test_proposes_a_real_skill_once_evidence_clears_the_calibrated_bound(self) -> None:
        for _ in range(10):
            self.run_real_passing_task()
        skill = self.skills.propose_from_evidence(
            name="research-basics", specialty="research", description="tâches de recherche de base",
            created_by=Subject.HUMAN,
        )
        self.assertEqual(skill.status, SkillStatus.PROPOSED)
        self.assertEqual(len(skill.evidence_run_ids), 10)
        self.assertIsNotNone(skill.confidence)
        self.assertGreater(skill.confidence, 0.7)

    def test_a_clone_subject_cannot_propose_from_evidence(self) -> None:
        for _ in range(10):
            self.run_real_passing_task()
        with self.assertRaises(PolicyDenied):
            self.skills.propose_from_evidence(
                name="research-basics", specialty="research", description="d",
                created_by=Subject.RESEARCH,
            )

    def test_proposed_from_evidence_is_never_auto_acquired(self) -> None:
        """§8 anti-auto-illusion : une preuve statistique, même forte, ne
        promeut jamais elle-même — seule une décision HUMAINE explicite
        (`promote`) fait passer de PROPOSED à ACQUIRED."""
        for _ in range(10):
            self.run_real_passing_task()
        skill = self.skills.propose_from_evidence(
            name="research-basics", specialty="research", description="d", created_by=Subject.HUMAN,
        )
        reloaded = self.skills.get(skill.id)
        self.assertEqual(reloaded.status, SkillStatus.PROPOSED)

    def test_evidence_run_ids_are_real_and_traceable_to_eval_runs(self) -> None:
        for _ in range(10):
            self.run_real_passing_task()
        skill = self.skills.propose_from_evidence(
            name="research-basics", specialty="research", description="d", created_by=Subject.HUMAN,
        )
        from apex.evalrunner.repository import EvalRunRepository

        eval_repo = EvalRunRepository(self.factory)
        for run_id in skill.evidence_run_ids:
            self.assertIsNotNone(eval_repo.get(run_id))


if __name__ == "__main__":
    unittest.main()

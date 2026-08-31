"""Intégration réelle : `apex.selfmodel` — « que sait APEX ? » et « pourquoi
pense-t-il le savoir ? », sous vraie sandbox namespaced (même patron que
tests/test_skills_evidence_integration.py).

Preuve recherchée : la vue est READ-ONLY, dérivée entièrement de composants
déjà réels (SkillRegistry/WeaknessDetector/ConfidenceCalibrator/EvalRunner/
CostGainEstimator) — jamais une table séparée, jamais une donnée devinée
pour une spécialité sans historique.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.agents.runtime import AgentRuntime
from apex.calibration.model import CalibrationStatus
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.evalrunner.model import EvalMethod
from apex.evalrunner.service import EvalRunnerService
from apex.oracles.model import OracleVerdict
from apex.policy.model import Subject
from apex.selfmodel.view import get_self_model_view, trace_skill_provenance
from apex.skills.service import SkillRegistryService
from apex.taskgen.deterministic import DeterministicTaskGenerator
from apex.taskgen.model import GenerationMode
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService
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


class TestSelfModelView(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.tasks = TaskBankService(self.factory)
        self.evalrunner = EvalRunnerService(self.factory, agent_runtime=self.runtime)
        self.generator = DeterministicTaskGenerator()
        self.skills = SkillRegistryService(self.factory)

    def run_real_passing_task(self, specialty: str = "research") -> None:
        clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:evidence-genome", spawned_by=Subject.CORE,
        )
        spec = self.generator.generate(mode=GenerationMode.NORMAL, specialty=specialty)
        task = self.tasks.create(
            split=TaskSplit.TRAIN, specialty=spec.specialty, title=spec.title,
            description=spec.description, command=spec.command, created_by=Subject.HUMAN,
        )
        self.evalrunner.run(task_id=task.id, clone_id=clone.clone_id, method=EvalMethod.PRACTICE)

    def test_a_specialty_with_no_history_is_honestly_empty(self) -> None:
        view = get_self_model_view(self.factory, specialties=["research"])
        self.assertEqual(len(view.specialties), 1)
        knowledge = view.specialties[0]
        self.assertEqual(knowledge.weakness_report.category, WeaknessCategory.INSUFFICIENT_DATA)
        self.assertEqual(knowledge.calibrated_confidence.status, CalibrationStatus.INSUFFICIENT_DATA)
        self.assertEqual(knowledge.acquired_skills, ())
        self.assertEqual(knowledge.eval_run_count, 0)
        self.assertEqual(knowledge.genome_refs_used, ())

    def test_view_reflects_real_accumulated_evidence(self) -> None:
        for _ in range(10):
            self.run_real_passing_task()
        view = get_self_model_view(self.factory, specialties=["research"])
        knowledge = view.specialties[0]
        self.assertEqual(knowledge.eval_run_count, 10)
        self.assertEqual(knowledge.weakness_report.category, WeaknessCategory.NO_WEAKNESS)
        self.assertEqual(knowledge.calibrated_confidence.status, CalibrationStatus.CALIBRATED)
        self.assertEqual(knowledge.genome_refs_used, ("sha256:evidence-genome",))
        self.assertIn(EvalMethod.PRACTICE, knowledge.methods_used)
        self.assertEqual(knowledge.cost.experiment_count, 10)

    def test_view_reflects_a_real_acquired_skill(self) -> None:
        for _ in range(10):
            self.run_real_passing_task()
        proposed = self.skills.propose_from_evidence(
            name="research-basics", specialty="research", description="d", created_by=Subject.HUMAN,
        )
        self.skills.promote(proposed.id, requested_by=Subject.HUMAN)

        view = get_self_model_view(self.factory, specialties=["research"])
        knowledge = view.specialties[0]
        self.assertEqual(len(knowledge.acquired_skills), 1)
        self.assertEqual(knowledge.acquired_skills[0].id, proposed.id)
        self.assertIsNotNone(knowledge.last_skill_validated_at)

    def test_multiple_specialties_are_independent(self) -> None:
        for _ in range(3):
            self.run_real_passing_task("research")
        view = get_self_model_view(self.factory, specialties=["research", "engineering"])
        by_specialty = {k.specialty: k for k in view.specialties}
        self.assertEqual(by_specialty["research"].eval_run_count, 3)
        self.assertEqual(by_specialty["engineering"].eval_run_count, 0)


class TestSkillProvenanceTrace(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.tasks = TaskBankService(self.factory)
        self.evalrunner = EvalRunnerService(self.factory, agent_runtime=self.runtime)
        self.generator = DeterministicTaskGenerator()
        self.skills = SkillRegistryService(self.factory)

    def run_real_passing_task(self) -> None:
        clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:trace-genome", spawned_by=Subject.CORE,
        )
        spec = self.generator.generate(mode=GenerationMode.NORMAL, specialty="research")
        task = self.tasks.create(
            split=TaskSplit.TRAIN, specialty=spec.specialty, title=spec.title,
            description=spec.description, command=spec.command, created_by=Subject.HUMAN,
        )
        self.evalrunner.run(task_id=task.id, clone_id=clone.clone_id, method=EvalMethod.PRACTICE)

    def test_provenance_traces_every_evidence_run_to_its_full_context(self) -> None:
        for _ in range(10):
            self.run_real_passing_task()
        skill = self.skills.propose_from_evidence(
            name="research-basics", specialty="research", description="d", created_by=Subject.HUMAN,
        )

        provenance = trace_skill_provenance(self.factory, skill.id)
        self.assertEqual(provenance.skill.id, skill.id)
        self.assertEqual(len(provenance.entries), 10)
        for entry in provenance.entries:
            self.assertEqual(entry.task_specialty, "research")
            self.assertEqual(entry.clone_genome_ref, "sha256:trace-genome")
            self.assertEqual(entry.oracle_verdict, OracleVerdict.PASS)
            self.assertEqual(entry.method, EvalMethod.PRACTICE)

    def test_unknown_skill_raises(self) -> None:
        with self.assertRaises(LookupError):
            trace_skill_provenance(self.factory, "does-not-exist")


if __name__ == "__main__":
    unittest.main()

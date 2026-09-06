"""Tests de `apex.metalearner.challenge_service`/`bug_farm_service` (passe
« Adversarial Curriculum + Capability Benchmarking », N+10). Lettres C/D/E
du §19 de la mission citées explicitement — même infra SQLite que
`tests/test_judge_feedback.py`."""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.genome.service import GenomeStoreService
from apex.metalearner.bug_farm_model import BugStatus
from apex.metalearner.bug_farm_service import BugFarmService
from apex.metalearner.challenge_model import ChallengeSource
from apex.metalearner.challenge_service import CurriculumChallengeService
from apex.metalearner.judge_feedback_model import FailureCategory, JudgeLearningSignal, SignalCategory
from apex.metalearner.judge_feedback_repository import JudgeLearningSignalRepository
from apex.metalearner.judge_model import JudgeDecision, JudgeDimension, JudgeSeverity
from apex.policy.model import Subject
from apex.sandbox.model import ResourceLimits


def sqlite_session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True,
    )
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def a_limits() -> ResourceLimits:
    return ResourceLimits(cpu_cores=1, memory_mb=512, pids_limit=32, timeout_s=10.0, disk_mb=64, network="none")


def make_signal(generation, dimension, category, decision, failure_category=None, severity=None):
    return JudgeLearningSignal.new(
        gauntlet_result_id=f"g{generation}", member_id=f"m{generation}", specialty="research",
        generation=generation, genome_version_id=f"genome-{generation}", judge_id=dimension.value,
        judge_version="1", dimension=dimension, decision=decision, category=category,
        failure_category=failure_category, severity=severity, score=0.0, confidence=1.0,
        finding_summary="test", evidence_reference=None, protocol_version="1",
    )


class TestSecurityChallengeGeneration(unittest.TestCase):
    """Lettre C du §19 — un VRAI challenge adversarial, jamais un texte
    d'observabilité seule."""

    def test_security_weakness_produces_a_real_persisted_adversarial_task(self) -> None:
        factory = sqlite_session_factory()
        genome = GenomeStoreService(factory)
        genome.propose(content={"a": 1}, created_by=Subject.HUMAN)
        genome.promote(genome.list_all()[0].id, requested_by=Subject.HUMAN)

        JudgeLearningSignalRepository(factory).create(
            make_signal(
                0, JudgeDimension.SECURITY, SignalCategory.CRITICAL_FAILURE, JudgeDecision.REJECT,
                FailureCategory.GENUINE_CAPABILITY_WEAKNESS, JudgeSeverity.CRITICAL,
            ),
        )
        challenges = CurriculumChallengeService(factory).build_challenges("research", generation=1, limits=a_limits())
        self.assertEqual(len(challenges), 1)
        challenge = challenges[0]
        self.assertEqual(challenge.dimension, JudgeDimension.SECURITY)
        self.assertEqual(challenge.source, ChallengeSource.SECURITY)
        self.assertFalse(challenge.heldout)
        self.assertTrue(challenge.adversarial)

        from apex.tasks.repository import TaskRepository

        task = TaskRepository(factory).get(challenge.task_id)
        self.assertIsNotNone(task)
        self.assertIn("réseau", task.description.lower() + "reseau")  # provenance non fabriquée


class TestGeneralizationChallengeGeneration(unittest.TestCase):
    """Lettre D du §19 — un challenge HELDOUT, structurellement distinct
    du curriculum d'entraînement."""

    def test_generalization_weakness_produces_a_real_heldout_task(self) -> None:
        factory = sqlite_session_factory()
        genome = GenomeStoreService(factory)
        genome.propose(content={"a": 1}, created_by=Subject.HUMAN)
        genome.promote(genome.list_all()[0].id, requested_by=Subject.HUMAN)

        JudgeLearningSignalRepository(factory).create(
            make_signal(
                0, JudgeDimension.GENERALIZATION, SignalCategory.CRITICAL_FAILURE, JudgeDecision.REJECT,
                FailureCategory.GENUINE_CAPABILITY_WEAKNESS, JudgeSeverity.CRITICAL,
            ),
        )
        challenges = CurriculumChallengeService(factory).build_challenges("research", generation=1, limits=a_limits())
        self.assertEqual(len(challenges), 1)
        challenge = challenges[0]
        self.assertTrue(challenge.heldout)
        self.assertFalse(challenge.adversarial)
        self.assertEqual(challenge.source, ChallengeSource.GENERALIZATION)

        from apex.tasks.model import TaskSplit
        from apex.tasks.repository import TaskRepository

        task = TaskRepository(factory).get(challenge.task_id)
        self.assertEqual(task.split, TaskSplit.HELDOUT)


class TestChallengeIdempotency(unittest.TestCase):
    def test_same_generation_never_produces_a_duplicate_challenge(self) -> None:
        factory = sqlite_session_factory()
        genome = GenomeStoreService(factory)
        genome.propose(content={"a": 1}, created_by=Subject.HUMAN)
        genome.promote(genome.list_all()[0].id, requested_by=Subject.HUMAN)

        JudgeLearningSignalRepository(factory).create(
            make_signal(
                0, JudgeDimension.SECURITY, SignalCategory.CRITICAL_FAILURE, JudgeDecision.REJECT,
                FailureCategory.GENUINE_CAPABILITY_WEAKNESS, JudgeSeverity.CRITICAL,
            ),
        )
        service = CurriculumChallengeService(factory)
        first = service.build_challenges("research", generation=1, limits=a_limits())
        second = service.build_challenges("research", generation=1, limits=a_limits())
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)


class TestBugFarmTransitions(unittest.TestCase):
    """Lettre E du §19 — OPEN -> FIXED -> REGRESSED -> FIXED, dérivé
    exclusivement de `WeaknessEngineService.aggregate` (N+9, inchangé)."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.repo = JudgeLearningSignalRepository(self.factory)
        self.service = BugFarmService(self.factory)

    def test_full_lifecycle_open_fixed_regressed_fixed_again(self) -> None:
        self.repo.create(
            make_signal(
                0, JudgeDimension.SECURITY, SignalCategory.CRITICAL_FAILURE, JudgeDecision.REJECT,
                FailureCategory.GENUINE_CAPABILITY_WEAKNESS, JudgeSeverity.CRITICAL,
            ),
        )
        bugs = self.service.sync_from_weakness_history("research", 0)
        self.assertEqual(bugs[0].status, BugStatus.OPEN)

        self.repo.create(make_signal(1, JudgeDimension.CORRECTNESS, SignalCategory.SUCCESS, JudgeDecision.PASS))
        self.repo.create(make_signal(2, JudgeDimension.CORRECTNESS, SignalCategory.SUCCESS, JudgeDecision.PASS))
        bugs = self.service.sync_from_weakness_history("research", 2)
        self.assertEqual(bugs[0].status, BugStatus.FIXED)
        self.assertEqual(bugs[0].fixed_at_generation, 2)

        self.repo.create(
            make_signal(
                3, JudgeDimension.SECURITY, SignalCategory.CRITICAL_FAILURE, JudgeDecision.REJECT,
                FailureCategory.GENUINE_CAPABILITY_WEAKNESS, JudgeSeverity.CRITICAL,
            ),
        )
        bugs = self.service.sync_from_weakness_history("research", 3)
        self.assertEqual(bugs[0].status, BugStatus.REGRESSED)
        self.assertEqual(bugs[0].regressed_at_generation, 3)
        self.assertIn("research:security:genuine_capability_weakness", [b.weakness_id for b in self.service.list_regressed("research")])

        self.repo.create(make_signal(4, JudgeDimension.CORRECTNESS, SignalCategory.SUCCESS, JudgeDecision.PASS))
        self.repo.create(make_signal(5, JudgeDimension.CORRECTNESS, SignalCategory.SUCCESS, JudgeDecision.PASS))
        bugs = self.service.sync_from_weakness_history("research", 5)
        self.assertEqual(bugs[0].status, BugStatus.FIXED)
        self.assertEqual(self.service.list_regressed("research"), ())

    def test_resyncing_the_same_state_produces_no_extra_transition(self) -> None:
        self.repo.create(
            make_signal(
                0, JudgeDimension.SECURITY, SignalCategory.CRITICAL_FAILURE, JudgeDecision.REJECT,
                FailureCategory.GENUINE_CAPABILITY_WEAKNESS, JudgeSeverity.CRITICAL,
            ),
        )
        self.service.sync_from_weakness_history("research", 0)
        second = self.service.sync_from_weakness_history("research", 0)
        self.assertEqual(second, ())


if __name__ == "__main__":
    unittest.main()

"""Tests de `apex.metalearner.feedback.LearningFeedbackService` — passe
« Skill + Genome Integration into the Autonomous Learning Loop ».

Même sandbox namespaced RÉELLE que `test_metalearner_autonomy.py` — le
service ne fait qu'appeler `SkillRegistryService`/`GenomeStoreService`
(déjà réels, phases 14/17), jamais une seconde implémentation.
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.genome.model import GenomeVersionOrigin, GenomeVersionStatus
from apex.genome.service import GenomeStoreService
from apex.metalearner.autonomy import AutonomousLearningService, CycleState
from apex.metalearner.cycle import run_experiment_cycle
from apex.metalearner.feedback import LearningFeedbackService
from apex.policy.model import Subject
from apex.skills.model import SkillStatus
from apex.skills.service import SkillRegistryService

#: Vérifié empiriquement (intervalle de Wilson, voir apex.calibration) : la
#: borne inférieure calibrée franchit 0.7 à partir de 9 succès consécutifs
#: sur 9 observations — jamais avant.
_SUFFICIENT_EVIDENCE_RUNS = 9
_INSUFFICIENT_EVIDENCE_RUNS = 2


def sqlite_session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True,
    )
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def a_pool(**overrides) -> ComputePool:
    defaults = dict(
        pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
        capabilities=(Capability.SANDBOX,),
    )
    defaults.update(overrides)
    return ComputePool(**defaults)


def promote_a_genome(factory, content=None) -> str:
    genome = GenomeStoreService(factory)
    genome.propose(content=content or {"a": 1, "b": 2.0}, created_by=Subject.HUMAN)
    proposed = genome.list_all()[0]
    genome.promote(proposed.id, requested_by=Subject.HUMAN)
    return proposed.id


def accumulate_passing_runs(factory, runtime, pool, *, specialty: str, count: int, ref_prefix: str):
    """Exécute `count` cycles RÉELS (sandbox namespaced, jamais simulés),
    chacun avec son propre clone jetable (à usage unique — voir
    `AgentRuntime.execute_in_sandbox`), accumulant une preuve réelle et
    croissante dans `apex.weakness`/`apex.evalrunner`. Retourne le DERNIER
    `ExperimentCycleResult`, celui qu'un cycle appelant traiterait."""
    result = None
    for i in range(count):
        clone = runtime.spawn(Subject.LEARNING, pool=pool, genome_ref="sha256:x", spawned_by=Subject.CORE)
        result = run_experiment_cycle(
            session_factory=factory, agent_runtime=runtime, clone_id=clone.clone_id,
            specialty=specialty, current_level=1, requested_by=Subject.HUMAN,
            experiment_ref=f"{ref_prefix}-{i}",
        )
    return result


def _wait_terminal(service: AutonomousLearningService, *, timeout_s: float = 10.0):
    deadline = time.monotonic() + timeout_s
    status = service.status()
    while status.state in (CycleState.RUNNING, CycleState.CANCEL_REQUESTED) and time.monotonic() < deadline:
        time.sleep(0.05)
        status = service.status()
    return status


class TestSkillFeedback(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.pool = a_pool()
        self.feedback = LearningFeedbackService(self.factory)

    def test_successful_candidate_creation_from_real_evidence(self) -> None:
        result = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_SUFFICIENT_EVIDENCE_RUNS, ref_prefix="skill-success",
        )
        outcome = self.feedback.process_cycle_result(result, experiment_ref="skill-success-final")

        self.assertIsNotNone(outcome.skill_candidate_id)
        skill = SkillRegistryService(self.factory).get(outcome.skill_candidate_id)
        self.assertIsNotNone(skill)
        self.assertEqual(skill.status, SkillStatus.PROPOSED)  # jamais promu par ce chemin
        self.assertEqual(skill.specialty, "research")
        self.assertEqual(skill.created_by, Subject.CORE.value)

    def test_rejected_invalid_candidate_when_evidence_insufficient(self) -> None:
        result = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_INSUFFICIENT_EVIDENCE_RUNS, ref_prefix="skill-insufficient",
        )
        outcome = self.feedback.process_cycle_result(result, experiment_ref="skill-insufficient-final")

        self.assertIsNone(outcome.skill_candidate_id)
        self.assertIn("insuffisante", outcome.skill_candidate_reason)
        self.assertEqual(SkillRegistryService(self.factory).list_all(), [])

    def test_provenance_is_traceable_to_experiment_ref_and_real_evidence(self) -> None:
        result = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_SUFFICIENT_EVIDENCE_RUNS, ref_prefix="skill-provenance",
        )
        outcome = self.feedback.process_cycle_result(result, experiment_ref="skill-provenance-final")

        skill = SkillRegistryService(self.factory).get(outcome.skill_candidate_id)
        self.assertIn("skill-provenance-final", skill.description)
        self.assertGreater(len(skill.evidence_run_ids), 0)  # jamais une preuve inventée

        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self.factory) as session:
            events = [
                e for e in EventRepository(session).recent(limit=200)
                if e.logger == "apex.metalearner.feedback" and e.msg == "learning.skill_feedback_proposed"
            ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].context["experiment_ref"], "skill-provenance-final")
        self.assertEqual(events[0].context["skill_id"], skill.id)

    def test_duplicate_pending_skill_blocks_a_new_proposal(self) -> None:
        """Sécurité contre l'accumulation non contrôlée : tant qu'une
        compétence PROPOSED existe pour la spécialité, aucune nouvelle
        n'est créée, quel que soit le nombre de cycles qualifiants
        supplémentaires."""
        first = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_SUFFICIENT_EVIDENCE_RUNS, ref_prefix="skill-dup-a",
        )
        outcome1 = self.feedback.process_cycle_result(first, experiment_ref="skill-dup-a-final")
        self.assertIsNotNone(outcome1.skill_candidate_id)

        second = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research", count=1, ref_prefix="skill-dup-b",
        )
        outcome2 = self.feedback.process_cycle_result(second, experiment_ref="skill-dup-b-final")

        self.assertIsNone(outcome2.skill_candidate_id)
        self.assertIn("déjà PROPOSED", outcome2.skill_candidate_reason)
        self.assertEqual(len(SkillRegistryService(self.factory).list_all()), 1)

    def test_duplicate_experiment_ref_processing_is_idempotent(self) -> None:
        result = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_SUFFICIENT_EVIDENCE_RUNS, ref_prefix="skill-idem",
        )
        o1 = self.feedback.process_cycle_result(result, experiment_ref="skill-idem-final")
        o2 = self.feedback.process_cycle_result(result, experiment_ref="skill-idem-final")
        o3 = self.feedback.process_cycle_result(result, experiment_ref="skill-idem-final")

        self.assertEqual(o1.skill_candidate_id, o2.skill_candidate_id)
        self.assertEqual(o2.skill_candidate_id, o3.skill_candidate_id)
        self.assertEqual(len(SkillRegistryService(self.factory).list_all()), 1)

    def test_skill_failure_is_isolated_and_never_blocks_genome_processing(self) -> None:
        result = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_SUFFICIENT_EVIDENCE_RUNS, ref_prefix="skill-fail",
        )
        promote_a_genome(self.factory)

        with patch(
            "apex.skills.service.SkillRegistryService.propose_from_evidence",
            side_effect=RuntimeError("boom"),
        ):
            outcome = self.feedback.process_cycle_result(result, experiment_ref="skill-fail-final")

        self.assertIsNone(outcome.skill_candidate_id)
        self.assertIn("boom", outcome.skill_candidate_reason)
        self.assertIsNotNone(outcome.genome_candidate_id)  # jamais bloqué par l'échec Skill


class TestGenomeFeedback(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.pool = a_pool()
        self.feedback = LearningFeedbackService(self.factory)

    def test_successful_integration_creates_a_proposed_never_promoted_candidate(self) -> None:
        promote_a_genome(self.factory)
        result = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_SUFFICIENT_EVIDENCE_RUNS, ref_prefix="genome-success",
        )
        outcome = self.feedback.process_cycle_result(result, experiment_ref="genome-success-final")

        self.assertIsNotNone(outcome.genome_candidate_id)
        genome = GenomeStoreService(self.factory)
        version = genome.get(outcome.genome_candidate_id)
        self.assertEqual(version.status, GenomeVersionStatus.PROPOSED)  # jamais promu
        self.assertEqual(version.origin, GenomeVersionOrigin.MUTATION)
        self.assertEqual(version.created_by, Subject.CORE.value)

    def test_lineage_and_provenance_reference_the_real_parent(self) -> None:
        current_id = promote_a_genome(self.factory, content={"rate": 1.0, "flag": True})
        result = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_SUFFICIENT_EVIDENCE_RUNS, ref_prefix="genome-lineage",
        )
        outcome = self.feedback.process_cycle_result(result, experiment_ref="genome-lineage-final")

        genome = GenomeStoreService(self.factory)
        version = genome.get(outcome.genome_candidate_id)
        self.assertEqual(version.parent_version_id, current_id)

        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self.factory) as session:
            events = [
                e for e in EventRepository(session).recent(limit=200)
                if e.logger == "apex.metalearner.feedback" and e.msg == "learning.genome_feedback_proposed"
            ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].context["experiment_ref"], "genome-lineage-final")
        self.assertEqual(events[0].context["parent_version_id"], current_id)

    def test_no_promoted_genome_yields_no_candidate_never_a_crash(self) -> None:
        result = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_SUFFICIENT_EVIDENCE_RUNS, ref_prefix="genome-none",
        )
        outcome = self.feedback.process_cycle_result(result, experiment_ref="genome-none-final")

        self.assertIsNone(outcome.genome_candidate_id)
        self.assertIn("évoluer", outcome.genome_candidate_reason)

    def test_duplicate_pending_candidate_blocks_a_new_proposal_population_safety(self) -> None:
        promote_a_genome(self.factory)
        first = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_SUFFICIENT_EVIDENCE_RUNS, ref_prefix="genome-dup-a",
        )
        outcome1 = self.feedback.process_cycle_result(first, experiment_ref="genome-dup-a-final")
        self.assertIsNotNone(outcome1.genome_candidate_id)

        second = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research", count=1, ref_prefix="genome-dup-b",
        )
        outcome2 = self.feedback.process_cycle_result(second, experiment_ref="genome-dup-b-final")

        self.assertIsNone(outcome2.genome_candidate_id)
        self.assertIn("déjà PROPOSED", outcome2.genome_candidate_reason)
        # exactement 2 versions : celle PROMOTED d'origine + le seul candidat PROPOSED
        self.assertEqual(len(GenomeStoreService(self.factory).list_all()), 2)

    def test_duplicate_experiment_ref_processing_is_idempotent(self) -> None:
        promote_a_genome(self.factory)
        result = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_SUFFICIENT_EVIDENCE_RUNS, ref_prefix="genome-idem",
        )
        o1 = self.feedback.process_cycle_result(result, experiment_ref="genome-idem-final")
        o2 = self.feedback.process_cycle_result(result, experiment_ref="genome-idem-final")

        self.assertEqual(o1.genome_candidate_id, o2.genome_candidate_id)
        self.assertEqual(len(GenomeStoreService(self.factory).list_all()), 2)

    def test_genome_failure_is_isolated_and_never_blocks_skill_processing(self) -> None:
        promote_a_genome(self.factory)
        result = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_SUFFICIENT_EVIDENCE_RUNS, ref_prefix="genome-fail",
        )
        with patch("apex.genome.service.GenomeStoreService.propose", side_effect=RuntimeError("kaboom")):
            outcome = self.feedback.process_cycle_result(result, experiment_ref="genome-fail-final")

        self.assertIsNone(outcome.genome_candidate_id)
        self.assertIn("kaboom", outcome.genome_candidate_reason)
        self.assertIsNotNone(outcome.skill_candidate_id)  # jamais bloqué par l'échec Genome


class TestLearningLoopIntegration(unittest.TestCase):
    """Bout en bout via `AutonomousLearningService` — jamais un appel direct
    à `LearningFeedbackService` seul."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.pool = a_pool()

        class FakeConfig:
            pools = {"local": self.pool}

        self.config = FakeConfig()
        self.service = AutonomousLearningService(self.factory, agent_runtime=self.runtime, config=self.config)

    def _run_cycle_and_wait(self, *, current_level: int = 1):
        self.service.start_cycle(specialty="research", current_level=current_level)
        return _wait_terminal(self.service)

    def test_successful_experiment_feeds_back_into_status_and_history(self) -> None:
        promote_a_genome(self.factory)
        # accumule la preuve, cycle par cycle via le service — jamais un
        # accès direct à run_experiment_cycle dans ce test-là.
        final = None
        for _ in range(_SUFFICIENT_EVIDENCE_RUNS):
            final = self._run_cycle_and_wait()

        self.assertEqual(final.state, CycleState.COMPLETED)
        self.assertIsNotNone(final.skill_candidate_id)
        self.assertIsNotNone(final.genome_candidate_id)

        history = self.service.history(limit=20)
        self.assertEqual(history[0].skill_candidate_id, final.skill_candidate_id)
        self.assertEqual(history[0].genome_candidate_id, final.genome_candidate_id)

    def test_failed_experiment_reaches_terminal_state_without_any_feedback_attempt(self) -> None:
        status = self.service.start_cycle(
            specialty="research", current_level=1, clone_id="does-not-exist",
        )
        self.assertEqual(status.state, CycleState.RUNNING)
        final = _wait_terminal(self.service)

        self.assertEqual(final.state, CycleState.FAILED)
        self.assertIsNone(final.skill_candidate_id)
        self.assertIsNone(final.genome_candidate_id)

    def test_skill_processing_failure_never_turns_a_successful_cycle_into_failed(self) -> None:
        promote_a_genome(self.factory)
        for _ in range(_SUFFICIENT_EVIDENCE_RUNS - 1):
            self._run_cycle_and_wait()

        with patch(
            "apex.skills.service.SkillRegistryService.propose_from_evidence",
            side_effect=RuntimeError("boom"),
        ):
            final = self._run_cycle_and_wait()

        self.assertEqual(final.state, CycleState.COMPLETED)
        self.assertIsNone(final.skill_candidate_id)

    def test_genome_processing_failure_never_turns_a_successful_cycle_into_failed(self) -> None:
        promote_a_genome(self.factory)
        for _ in range(_SUFFICIENT_EVIDENCE_RUNS - 1):
            self._run_cycle_and_wait()

        with patch("apex.genome.service.GenomeStoreService.propose", side_effect=RuntimeError("kaboom")):
            final = self._run_cycle_and_wait()

        self.assertEqual(final.state, CycleState.COMPLETED)
        self.assertIsNone(final.genome_candidate_id)

    def test_repeated_successful_cycles_never_accumulate_more_than_one_pending_candidate_each(self) -> None:
        promote_a_genome(self.factory)
        for _ in range(_SUFFICIENT_EVIDENCE_RUNS + 2):
            self._run_cycle_and_wait()

        self.assertEqual(len(SkillRegistryService(self.factory).list_all()), 1)
        self.assertEqual(len(GenomeStoreService(self.factory).list_all()), 2)  # promoted + 1 candidat


class TestSecurity(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.pool = a_pool()
        self.feedback = LearningFeedbackService(self.factory)

    def test_skill_candidate_is_always_proposed_as_core_never_a_caller_supplied_identity(self) -> None:
        result = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_SUFFICIENT_EVIDENCE_RUNS, ref_prefix="sec-skill",
        )
        with patch(
            "apex.skills.service.SkillRegistryService.propose_from_evidence",
            wraps=SkillRegistryService(self.factory).propose_from_evidence,
        ) as spy:
            self.feedback.process_cycle_result(result, experiment_ref="sec-skill-final")
        self.assertEqual(spy.call_args.kwargs["created_by"], Subject.CORE)

    def test_genome_candidate_is_always_proposed_as_core_never_a_caller_supplied_identity(self) -> None:
        promote_a_genome(self.factory)
        result = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_SUFFICIENT_EVIDENCE_RUNS, ref_prefix="sec-genome",
        )
        with patch(
            "apex.genome.service.GenomeStoreService.propose",
            wraps=GenomeStoreService(self.factory).propose,
        ) as spy:
            self.feedback.process_cycle_result(result, experiment_ref="sec-genome-final")
        self.assertEqual(spy.call_args.kwargs["created_by"], Subject.CORE)

    def test_process_cycle_result_has_no_identity_parameter_at_all(self) -> None:
        """Défense en profondeur : la signature elle-même n'accepte aucun
        `requested_by`/`subject`/`actor` — vérifié directement, jamais
        supposé."""
        import inspect

        sig = inspect.signature(LearningFeedbackService.process_cycle_result)
        for forbidden in ("requested_by", "subject", "actor", "created_by"):
            self.assertNotIn(forbidden, sig.parameters)

    def test_feedback_service_never_promotes_or_rejects_anything(self) -> None:
        """`PROMOTE_SKILL`/`PROMOTE_GENOME_VERSION`/`REJECT_*` restent
        HUMAIN seul — vérifié en observant qu'aucun des deux n'est jamais
        appelé pendant un traitement normal (patchés pour lever si
        atteints)."""
        promote_a_genome(self.factory)
        result = accumulate_passing_runs(
            self.factory, self.runtime, self.pool, specialty="research",
            count=_SUFFICIENT_EVIDENCE_RUNS, ref_prefix="sec-no-promo",
        )
        with (
            patch("apex.skills.service.SkillRegistryService.promote", side_effect=AssertionError("never")),
            patch("apex.skills.service.SkillRegistryService.reject", side_effect=AssertionError("never")),
            patch("apex.genome.service.GenomeStoreService.promote", side_effect=AssertionError("never")),
            patch("apex.genome.service.GenomeStoreService.reject", side_effect=AssertionError("never")),
        ):
            outcome = self.feedback.process_cycle_result(result, experiment_ref="sec-no-promo-final")
        self.assertIsNotNone(outcome.skill_candidate_id)
        self.assertIsNotNone(outcome.genome_candidate_id)


if __name__ == "__main__":
    unittest.main()

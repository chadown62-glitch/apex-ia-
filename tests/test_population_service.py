"""Tests de `apex.metalearner.population_service.PopulationService` — bookkeeping pur
(aucun spawn, aucune proposition de Genome, voir sa docstring). SQLite en
mémoire, même patron `StaticPool`/`check_same_thread=False` qu'établi par
`test_metalearner_autonomy.py`/`test_metalearner_feedback.py` — aucun test
d'intégration réel n'est nécessaire ici (pas de thread de fond)."""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import PopulationCapExceeded
from apex.metalearner.population_fitness import FitnessResult
from apex.metalearner.population_model import MemberStatus
from apex.metalearner.population_service import PopulationService


def sqlite_session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True,
    )
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def _fitness(value: float = 1.0) -> FitnessResult:
    return FitnessResult(value=value, components={"quality": 1.0})


class TestAddMember(unittest.TestCase):
    def setUp(self) -> None:
        self.service = PopulationService(sqlite_session_factory())

    def test_creates_a_new_member_as_experimental(self) -> None:
        member = self.service.add_member(
            specialty="research", generation=0, clone_id="clone-1", genome_version_id="genome-1",
            parent_member_id=None, created_by="core", experiment_ref="ref-1", max_population_size=8,
        )
        self.assertEqual(member.status, MemberStatus.EXPERIMENTAL)
        self.assertEqual(member.specialty, "research")
        self.assertEqual(member.generation, 0)
        self.assertIsNone(member.parent_member_id)

    def test_child_carries_its_parent(self) -> None:
        parent = self.service.add_member(
            specialty="research", generation=0, clone_id="clone-1", genome_version_id="genome-1",
            parent_member_id=None, created_by="core", experiment_ref="ref-parent", max_population_size=8,
        )
        child = self.service.add_member(
            specialty="research", generation=1, clone_id="clone-2", genome_version_id="genome-2",
            parent_member_id=parent.id, created_by="core", experiment_ref="ref-child", max_population_size=8,
        )
        self.assertEqual(child.parent_member_id, parent.id)
        self.assertEqual(child.generation, 1)


class TestIdempotency(unittest.TestCase):
    def setUp(self) -> None:
        self.service = PopulationService(sqlite_session_factory())

    def test_replaying_the_same_experiment_ref_returns_the_same_member(self) -> None:
        first = self.service.add_member(
            specialty="research", generation=0, clone_id="clone-1", genome_version_id="genome-1",
            parent_member_id=None, created_by="core", experiment_ref="dup-ref", max_population_size=8,
        )
        second = self.service.add_member(
            specialty="research", generation=0, clone_id="clone-DIFFERENT", genome_version_id="genome-DIFFERENT",
            parent_member_id=None, created_by="core", experiment_ref="dup-ref", max_population_size=8,
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.clone_id, "clone-1")  # jamais écrasé par le second appel
        self.assertEqual(len(self.service.list_by_specialty("research")), 1)

    def test_replay_never_counts_against_the_population_cap(self) -> None:
        member = self.service.add_member(
            specialty="research", generation=0, clone_id="clone-1", genome_version_id="genome-1",
            parent_member_id=None, created_by="core", experiment_ref="ref-1", max_population_size=1,
        )
        # La population est déjà pleine (max=1) — le même experiment_ref doit
        # quand même repasser, jamais bloqué par le plafond qu'il a lui-même rempli.
        replay = self.service.add_member(
            specialty="research", generation=0, clone_id="clone-1", genome_version_id="genome-1",
            parent_member_id=None, created_by="core", experiment_ref="ref-1", max_population_size=1,
        )
        self.assertEqual(member.id, replay.id)


class TestPopulationCap(unittest.TestCase):
    def setUp(self) -> None:
        self.service = PopulationService(sqlite_session_factory())

    def test_a_genuinely_new_member_beyond_the_cap_is_rejected(self) -> None:
        self.service.add_member(
            specialty="research", generation=0, clone_id="clone-1", genome_version_id="genome-1",
            parent_member_id=None, created_by="core", experiment_ref="ref-1", max_population_size=1,
        )
        with self.assertRaises(PopulationCapExceeded):
            self.service.add_member(
                specialty="research", generation=0, clone_id="clone-2", genome_version_id="genome-2",
                parent_member_id=None, created_by="core", experiment_ref="ref-2", max_population_size=1,
            )

    def test_retiring_a_member_frees_a_slot(self) -> None:
        first = self.service.add_member(
            specialty="research", generation=0, clone_id="clone-1", genome_version_id="genome-1",
            parent_member_id=None, created_by="core", experiment_ref="ref-1", max_population_size=1,
        )
        self.service.set_status(first.id, MemberStatus.RETIRED)
        second = self.service.add_member(
            specialty="research", generation=1, clone_id="clone-2", genome_version_id="genome-2",
            parent_member_id=None, created_by="core", experiment_ref="ref-2", max_population_size=1,
        )
        self.assertIsNotNone(second)

    def test_cap_is_scoped_per_specialty(self) -> None:
        self.service.add_member(
            specialty="research", generation=0, clone_id="clone-1", genome_version_id="genome-1",
            parent_member_id=None, created_by="core", experiment_ref="ref-1", max_population_size=1,
        )
        other = self.service.add_member(
            specialty="coding", generation=0, clone_id="clone-2", genome_version_id="genome-2",
            parent_member_id=None, created_by="core", experiment_ref="ref-2", max_population_size=1,
        )
        self.assertIsNotNone(other)


class TestStatusTransitionsAndSelection(unittest.TestCase):
    def setUp(self) -> None:
        self.service = PopulationService(sqlite_session_factory())
        self.member = self.service.add_member(
            specialty="research", generation=0, clone_id="clone-1", genome_version_id="genome-1",
            parent_member_id=None, created_by="core", experiment_ref="ref-1", max_population_size=8,
        )
        self.service.record_fitness(self.member.id, _fitness(1.5))

    def test_elite_status_is_reflected_in_list_elite(self) -> None:
        self.service.set_status(self.member.id, MemberStatus.ELITE)
        elite = self.service.list_elite("research")
        self.assertEqual([m.id for m in elite], [self.member.id])

    def test_champion_status_is_reflected_in_get_champion(self) -> None:
        self.service.set_status(self.member.id, MemberStatus.CHAMPION)
        champion = self.service.get_champion("research")
        self.assertIsNotNone(champion)
        self.assertEqual(champion.id, self.member.id)

    def test_no_champion_before_any_promotion(self) -> None:
        self.assertIsNone(self.service.get_champion("research"))

    def test_retired_is_terminal_never_leaves_that_state(self) -> None:
        self.service.set_status(self.member.id, MemberStatus.RETIRED)
        updated = self.service.set_status(self.member.id, MemberStatus.ELITE)
        self.assertEqual(updated.status, MemberStatus.RETIRED)  # jamais réactivé

    def test_retired_transition_is_never_an_error_even_when_redundant(self) -> None:
        self.service.set_status(self.member.id, MemberStatus.RETIRED)
        # Un second appel redondant reste un no-op silencieux, jamais une exception.
        updated = self.service.set_status(self.member.id, MemberStatus.RETIRED)
        self.assertEqual(updated.status, MemberStatus.RETIRED)


class TestFitnessRecording(unittest.TestCase):
    def setUp(self) -> None:
        self.service = PopulationService(sqlite_session_factory())
        self.member = self.service.add_member(
            specialty="research", generation=0, clone_id="clone-1", genome_version_id="genome-1",
            parent_member_id=None, created_by="core", experiment_ref="ref-1", max_population_size=8,
        )

    def test_fitness_and_candidate_ids_are_persisted(self) -> None:
        updated = self.service.record_fitness(
            self.member.id, _fitness(0.75), skill_candidate_id="skill-1", genome_candidate_id="genome-cand-1",
        )
        self.assertEqual(updated.fitness, 0.75)
        self.assertEqual(updated.fitness_components, {"quality": 1.0})
        self.assertEqual(updated.skill_candidate_id, "skill-1")
        self.assertEqual(updated.genome_candidate_id, "genome-cand-1")

    def test_unknown_member_raises_lookup_error(self) -> None:
        with self.assertRaises(LookupError):
            self.service.record_fitness("does-not-exist", _fitness())


class TestLineage(unittest.TestCase):
    def setUp(self) -> None:
        self.service = PopulationService(sqlite_session_factory())

    def test_lineage_walks_from_child_to_root(self) -> None:
        grandparent = self.service.add_member(
            specialty="research", generation=0, clone_id="c0", genome_version_id="g0",
            parent_member_id=None, created_by="core", experiment_ref="ref-0", max_population_size=8,
        )
        parent = self.service.add_member(
            specialty="research", generation=1, clone_id="c1", genome_version_id="g1",
            parent_member_id=grandparent.id, created_by="core", experiment_ref="ref-1", max_population_size=8,
        )
        child = self.service.add_member(
            specialty="research", generation=2, clone_id="c2", genome_version_id="g2",
            parent_member_id=parent.id, created_by="core", experiment_ref="ref-2", max_population_size=8,
        )
        lineage = self.service.get_lineage(child.id)
        self.assertEqual([m.id for m in lineage], [child.id, parent.id, grandparent.id])

    def test_lineage_of_a_root_member_is_itself_alone(self) -> None:
        root = self.service.add_member(
            specialty="research", generation=0, clone_id="c0", genome_version_id="g0",
            parent_member_id=None, created_by="core", experiment_ref="ref-0", max_population_size=8,
        )
        self.assertEqual([m.id for m in self.service.get_lineage(root.id)], [root.id])


class TestLatestGeneration(unittest.TestCase):
    def setUp(self) -> None:
        self.service = PopulationService(sqlite_session_factory())

    def test_minus_one_before_any_member(self) -> None:
        self.assertEqual(self.service.latest_generation("research"), -1)

    def test_reflects_the_highest_generation_created(self) -> None:
        self.service.add_member(
            specialty="research", generation=0, clone_id="c0", genome_version_id="g0",
            parent_member_id=None, created_by="core", experiment_ref="ref-0", max_population_size=8,
        )
        self.service.add_member(
            specialty="research", generation=3, clone_id="c1", genome_version_id="g1",
            parent_member_id=None, created_by="core", experiment_ref="ref-1", max_population_size=8,
        )
        self.assertEqual(self.service.latest_generation("research"), 3)


if __name__ == "__main__":
    unittest.main()

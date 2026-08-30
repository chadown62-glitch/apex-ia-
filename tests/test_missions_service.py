"""Tests de MissionService — cycle de vie complet, SQLite en mémoire.

Même patron que tests/test_agents_runtime.py : ce qui est vérifié est la
machine à états, l'application de PolicyEngine (aucun clone ne peut créer,
valider, rejeter ou lancer sa propre mission), la persistance et la
journalisation (events/audit_log), et le rejet honnête des cas invalides
(spécialité inconnue/PLANNED, agent inexistant/incompatible, mission
inexistante, transitions interdites, double décision).
"""

from __future__ import annotations

import unittest
import unittest.mock

from sqlalchemy import create_engine

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory, session_scope
from apex.db.models import Base
from apex.db.repositories import EventRepository
from apex.errors import PolicyDenied
from apex.missions.dependencies import DependencyCycle
from apex.missions.model import MissionPriority
from apex.missions.service import MissionService
from apex.missions.states import InvalidMissionTransition, MissionState
from apex.policy.model import Subject


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def a_pool(**overrides) -> ComputePool:
    defaults = dict(
        pool_id="local-gpu", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
        capabilities=(Capability.CPU_EVAL,),
    )
    defaults.update(overrides)
    return ComputePool(**defaults)


def spawn_research_clone(factory):
    runtime = AgentRuntime(factory)
    return runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:x", spawned_by=Subject.CORE)


class TestCreate(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = MissionService(self.factory)

    def test_human_can_create_a_mission_which_ends_up_queued(self) -> None:
        mission = self.service.create(
            title="Explorer X", description="d", specialty="research",
            objective="trouver une hypothèse", created_by=Subject.HUMAN,
        )
        self.assertEqual(mission.state, MissionState.QUEUED)

    def test_create_persists_the_mission(self) -> None:
        mission = self.service.create(
            title="t", description="d", specialty="research", objective="o", created_by=Subject.HUMAN,
        )
        fetched = self.service.get(mission.id)
        assert fetched is not None
        self.assertEqual(fetched.title, "t")
        self.assertEqual(fetched.state, MissionState.QUEUED)

    def test_create_writes_created_and_queued_events(self) -> None:
        mission = self.service.create(
            title="t", description="d", specialty="research", objective="o", created_by=Subject.HUMAN,
        )
        with session_scope(self.factory) as session:
            events = EventRepository(session).recent()
        kinds = [e.context.get("kind") for e in events if e.context.get("mission_id") == mission.id]
        self.assertIn("mission.created", kinds)
        self.assertIn("mission.queued", kinds)

    def test_a_clone_cannot_create_a_mission(self) -> None:
        """Agent non autorisé : aucun type de clone n'a CREATE_MISSION."""
        with self.assertRaises(PolicyDenied):
            self.service.create(
                title="t", description="d", specialty="research", objective="o",
                created_by=Subject.RESEARCH,
            )

    def test_core_cannot_create_a_mission_either(self) -> None:
        """Seul HUMAN crée une mission — CORE ne fait que la lancer/l'annuler."""
        with self.assertRaises(PolicyDenied):
            self.service.create(
                title="t", description="d", specialty="research", objective="o",
                created_by=Subject.CORE,
            )

    def test_empty_title_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create(title="   ", description="d", specialty="research", objective="o", created_by=Subject.HUMAN)

    def test_empty_objective_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create(title="t", description="d", specialty="research", objective="  ", created_by=Subject.HUMAN)

    def test_unknown_specialty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create(title="t", description="d", specialty="does-not-exist", objective="o", created_by=Subject.HUMAN)

    def test_planned_specialty_rejected(self) -> None:
        """Spécialité PLANNED : aucune mission ne peut lui être assignée."""
        with self.assertRaises(ValueError) as ctx:
            self.service.create(title="t", description="d", specialty="code-intelligence", objective="o", created_by=Subject.HUMAN)
        self.assertIn("PLANNED", str(ctx.exception))

    def test_nonexistent_assigned_agent_rejected(self) -> None:
        """Agent inexistant."""
        with self.assertRaises(ValueError) as ctx:
            self.service.create(
                title="t", description="d", specialty="research", objective="o",
                created_by=Subject.HUMAN, assigned_agent="does-not-exist",
            )
        self.assertIn("introuvable", str(ctx.exception))

    def test_incompatible_agent_type_rejected(self) -> None:
        runtime = AgentRuntime(self.factory)
        developer = runtime.spawn(Subject.DEVELOPER, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)
        with self.assertRaises(ValueError) as ctx:
            self.service.create(
                title="t", description="d", specialty="research", objective="o",
                created_by=Subject.HUMAN, assigned_agent=developer.clone_id,
            )
        self.assertIn("incompatible", str(ctx.exception))

    def test_compatible_agent_assignment_succeeds(self) -> None:
        clone = spawn_research_clone(self.factory)
        mission = self.service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id, priority=MissionPriority.HIGH,
        )
        self.assertEqual(mission.assigned_agent, clone.clone_id)
        self.assertEqual(mission.priority, MissionPriority.HIGH)


class TestDependsOn(unittest.TestCase):
    """Étape 8 — `depends_on` : existence vérifiée transactionnellement,
    absence de cycle garantie par construction (immuable après création) et
    revérifiée explicitement (voir tests/test_missions_dependencies.py pour
    l'algorithme pur)."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = MissionService(self.factory)

    def test_depends_on_defaults_to_empty(self) -> None:
        mission = self.service.create(
            title="t", description="d", specialty="research", objective="o", created_by=Subject.HUMAN,
        )
        self.assertEqual(mission.depends_on, ())

    def test_depends_on_an_existing_mission_is_accepted_and_persisted(self) -> None:
        upstream = self.service.create(
            title="A", description="d", specialty="research", objective="o", created_by=Subject.HUMAN,
        )
        downstream = self.service.create(
            title="B", description="d", specialty="research", objective="o", created_by=Subject.HUMAN,
            depends_on=(upstream.id,),
        )
        self.assertEqual(downstream.depends_on, (upstream.id,))
        fetched = self.service.get(downstream.id)
        assert fetched is not None
        self.assertEqual(fetched.depends_on, (upstream.id,))

    def test_depending_on_a_nonexistent_mission_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.service.create(
                title="B", description="d", specialty="research", objective="o", created_by=Subject.HUMAN,
                depends_on=("does-not-exist",),
            )
        self.assertIn("introuvable", str(ctx.exception))

    def test_a_manufactured_cycle_in_the_persisted_graph_is_rejected(self) -> None:
        """`depends_on` est immuable après création (aucune méthode ne le
        mute) : un cycle est donc impossible par construction en usage
        normal. Ce test vérifie que la seconde ligne de défense explicite
        (apex.missions.dependencies.detect_cycle) est bien câblée dans
        MissionService.create — pas seulement testée isolément."""
        a = self.service.create(
            title="A", description="d", specialty="research", objective="o", created_by=Subject.HUMAN,
        )
        b = self.service.create(
            title="B", description="d", specialty="research", objective="o", created_by=Subject.HUMAN,
            depends_on=(a.id,),
        )
        # On force artificiellement le graphe persisté en cycle (A -> B) en
        # écrivant directement la colonne — un contournement délibéré de
        # l'immuabilité normale (aucune méthode publique ne permet ça),
        # uniquement pour prouver que detect_cycle() est réellement invoqué
        # à la création suivante, pas seulement testé isolément.
        from apex.db.engine import session_scope
        from apex.db.models import MissionRow

        with session_scope(self.factory) as session:
            row = session.get(MissionRow, a.id)
            assert row is not None
            row.depends_on = [b.id]

        with self.assertRaises(DependencyCycle):
            self.service.create(
                title="C", description="d", specialty="research", objective="o", created_by=Subject.HUMAN,
                depends_on=(a.id,),
            )


class TestCancel(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = MissionService(self.factory)
        self.mission = self.service.create(
            title="t", description="d", specialty="research", objective="o", created_by=Subject.HUMAN,
        )

    def test_human_can_cancel_a_queued_mission(self) -> None:
        cancelled = self.service.cancel(self.mission.id, requested_by=Subject.HUMAN)
        self.assertEqual(cancelled.state, MissionState.CANCELLED)
        self.assertIsNotNone(cancelled.completed_at)

    def test_core_can_cancel_a_mission(self) -> None:
        cancelled = self.service.cancel(self.mission.id, requested_by=Subject.CORE)
        self.assertEqual(cancelled.state, MissionState.CANCELLED)

    def test_a_clone_cannot_cancel_a_mission(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.cancel(self.mission.id, requested_by=Subject.DEVELOPER)

    def test_cancel_nonexistent_mission_raises(self) -> None:
        """Mission inexistante."""
        with self.assertRaises(LookupError):
            self.service.cancel("does-not-exist", requested_by=Subject.HUMAN)

    def test_cancel_already_terminated_mission_raises(self) -> None:
        """Mission déjà terminée : la machine à états refuse, pas un raccourci silencieux."""
        self.service.cancel(self.mission.id, requested_by=Subject.HUMAN)
        with self.assertRaises(InvalidMissionTransition):
            self.service.cancel(self.mission.id, requested_by=Subject.HUMAN)


class TestSystemTransitions(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = MissionService(self.factory)
        self.mission = self.service.create(
            title="t", description="d", specialty="research", objective="o", created_by=Subject.HUMAN,
        )

    def test_mark_running_requires_queued_and_moves_forward(self) -> None:
        running = self.service.mark_running(self.mission.id)
        self.assertEqual(running.state, MissionState.RUNNING)
        self.assertIsNotNone(running.started_at)

    def test_double_launch_is_rejected(self) -> None:
        """Double lancement : lancer deux fois la même mission doit échouer."""
        self.service.mark_running(self.mission.id)
        with self.assertRaises(InvalidMissionTransition):
            self.service.mark_running(self.mission.id)

    def test_only_core_can_launch_a_mission(self) -> None:
        """Ni HUMAN ni un clone ne lance directement — CORE seul (orchestration)."""
        with self.assertRaises(PolicyDenied):
            self.service.mark_running(self.mission.id, requested_by=Subject.HUMAN)
        with self.assertRaises(PolicyDenied):
            self.service.mark_running(self.mission.id, requested_by=Subject.RESEARCH)

    def test_mark_review_requires_running(self) -> None:
        with self.assertRaises(InvalidMissionTransition):
            self.service.mark_review(self.mission.id, result="x")

    def test_mark_failed_requires_running(self) -> None:
        with self.assertRaises(InvalidMissionTransition):
            self.service.mark_failed(self.mission.id, error="x")

    def test_full_run_to_review(self) -> None:
        self.service.mark_running(self.mission.id)
        reviewed = self.service.mark_review(self.mission.id, result="hypothèse trouvée")
        self.assertEqual(reviewed.state, MissionState.REVIEW)
        self.assertEqual(reviewed.result, "hypothèse trouvée")

    def test_running_can_fail_directly(self) -> None:
        self.service.mark_running(self.mission.id)
        failed = self.service.mark_failed(self.mission.id, error="crash")
        self.assertEqual(failed.state, MissionState.FAILED)
        self.assertEqual(failed.error, "crash")
        self.assertIsNotNone(failed.completed_at)


class TestReviewDecisions(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = MissionService(self.factory)
        self.mission = self.service.create(
            title="t", description="d", specialty="research", objective="o", created_by=Subject.HUMAN,
        )
        self.service.mark_running(self.mission.id)
        self.service.mark_review(self.mission.id, result="résultat")

    def test_human_can_validate(self) -> None:
        validated = self.service.validate(self.mission.id, requested_by=Subject.HUMAN)
        self.assertEqual(validated.state, MissionState.COMPLETED)
        self.assertEqual(validated.validation_status.value, "validated")
        self.assertIsNotNone(validated.completed_at)

    def test_a_clone_cannot_validate_its_own_mission(self) -> None:
        """Un agent ne doit jamais pouvoir s'auto-valider."""
        with self.assertRaises(PolicyDenied):
            self.service.validate(self.mission.id, requested_by=Subject.RESEARCH)

    def test_core_cannot_validate_either(self) -> None:
        """Seul un HUMAN valide — même CORE (le système) ne le peut pas."""
        with self.assertRaises(PolicyDenied):
            self.service.validate(self.mission.id, requested_by=Subject.CORE)

    def test_validate_requires_review_state(self) -> None:
        other = self.service.create(title="t2", description="d", specialty="research", objective="o", created_by=Subject.HUMAN)
        with self.assertRaises(InvalidMissionTransition):
            self.service.validate(other.id, requested_by=Subject.HUMAN)  # encore QUEUED

    def test_double_validation_is_rejected(self) -> None:
        self.service.validate(self.mission.id, requested_by=Subject.HUMAN)
        with self.assertRaises(ValueError):
            self.service.validate(self.mission.id, requested_by=Subject.HUMAN)

    def test_human_can_reject_with_a_reason(self) -> None:
        rejected = self.service.reject(self.mission.id, requested_by=Subject.HUMAN, reason="hors périmètre")
        self.assertEqual(rejected.state, MissionState.FAILED)
        self.assertEqual(rejected.validation_status.value, "rejected")
        self.assertEqual(rejected.error, "hors périmètre")

    def test_a_clone_cannot_reject(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.service.reject(self.mission.id, requested_by=Subject.RESEARCH)

    def test_double_rejection_is_rejected(self) -> None:
        self.service.reject(self.mission.id, requested_by=Subject.HUMAN)
        with self.assertRaises(ValueError):
            self.service.reject(self.mission.id, requested_by=Subject.HUMAN)

    def test_validate_after_reject_is_rejected(self) -> None:
        """Une décision humaine déjà rendue ne peut pas être écrasée par l'autre décision."""
        self.service.reject(self.mission.id, requested_by=Subject.HUMAN)
        with self.assertRaises(ValueError):
            self.service.validate(self.mission.id, requested_by=Subject.HUMAN)

    def test_human_can_request_revision(self) -> None:
        revised = self.service.request_revision(self.mission.id, requested_by=Subject.HUMAN)
        self.assertEqual(revised.state, MissionState.DRAFT)
        self.assertEqual(revised.validation_status.value, "revision_requested")

    def test_validate_nonexistent_mission_raises(self) -> None:
        with self.assertRaises(LookupError):
            self.service.validate("does-not-exist", requested_by=Subject.HUMAN)


class TestDatabaseError(unittest.TestCase):
    def test_create_propagates_a_broken_session_factory(self) -> None:
        """API sans PostgreSQL / erreur DB : le service ne doit ni avaler
        l'erreur ni prétendre avoir persisté la mission."""

        def broken_factory():
            raise RuntimeError("connexion refusée")

        service = MissionService(broken_factory)
        with self.assertRaises(RuntimeError):
            service.create(title="t", description="d", specialty="research", objective="o", created_by=Subject.HUMAN)


class TestImportableWithoutSqlalchemy(unittest.TestCase):
    """Garde de non-régression, même patron que
    tests/test_agents_runtime.py::TestImportableWithoutSqlalchemy — nécessaire
    avant toute déclaration `module="apex.missions.service"` dans le registre."""

    def test_module_imports_with_sqlalchemy_hidden(self) -> None:
        import sys

        blocked = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if name == "sqlalchemy"
            or name.startswith("sqlalchemy.")
            or name.startswith("apex.missions")
            or name.startswith("apex.db.")
        }
        try:
            with unittest.mock.patch.dict(sys.modules, {"sqlalchemy": None}):
                import apex.missions.service as fresh_service

                self.assertTrue(hasattr(fresh_service, "MissionService"))
        finally:
            for name in list(sys.modules):
                if name.startswith("apex.missions") or name.startswith("apex.db."):
                    del sys.modules[name]
            sys.modules.update(blocked)


if __name__ == "__main__":
    unittest.main()

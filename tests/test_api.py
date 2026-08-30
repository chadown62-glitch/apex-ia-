"""Tests de l'API — backend du futur APEX Control Center.

Deux volets, cohérents avec le reste du dépôt depuis la Phase 2 :
- sans DB configurée : chaque endpoint dégrade proprement en "unknown",
  jamais une erreur 500 ni une fausse donnée "real" ;
- avec PostgreSQL réel joignable (`db_test_support.postgres_testable()`) :
  chemin "real" bout en bout, y compris un clone réellement spawné.
"""

from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient

from apex.api.app import create_app
from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)


class TestApiWithoutDatabase(unittest.TestCase):
    """Aucune variable POSTGRES_* définie : simule un déploiement sans DB
    configurée. Doit rester utilisable — health/gate/registry ne dépendent
    pas de la DB."""

    def setUp(self) -> None:
        self._saved = {
            k: os.environ.pop(k, None)
            for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
        }
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_health_never_fails(self) -> None:
        r = self.client.get("/api/v1/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "real")  # on a réellement mesuré, à l'instant
        self.assertIn(body["data"]["status"], ("ok", "degraded", "failed"))

    def test_registry_is_always_real(self) -> None:
        r = self.client.get("/api/v1/registry")
        body = r.json()
        self.assertEqual(body["status"], "real")
        names = {a["name"] for a in body["data"]["abstractions"]}
        self.assertIn("AgentRuntime", names)
        self.assertIn("PolicyEngine", names)

    def test_registry_reports_agentruntime_as_stub_not_implemented(self) -> None:
        """Le registre ne doit jamais paraître plus avancé qu'il ne l'est —
        vérifié à travers l'API, pas seulement en Python."""
        r = self.client.get("/api/v1/registry")
        abstractions = {a["name"]: a for a in r.json()["data"]["abstractions"]}
        self.assertEqual(abstractions["AgentRuntime"]["status"], "stub")
        self.assertEqual(abstractions["PolicyEngine"]["status"], "implemented")

    def test_gate_is_unknown_without_a_report(self) -> None:
        r = self.client.get("/api/v1/gate")
        body = r.json()
        self.assertEqual(body["status"], "unknown")
        self.assertIsNone(body["data"])
        self.assertTrue(body["detail"])

    def test_events_degrades_to_unknown_without_db(self) -> None:
        r = self.client.get("/api/v1/events")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "unknown")
        self.assertIsNone(body["data"])

    def test_audit_degrades_to_unknown_without_db(self) -> None:
        r = self.client.get("/api/v1/audit")
        self.assertEqual(r.json()["status"], "unknown")

    def test_agents_degrades_to_unknown_without_db(self) -> None:
        r = self.client.get("/api/v1/agents")
        self.assertEqual(r.json()["status"], "unknown")

    def test_missions_list_degrades_to_unknown_without_db(self) -> None:
        r = self.client.get("/api/v1/missions")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "unknown")

    def test_create_mission_degrades_to_unknown_without_db(self) -> None:
        """API sans PostgreSQL : la création dégrade proprement, jamais une
        fausse mission « real » sans base pour la persister."""
        r = self.client.post(
            "/api/v1/missions",
            json={"title": "t", "specialty": "research", "objective": "o"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "unknown")
        self.assertIsNone(body["data"])

    def test_unknown_route_is_a_clean_404(self) -> None:
        r = self.client.get("/api/v1/does-not-exist")
        self.assertEqual(r.status_code, 404)

    def test_no_route_exists_for_capabilities_still_planned(self) -> None:
        """Learning/Code/Design/Projects/... n'ont aucune route : les exposer
        reviendrait à promettre des données qui n'existent pas encore."""
        for path in ("/api/v1/learning", "/api/v1/code", "/api/v1/design", "/api/v1/genome"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestApiWithRealDatabase(unittest.TestCase):
    """Base fraîche par test (pas par classe) : plusieurs tests écrivent des
    clones dans `agents` — une base partagée entre méthodes romprait
    l'hypothèse d'un test qui vérifie une liste vide, selon l'ordre
    d'exécution alphabétique d'unittest. Coût accepté : 4 tests seulement."""

    def setUp(self) -> None:
        support.recreate_test_database()
        support.run_upgrade()
        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def test_events_and_audit_report_real_with_empty_data(self) -> None:
        for path in ("/api/v1/events", "/api/v1/audit", "/api/v1/agents"):
            with self.subTest(path=path):
                body = self.client.get(path).json()
                self.assertEqual(body["status"], "real")
                self.assertEqual(body["data"], [])

    def test_a_spawned_clone_is_visible_through_the_api(self) -> None:
        from apex.agents.runtime import AgentRuntime
        from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
        from apex.db.engine import build_engine, build_session_factory, database_url_from_config
        from apex.config.loader import load_config
        from apex.policy.model import Subject

        config = load_config()
        factory = build_session_factory(build_engine(database_url_from_config(config)))
        runtime = AgentRuntime(factory)
        pool = ComputePool(
            pool_id="local-gpu", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
            capabilities=(Capability.CPU_EVAL,),
        )
        clone = runtime.spawn(Subject.RESEARCH, pool=pool, genome_ref="sha256:api-test", spawned_by=Subject.CORE)
        runtime.run(clone, lambda: "ok")

        r_list = self.client.get("/api/v1/agents")
        body = r_list.json()
        self.assertEqual(body["status"], "real")
        clone_ids = [a["clone_id"] for a in body["data"]]
        self.assertIn(clone.clone_id, clone_ids)

        r_detail = self.client.get(f"/api/v1/agents/{clone.clone_id}")
        detail = r_detail.json()
        self.assertEqual(detail["status"], "real")
        self.assertEqual(detail["data"]["state"], "terminated")
        self.assertEqual(detail["data"]["type"], "research")
        # RAM/GPU jamais mesurées, quel que soit le clone (§12) :
        self.assertEqual(detail["data"]["ram_mb"]["status"], "unknown")
        self.assertEqual(detail["data"]["gpu"]["status"], "unknown")
        # HUMAN (viewer par défaut de l'API) peut terminer un clone :
        self.assertIn("terminate_clone", detail["data"]["available_actions"])

    def test_unknown_clone_id_returns_unknown_status_not_a_404(self) -> None:
        r = self.client.get("/api/v1/agents/does-not-exist")
        body = r.json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["status"], "unknown")
        self.assertIn("introuvable", body["detail"])

    def test_health_reports_database_ok_when_reachable(self) -> None:
        body = self.client.get("/api/v1/health").json()
        components = {c["component"]: c for c in body["data"]["components"]}
        self.assertEqual(components["database"]["status"], "ok")


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestMissionApiWithRealDatabase(unittest.TestCase):
    """Base fraîche par test — même raison que TestApiWithRealDatabase :
    plusieurs tests créent des missions et vérifient des comptes/listes.

    `reset_scheduler_service_for_tests()` est OBLIGATOIRE ici (étape 11) :
    le singleton Scheduler applicatif (apex.api.dependencies.
    get_scheduler_service) est réellement mis en cache pour tenir toute la
    vie d'un processus réel — mais cette classe recrée la base PostgreSQL
    SOUS LE MÊME NOM à chaque test, ce qu'un déploiement réel ne fait jamais.
    Sans ce reset, le singleton construit par un test garderait un pool de
    connexions SQLAlchemy pointant vers une base ensuite DROP puis CREATE
    par le test suivant — un déploiement réel n'a jamais besoin de ce
    mécanisme (voir la docstring de apex.api.dependencies)."""

    def setUp(self) -> None:
        from apex.api.dependencies import reset_scheduler_service_for_tests

        support.recreate_test_database()
        support.run_upgrade()
        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME
        reset_scheduler_service_for_tests()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        from apex.api.dependencies import reset_scheduler_service_for_tests

        reset_scheduler_service_for_tests()
        support.drop_test_database()
        support.drop_application_roles()

    def test_missions_list_is_real_and_empty_on_a_fresh_database(self) -> None:
        body = self.client.get("/api/v1/missions").json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"], [])

    def test_create_mission_through_the_api(self) -> None:
        r = self.client.post(
            "/api/v1/missions",
            json={"title": "Explorer X", "specialty": "research", "objective": "trouver une hypothèse"},
        )
        body = r.json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"]["state"], "queued")
        self.assertEqual(body["data"]["created_by"], "human")

        listed = self.client.get("/api/v1/missions").json()
        self.assertIn(body["data"]["id"], [m["id"] for m in listed["data"]])

        fetched = self.client.get(f"/api/v1/missions/{body['data']['id']}").json()
        self.assertEqual(fetched["status"], "real")
        self.assertEqual(fetched["data"]["title"], "Explorer X")

    def test_get_unknown_mission_id_returns_unknown_status_not_a_404(self) -> None:
        r = self.client.get("/api/v1/missions/does-not-exist")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "unknown")
        self.assertIn("introuvable", body["detail"])

    def test_create_mission_for_a_planned_specialty_is_refused(self) -> None:
        r = self.client.post(
            "/api/v1/missions",
            json={"title": "t", "specialty": "code-intelligence", "objective": "o"},
        )
        body = r.json()
        self.assertEqual(body["status"], "unknown")
        self.assertIn("PLANNED", body["detail"])

    def test_create_mission_with_nonexistent_agent_is_refused(self) -> None:
        r = self.client.post(
            "/api/v1/missions",
            json={"title": "t", "specialty": "research", "objective": "o", "assigned_agent": "does-not-exist"},
        )
        body = r.json()
        self.assertEqual(body["status"], "unknown")
        self.assertIn("introuvable", body["detail"])

    def test_cancel_mission_through_the_api(self) -> None:
        created = self.client.post(
            "/api/v1/missions", json={"title": "t", "specialty": "research", "objective": "o"}
        ).json()["data"]
        r = self.client.post(f"/api/v1/missions/{created['id']}/cancel")
        body = r.json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"]["state"], "cancelled")

    def test_cancel_already_cancelled_mission_is_refused(self) -> None:
        created = self.client.post(
            "/api/v1/missions", json={"title": "t", "specialty": "research", "objective": "o"}
        ).json()["data"]
        self.client.post(f"/api/v1/missions/{created['id']}/cancel")
        r = self.client.post(f"/api/v1/missions/{created['id']}/cancel")
        self.assertEqual(r.json()["status"], "unknown")

    def test_validate_and_reject_through_the_api(self) -> None:
        """validate/reject exigent REVIEW — atteint ici via MissionService
        directement (mark_running/mark_review), pas via l'API : aucune route
        ne fait encore ce lancement (voir apex.missions.service)."""
        from apex.config.loader import load_config
        from apex.db.engine import build_engine, build_session_factory, database_url_from_config
        from apex.missions.service import MissionService

        config = load_config()
        factory = build_session_factory(build_engine(database_url_from_config(config)))
        service = MissionService(factory)

        created = self.client.post(
            "/api/v1/missions", json={"title": "t", "specialty": "research", "objective": "o"}
        ).json()["data"]
        service.mark_running(created["id"])
        service.mark_review(created["id"], result="hypothèse trouvée")

        r = self.client.post(f"/api/v1/missions/{created['id']}/validate")
        body = r.json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"]["state"], "completed")
        self.assertEqual(body["data"]["validation_status"], "validated")

        # Double validation : refusée, jamais une deuxième "réussite" silencieuse.
        r2 = self.client.post(f"/api/v1/missions/{created['id']}/validate")
        self.assertEqual(r2.json()["status"], "unknown")

    def test_reject_with_a_reason_through_the_api(self) -> None:
        from apex.config.loader import load_config
        from apex.db.engine import build_engine, build_session_factory, database_url_from_config
        from apex.missions.service import MissionService

        config = load_config()
        factory = build_session_factory(build_engine(database_url_from_config(config)))
        service = MissionService(factory)

        created = self.client.post(
            "/api/v1/missions", json={"title": "t", "specialty": "research", "objective": "o"}
        ).json()["data"]
        service.mark_running(created["id"])
        service.mark_review(created["id"], result="résultat discutable")

        r = self.client.post(f"/api/v1/missions/{created['id']}/reject", json={"reason": "hors périmètre"})
        body = r.json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"]["state"], "failed")
        self.assertEqual(body["data"]["validation_status"], "rejected")
        self.assertEqual(body["data"]["error"], "hors périmètre")

    def _spawn_research_clone(self):
        """Un vrai clone READY, via AgentRuntime directement — la route
        `/missions` n'expose aucun endpoint de spawn (voir apex/api/routes/agents.py).
        Même façon de construire la session factory que les tests
        validate/reject ci-dessus, contre la même base que l'API (`POSTGRES_DB`
        déjà positionné sur `support.TEST_DB_NAME` par `setUp`)."""
        from apex.agents.runtime import AgentRuntime
        from apex.config.loader import load_config
        from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
        from apex.db.engine import build_engine, build_session_factory, database_url_from_config
        from apex.policy.model import Subject

        config = load_config()
        factory = build_session_factory(build_engine(database_url_from_config(config)))
        pool = ComputePool(
            pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
            capabilities=(Capability.CPU_EVAL,),
        )
        runtime = AgentRuntime(factory)
        return runtime.spawn(Subject.RESEARCH, pool=pool, genome_ref="sha256:x", spawned_by=Subject.CORE)

    def test_launch_mission_through_the_api_reaches_review_with_a_real_result(self) -> None:
        """Chaîne complète CORE -> POLICY -> AGENT RUNTIME -> SANDBOX ->
        MISSION -> RESULT, déclenchée par la route HTTP elle-même — pas en
        appelant mark_running/mark_review directement comme les tests
        validate/reject ci-dessus (eux testent l'aval, celui-ci l'amont)."""
        clone = self._spawn_research_clone()
        created = self.client.post(
            "/api/v1/missions",
            json={
                "title": "Diagnostic", "specialty": "research", "objective": "valider la chaîne",
                "assigned_agent": clone.clone_id,
            },
        ).json()["data"]
        self.assertEqual(created["state"], "queued")

        r = self.client.post(f"/api/v1/missions/{created['id']}/launch")
        body = r.json()
        self.assertEqual(body["status"], "real")
        self.assertEqual(body["data"]["state"], "review")
        self.assertIsNotNone(body["data"]["result"])
        self.assertIn("network_reachable", body["data"]["result"])
        self.assertIn("false", body["data"]["result"].lower())

        # Le résultat de /launch est ensuite validable via la route existante.
        validated = self.client.post(f"/api/v1/missions/{created['id']}/validate").json()
        self.assertEqual(validated["status"], "real")
        self.assertEqual(validated["data"]["state"], "completed")

    def test_launch_mission_without_assigned_agent_fails_cleanly(self) -> None:
        created = self.client.post(
            "/api/v1/missions", json={"title": "t", "specialty": "research", "objective": "o"}
        ).json()["data"]
        r = self.client.post(f"/api/v1/missions/{created['id']}/launch")
        body = r.json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["status"], "unknown")
        self.assertIsNotNone(body["detail"])

        # Aucun raccourci silencieux : la mission reste QUEUED, pas RUNNING/FAILED.
        fetched = self.client.get(f"/api/v1/missions/{created['id']}").json()
        self.assertEqual(fetched["data"]["state"], "queued")

    def test_launching_an_already_launched_mission_fails_the_second_time(self) -> None:
        clone = self._spawn_research_clone()
        created = self.client.post(
            "/api/v1/missions",
            json={
                "title": "t", "specialty": "research", "objective": "o",
                "assigned_agent": clone.clone_id,
            },
        ).json()["data"]

        first = self.client.post(f"/api/v1/missions/{created['id']}/launch").json()
        self.assertEqual(first["status"], "real")
        self.assertEqual(first["data"]["state"], "review")

        second = self.client.post(f"/api/v1/missions/{created['id']}/launch").json()
        self.assertEqual(second["status"], "unknown")

    def test_get_scheduler_status_reports_real_measurements(self) -> None:
        """GET /scheduler — étape 11. Aucune donnée inventée : capacité et
        exécutions suivies sont de vraies mesures, heartbeat reste UNKNOWN
        (arbitrage humain étape 11, point B — HeartbeatService non démarré)."""
        r = self.client.get("/api/v1/scheduler")
        body = r.json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["status"], "real")
        data = body["data"]
        self.assertGreaterEqual(data["max_concurrent_missions"], 1)
        self.assertEqual(data["running_missions"], 0)
        self.assertEqual(data["tracked_executions"], 0)
        self.assertEqual(data["available_slots"], data["max_concurrent_missions"])
        self.assertEqual(data["heartbeat"]["status"], "unknown")
        self.assertIsNone(data["heartbeat"]["value"])

    def test_launch_routes_through_the_scheduler_and_reports_unmet_dependency(self) -> None:
        """Différence de comportement documentée par rapport à l'étape 7 :
        les dépendances non satisfaites sont désormais vérifiées à /launch,
        parce que la route passe réellement par le Scheduler (qui les
        vérifiait déjà, mais que rien n'appelait). `depends_on` n'est pas
        exposé par MissionCreateIn (aucune route ne le permet aujourd'hui) —
        construit directement via MissionService, comme les tests
        validate/reject ci-dessus construisent leur état via le service."""
        from apex.config.loader import load_config
        from apex.db.engine import build_engine, build_session_factory, database_url_from_config
        from apex.missions.service import MissionService
        from apex.policy.model import Subject

        config = load_config()
        factory = build_session_factory(build_engine(database_url_from_config(config)))
        service = MissionService(factory)

        upstream_clone = self._spawn_research_clone()
        upstream = service.create(
            title="amont", description="", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=upstream_clone.clone_id,
        )
        downstream_clone = self._spawn_research_clone()
        downstream = service.create(
            title="aval", description="", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=downstream_clone.clone_id,
            depends_on=(upstream.id,),
        )

        r = self.client.post(f"/api/v1/missions/{downstream.id}/launch")
        body = r.json()
        self.assertEqual(body["status"], "unknown")
        self.assertIn("dépendance", body["detail"])

        fetched = self.client.get(f"/api/v1/missions/{downstream.id}").json()
        self.assertEqual(fetched["data"]["state"], "queued")

    def test_launch_reports_unknown_when_scheduler_capacity_is_exhausted(self) -> None:
        """La mission n'est jamais perdue sous capacité insuffisante : elle
        reste QUEUED, réponse honnête immédiate plutôt qu'une exécution
        forcée. Capacité épuisée simulée en pré-remplissant ExecutionRegistry
        — même technique que tests/test_scheduler_service.py."""
        from apex.api.dependencies import get_scheduler_service
        from apex.scheduler.model import ExecutionHandle

        clone = self._spawn_research_clone()
        created = self.client.post(
            "/api/v1/missions",
            json={
                "title": "t", "specialty": "research", "objective": "o",
                "assigned_agent": clone.clone_id,
            },
        ).json()["data"]

        scheduler = get_scheduler_service()
        max_slots = scheduler.capacity().max_concurrent_missions
        fillers = [ExecutionHandle(mission_id=f"filler-{i}", clone_id=clone.clone_id) for i in range(max_slots)]
        for handle in fillers:
            scheduler.registry.track(handle)
        try:
            r = self.client.post(f"/api/v1/missions/{created['id']}/launch")
            body = r.json()
            self.assertEqual(body["status"], "unknown")
            self.assertIn("capacité", body["detail"])
        finally:
            for handle in fillers:
                scheduler.registry.discard(handle.mission_id)

        fetched = self.client.get(f"/api/v1/missions/{created['id']}").json()
        self.assertEqual(fetched["data"]["state"], "queued")

    def test_two_concurrent_launch_requests_for_the_same_mission_never_double_execute(self) -> None:
        """Deux requêtes /launch concurrentes sur la même mission convergent
        sans double exécution — la garantie réelle reste l'UPDATE atomique de
        MissionService.mark_running (étape 8), prouvée ici à travers la
        route HTTP plutôt qu'en appelant SchedulerService directement."""
        import threading

        from apex.config.loader import load_config
        from apex.db.engine import build_engine, build_session_factory, database_url_from_config, session_scope
        from apex.db.repositories import EventRepository

        clone = self._spawn_research_clone()
        created = self.client.post(
            "/api/v1/missions",
            json={
                "title": "t", "specialty": "research", "objective": "o",
                "assigned_agent": clone.clone_id,
            },
        ).json()["data"]

        responses: list[dict] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def call() -> None:
            barrier.wait()
            r = self.client.post(f"/api/v1/missions/{created['id']}/launch")
            with lock:
                responses.append(r.json())

        threads = [threading.Thread(target=call) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(responses), 2)

        final = self.client.get(f"/api/v1/missions/{created['id']}").json()["data"]
        self.assertEqual(final["state"], "review")

        config = load_config()
        factory = build_session_factory(build_engine(database_url_from_config(config)))
        with session_scope(factory) as session:
            events = EventRepository(session).recent(limit=200)
        starts = [
            e for e in events
            if e.msg == "mission.started" and e.context.get("mission_id") == created["id"]
        ]
        self.assertEqual(len(starts), 1, "la mission a été lancée plus d'une fois")

    def test_scheduler_singleton_is_never_recreated_across_requests(self) -> None:
        """Arbitrage humain étape 11, point A : le Scheduler est un singleton
        applicatif paresseux, jamais recréé par requête. Deux appels à
        GET /scheduler dans le même processus doivent donc observer le MÊME
        ExecutionRegistry (même identité d'objet, pas seulement des valeurs
        égales)."""
        from apex.api.dependencies import get_scheduler_service

        self.client.get("/api/v1/scheduler")
        first = get_scheduler_service()
        self.client.get("/api/v1/scheduler")
        second = get_scheduler_service()
        self.assertIs(first, second)
        self.assertIs(first.registry, second.registry)


if __name__ == "__main__":
    unittest.main()

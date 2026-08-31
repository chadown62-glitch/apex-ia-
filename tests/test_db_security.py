"""Sécurité SQL réelle — rôles, GRANT/REVOKE, audit_log en écriture seule.

Ces garanties ne sont PAS testables via SQLite (tests/test_db_models.py le
documente déjà) : SQLite n'a pas de rôles ni de GRANT/REVOKE. Chaque test ici
se connecte RÉELLEMENT avec le rôle applicatif concerné (jamais le
superutilisateur, qui contournerait tout) et vérifie que PostgreSQL refuse
l'opération interdite — pas seulement que le DDL de la migration la déclare.

SKIP explicite si PostgreSQL réel n'est pas joignable : voir
tests/db_test_support.py et docs/PHASE_2_REPORT.md.
"""

from __future__ import annotations

import unittest

from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestAuditLogInsertOnly(unittest.TestCase):
    """Critère de validation §5.2 du brief : DELETE sur audit_log doit échouer."""

    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        # Ordre obligatoire : voir tests/test_db_migrations.py.
        support.drop_test_database()
        support.drop_application_roles()

    def test_apex_audit_can_insert(self) -> None:
        import psycopg

        with support.role_connection("apex_audit") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_log (ts, actor, action, target, details) "
                "VALUES (now(), 'governor', 'promote', 'genome:x', '{}'::jsonb)"
            )

    def test_apex_audit_cannot_delete(self) -> None:
        import psycopg

        with support.role_connection("apex_audit") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM audit_log")

    def test_apex_audit_cannot_update(self) -> None:
        import psycopg

        with support.role_connection("apex_audit") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("UPDATE audit_log SET actor = 'x'")

    def test_apex_audit_cannot_truncate(self) -> None:
        import psycopg

        with support.role_connection("apex_audit") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("TRUNCATE audit_log")

    def test_apex_core_can_read_audit_log_but_not_write(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM audit_log")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM audit_log")


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestAgentRoleIsWriteRestricted(unittest.TestCase):
    """apex_agent journalise des événements mais ne modifie jamais compute_pools
    ni les événements déjà écrits — anti-contamination minimale (§6 PHASE_1_REPORT)."""

    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        # Ordre obligatoire : voir tests/test_db_migrations.py.
        support.drop_test_database()
        support.drop_application_roles()

    def test_apex_agent_can_insert_events(self) -> None:
        with support.role_connection("apex_agent") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO events (ts, level, logger, msg, context) "
                "VALUES (now(), 'INFO', 'apex.agent', 'run', '{}'::jsonb)"
            )

    def test_apex_agent_cannot_update_events(self) -> None:
        import psycopg

        with support.role_connection("apex_agent") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("UPDATE events SET level = 'DEBUG'")

    def test_apex_agent_cannot_write_compute_pools(self) -> None:
        import psycopg

        with support.role_connection("apex_agent") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO compute_pools (pool_id, location, trust_level, capabilities) "
                        "VALUES ('rogue', 'local', 'trusted', '[]'::jsonb)"
                    )


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestReadonlyRoleIsReadOnly(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        # Ordre obligatoire : voir tests/test_db_migrations.py.
        support.drop_test_database()
        support.drop_application_roles()

    def test_apex_readonly_can_select(self) -> None:
        with support.role_connection("apex_readonly") as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM compute_pools")
            cur.execute("SELECT count(*) FROM events")
            cur.execute("SELECT count(*) FROM audit_log")

    def test_apex_readonly_cannot_insert_anywhere(self) -> None:
        import psycopg

        for statement in (
            "INSERT INTO compute_pools (pool_id, location, trust_level, capabilities) "
            "VALUES ('x', 'local', 'trusted', '[]'::jsonb)",
            "INSERT INTO events (ts, level, logger, msg, context) "
            "VALUES (now(), 'INFO', 'x', 'x', '{}'::jsonb)",
            "INSERT INTO audit_log (ts, actor, action, target, details) "
            "VALUES (now(), 'x', 'x', NULL, '{}'::jsonb)",
        ):
            with support.role_connection("apex_readonly") as conn:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with conn.cursor() as cur:
                        cur.execute(statement)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestMissionRoleIsWriteRestricted(unittest.TestCase):
    """apex_core est le seul rôle qui écrit `missions` — aucun rôle de clone
    (apex_agent/apex_eval/apex_audit) n'y a accès : cohérent avec
    PolicyEngine (aucun Subject de clone n'a CREATE/CANCEL/VALIDATE/
    REJECT_MISSION dans son allow-list)."""

    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def test_apex_core_can_insert_and_update_missions(self) -> None:
        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO missions (id, title, description, specialty, objective, priority, "
                "state, created_by, created_at) VALUES "
                "('m1', 't', 'd', 'research', 'o', 'normal', 'draft', 'human', now())"
            )
            cur.execute("UPDATE missions SET state = 'queued' WHERE id = 'm1'")

    def test_apex_core_cannot_delete_missions(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM missions")

    def test_apex_agent_cannot_write_missions(self) -> None:
        """Un clone (rôle apex_agent) n'a aucun accès à `missions` — pas même en lecture."""
        import psycopg

        with support.role_connection("apex_agent") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO missions (id, title, description, specialty, objective, "
                        "priority, state, created_by, created_at) VALUES "
                        "('rogue', 't', 'd', 'research', 'o', 'normal', 'draft', 'research', now())"
                    )

    def test_apex_readonly_can_select_but_not_insert_missions(self) -> None:
        import psycopg

        with support.role_connection("apex_readonly") as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM missions")
        with support.role_connection("apex_readonly") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO missions (id, title, description, specialty, objective, "
                        "priority, state, created_by, created_at) VALUES "
                        "('x', 't', 'd', 'research', 'o', 'normal', 'draft', 'human', now())"
                    )


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestMemoryRoleIsWriteRestricted(unittest.TestCase):
    """apex_core est le seul rôle qui écrit `memory_records`/`memory_relations`
    — aucun rôle de clone n'y a accès direct, cohérent avec PolicyEngine
    (un clone n'écrit jamais sa propre ligne, seul MemoryService le fait
    pour son compte). DELETE/TRUNCATE refusés même à apex_core : aucune
    ligne mémoire n'est jamais supprimée, seule une révision (nouvelle
    ligne) ou une promotion de visibilité (UPDATE ciblé) existe."""

    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def test_apex_core_can_insert_and_update_memory_records(self) -> None:
        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memory_records (id, kind, content, source, created_by, "
                "confidence, verification_status, visibility) VALUES "
                "('mem1', 'semantic', 'x', 'https://example.com', 'research', 0.5, "
                "'uncertain', 'own')"
            )
            cur.execute("UPDATE memory_records SET visibility = 'shared' WHERE id = 'mem1'")

    def test_apex_core_cannot_delete_memory_records(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM memory_records")

    def test_apex_core_can_insert_memory_relations_but_not_delete(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memory_records (id, kind, content, source, created_by, "
                "confidence, verification_status, visibility) VALUES "
                "('mem2', 'semantic', 'y', 'https://example.com', 'research', 0.5, "
                "'uncertain', 'own')"
            )
            cur.execute(
                "INSERT INTO memory_records (id, kind, content, source, created_by, "
                "confidence, verification_status, visibility) VALUES "
                "('mem3', 'semantic', 'z', 'https://example.com', 'research', 0.5, "
                "'uncertain', 'own')"
            )
            cur.execute(
                "INSERT INTO memory_relations (id, source_id, target_id, relation, "
                "provenance, created_by) VALUES "
                "('rel1', 'mem2', 'mem3', 'contradicts', 'cross-check', 'research')"
            )
        with support.role_connection("apex_core") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM memory_relations")

    def test_apex_agent_cannot_write_memory_records(self) -> None:
        """Un clone (rôle apex_agent) n'a aucun accès à `memory_records` —
        pas même en lecture : seul MemoryService écrit pour son compte."""
        import psycopg

        with support.role_connection("apex_agent") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO memory_records (id, kind, content, source, created_by, "
                        "confidence, verification_status, visibility) VALUES "
                        "('rogue', 'semantic', 'x', 'x', 'research', 0.5, 'uncertain', 'own')"
                    )

    def test_apex_readonly_can_select_but_not_insert_memory_records(self) -> None:
        import psycopg

        with support.role_connection("apex_readonly") as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM memory_records")
            cur.execute("SELECT count(*) FROM memory_relations")
        with support.role_connection("apex_readonly") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO memory_records (id, kind, content, source, created_by, "
                        "confidence, verification_status, visibility) VALUES "
                        "('x', 'semantic', 'x', 'x', 'human', 0.5, 'uncertain', 'own')"
                    )


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestHeldoutSplitSeparation(unittest.TestCase):
    """Critère de validation §5.3 du brief : le rôle worker ne doit pas lire
    le split HELDOUT.

    Résolu pour de vrai en phase 5 (TaskBank, migration `eda413ca0829`) :
    `apex_eval` n'a AUCUN `GRANT` sur `tasks`, ni sur le split HELDOUT ni
    sur TRAIN. La séparation n'est donc PAS appliquée par une clause WHERE
    ou une règle applicative (contournable) mais par l'absence totale de
    privilège SQL sur la table entière — le SGBD refuse la lecture avant
    même qu'une ligne HELDOUT ne soit distinguée d'une ligne TRAIN. Ce test
    prouve le refus RÉEL par PostgreSQL, contre le vrai schéma, pas un
    schéma fictif construit pour la circonstance (voir l'historique : ce
    test était `@unittest.skip`, reporté depuis `docs/PHASE_2_REPORT.md`).
    """

    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def _insert_task(self, task_id: str, split: str) -> None:
        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks (id, split, specialty, title, description, command, "
                "created_by, created_at) VALUES "
                "(%s, %s, 'research', 't', 'd', '[\"true\"]'::jsonb, 'human', now())",
                (task_id, split),
            )

    def test_worker_role_cannot_read_heldout_split(self) -> None:
        import psycopg

        self._insert_task("t-heldout", "heldout")
        with support.role_connection("apex_eval") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM tasks WHERE id = 't-heldout'")

    def test_worker_role_cannot_read_train_split_either(self) -> None:
        """Le refus est TOTAL, pas seulement sur HELDOUT : `apex_eval` n'a
        aucun `GRANT` sur `tasks`, quel que soit le split — la table entière
        est hors de portée, jamais un filtre par ligne qui pourrait, lui,
        être contourné par une requête différente."""
        import psycopg

        self._insert_task("t-train", "train")
        with support.role_connection("apex_eval") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM tasks WHERE id = 't-train'")


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestTaskRoleIsWriteRestricted(unittest.TestCase):
    """`tasks` est INSERT seul pour apex_core — UPDATE est révoqué même pour
    apex_core (plus strict que `missions`/`memory_records`) : une tâche ne
    change jamais après coup, voir `apex.db.models.TaskRow`."""

    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def test_apex_core_can_insert_but_not_update_tasks(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks (id, split, specialty, title, description, command, "
                "created_by, created_at) VALUES "
                "('t1', 'train', 'research', 't', 'd', '[\"true\"]'::jsonb, 'human', now())"
            )
        with support.role_connection("apex_core") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("UPDATE tasks SET title = 'x' WHERE id = 't1'")

    def test_apex_core_cannot_delete_tasks(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM tasks")

    def test_apex_agent_cannot_write_tasks(self) -> None:
        """Un clone (rôle apex_agent) n'a aucun accès à `tasks` — pas même
        en lecture : même motif que `apex_eval` (HELDOUT), mais ici pour
        TOUT clone, pas seulement l'exécuteur d'évaluation."""
        import psycopg

        with support.role_connection("apex_agent") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO tasks (id, split, specialty, title, description, command, "
                        "created_by, created_at) VALUES "
                        "('rogue', 'train', 'research', 't', 'd', '[\"true\"]'::jsonb, 'research', now())"
                    )

    def test_apex_readonly_can_select_but_not_insert_tasks(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks (id, split, specialty, title, description, command, "
                "created_by, created_at) VALUES "
                "('t2', 'train', 'research', 't', 'd', '[\"true\"]'::jsonb, 'human', now())"
            )
        with support.role_connection("apex_readonly") as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM tasks")
        with support.role_connection("apex_readonly") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO tasks (id, split, specialty, title, description, command, "
                        "created_by, created_at) VALUES "
                        "('x', 'train', 'research', 't', 'd', '[\"true\"]'::jsonb, 'human', now())"
                    )


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestEvalRunRoleIsWriteRestricted(unittest.TestCase):
    """`eval_runs` est INSERT seul, mais accessible en écriture à la fois par
    apex_core (`EvalRunnerService`) et par apex_eval (rôle réservé sans
    usage depuis la migration initiale) — jamais par apex_agent : un clone
    n'écrit jamais son propre résultat d'évaluation."""

    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def _insert_task(self) -> str:
        import uuid

        task_id = f"t-{uuid.uuid4().hex[:12]}"
        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks (id, split, specialty, title, description, command, "
                "created_by, created_at) VALUES "
                "(%s, 'train', 'research', 't', 'd', '[\"true\"]'::jsonb, 'human', now())",
                (task_id,),
            )
        return task_id

    def test_apex_core_can_insert_but_not_update_eval_runs(self) -> None:
        import psycopg

        task_id = self._insert_task()
        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO eval_runs (id, task_id, clone_id, method, oracle_verdict, "
                "sandbox_status, facts, created_by, created_at) VALUES "
                "('r1', %s, 'c1', 'practice', 'pass', 'success', '{}'::jsonb, 'core', now())",
                (task_id,),
            )
        with support.role_connection("apex_core") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("UPDATE eval_runs SET oracle_verdict = 'fail' WHERE id = 'r1'")

    def test_apex_core_cannot_delete_eval_runs(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM eval_runs")

    def test_apex_eval_can_insert_eval_runs_but_not_read_tasks(self) -> None:
        """Rôle réservé depuis la migration initiale, enfin exercé : apex_eval
        peut écrire un résultat d'évaluation, mais reste aveugle au contenu
        de `tasks` — la garantie HELDOUT tient même pour le rôle qui exécute
        les évaluations lui-même, pas seulement pour un rôle tiers."""
        import psycopg

        task_id = self._insert_task()
        with support.role_connection("apex_eval") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO eval_runs (id, task_id, clone_id, method, oracle_verdict, "
                    "sandbox_status, facts, created_by, created_at) VALUES "
                    "('r2', %s, 'c1', 'practice', 'pass', 'success', '{}'::jsonb, 'core', now())",
                    (task_id,),
                )
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM tasks")

    def test_apex_agent_cannot_write_eval_runs(self) -> None:
        import psycopg

        task_id = self._insert_task()
        with support.role_connection("apex_agent") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO eval_runs (id, task_id, clone_id, method, oracle_verdict, "
                        "sandbox_status, facts, created_by, created_at) VALUES "
                        "('rogue', %s, 'c1', 'practice', 'pass', 'success', '{}'::jsonb, "
                        "'research', now())",
                        (task_id,),
                    )

    def test_apex_readonly_can_select_but_not_insert_eval_runs(self) -> None:
        import psycopg

        task_id = self._insert_task()
        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO eval_runs (id, task_id, clone_id, method, oracle_verdict, "
                "sandbox_status, facts, created_by, created_at) VALUES "
                "('r3', %s, 'c1', 'practice', 'pass', 'success', '{}'::jsonb, 'core', now())",
                (task_id,),
            )
        with support.role_connection("apex_readonly") as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM eval_runs")
        with support.role_connection("apex_readonly") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO eval_runs (id, task_id, clone_id, method, oracle_verdict, "
                        "sandbox_status, facts, created_by, created_at) VALUES "
                        "('x', 't-for-run', 'c1', 'practice', 'pass', 'success', '{}'::jsonb, "
                        "'human', now())"
                    )


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestSkillRoleIsWriteRestricted(unittest.TestCase):
    """`skills` : apex_core écrit ET met à jour (le SEUL usage réel de cet
    UPDATE est `decide()`, ciblé) — jamais apex_agent/apex_eval/apex_audit,
    un clone ne peut jamais se créditer lui-même d'une compétence (§8
    anti-auto-illusion)."""

    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def test_apex_core_can_insert_and_update_skills(self) -> None:
        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO skills (id, name, specialty, description, status, "
                "prerequisite_skill_ids, evidence_run_ids, created_by, created_at) VALUES "
                "('s1', 'n', 'research', 'd', 'proposed', '[]'::jsonb, '[]'::jsonb, 'human', now())"
            )
            cur.execute("UPDATE skills SET status = 'acquired' WHERE id = 's1'")

    def test_apex_core_cannot_delete_skills(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM skills")

    def test_apex_agent_cannot_write_skills(self) -> None:
        """Un clone (rôle apex_agent) n'a aucun accès à `skills` — pas même
        en lecture : seul le service écrit pour son compte."""
        import psycopg

        with support.role_connection("apex_agent") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO skills (id, name, specialty, description, status, "
                        "prerequisite_skill_ids, evidence_run_ids, created_by, created_at) VALUES "
                        "('rogue', 'n', 'research', 'd', 'acquired', '[]'::jsonb, '[]'::jsonb, "
                        "'research', now())"
                    )

    def test_apex_readonly_can_select_but_not_insert_skills(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO skills (id, name, specialty, description, status, "
                "prerequisite_skill_ids, evidence_run_ids, created_by, created_at) VALUES "
                "('s2', 'n', 'research', 'd', 'proposed', '[]'::jsonb, '[]'::jsonb, 'human', now())"
            )
        with support.role_connection("apex_readonly") as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM skills")
        with support.role_connection("apex_readonly") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO skills (id, name, specialty, description, status, "
                        "prerequisite_skill_ids, evidence_run_ids, created_by, created_at) VALUES "
                        "('x', 'n', 'research', 'd', 'proposed', '[]'::jsonb, '[]'::jsonb, 'human', now())"
                    )


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestGenomeVersionRoleIsWriteRestricted(unittest.TestCase):
    """`genome_versions` : apex_core écrit ET met à jour (le SEUL usage réel
    de cet UPDATE est `decide()`, ciblé) — jamais apex_agent/apex_eval/
    apex_audit : invariant absolu (ADR-0001), aucun clone ne peut même
    proposer une version de Genome, a fortiori la promouvoir."""

    @classmethod
    def setUpClass(cls) -> None:
        support.recreate_test_database()
        support.run_upgrade()

    @classmethod
    def tearDownClass(cls) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def test_apex_core_can_insert_and_update_genome_versions(self) -> None:
        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO genome_versions (id, content_hash, origin, status, "
                "created_by, created_at) VALUES "
                "('g1', 'sha256:" + "0" * 64 + "', 'manual', 'proposed', 'human', now())"
            )
            cur.execute("UPDATE genome_versions SET status = 'promoted' WHERE id = 'g1'")

    def test_apex_core_cannot_delete_genome_versions(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM genome_versions")

    def test_apex_agent_cannot_write_genome_versions(self) -> None:
        """Un clone (rôle apex_agent) n'a aucun accès à `genome_versions` —
        pas même en lecture : le Genome ne peut jamais être influencé par
        un clone, même indirectement via une simple proposition."""
        import psycopg

        with support.role_connection("apex_agent") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO genome_versions (id, content_hash, origin, status, "
                        "created_by, created_at) VALUES "
                        "('rogue', 'sha256:" + "0" * 64 + "', 'manual', 'proposed', 'research', now())"
                    )

    def test_apex_readonly_can_select_but_not_insert_genome_versions(self) -> None:
        import psycopg

        with support.role_connection("apex_core") as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO genome_versions (id, content_hash, origin, status, "
                "created_by, created_at) VALUES "
                "('g2', 'sha256:" + "0" * 64 + "', 'manual', 'proposed', 'human', now())"
            )
        with support.role_connection("apex_readonly") as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM genome_versions")
        with support.role_connection("apex_readonly") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO genome_versions (id, content_hash, origin, status, "
                        "created_by, created_at) VALUES "
                        "('x', 'sha256:" + "0" * 64 + "', 'manual', 'proposed', 'human', now())"
                    )


if __name__ == "__main__":
    unittest.main()

"""Tests de `apex.api.auth` — jeton local, Phase 0, câblé et durci passe
« API Authentication Hardening » (phase N+1).

Volets : le comportement HTTP (via `api_test_support.authed_client()`,
jeton éphémère, jamais le fichier réel du poste), le comportement du module
lui-même (génération/persistance, isolé dans un répertoire temporaire —
jamais `.apex/api-token` du dépôt), l'autorisation (PolicyEngine, DÉJÀ
existante et réutilisée telle quelle — jamais dupliquée ici), l'exposition
OpenAPI/CORS, et la non-régression de la journalisation d'audit existante."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from apex.api import auth
from apex.api.app import create_app
from tests import api_test_support


class TestTokenRequiredOnEveryRoute(unittest.TestCase):
    """Frontière uniforme (décision explicite) : `/health` — la route la
    plus anodine possible, sans DB, qui ne lève jamais — est protégée
    exactement comme les autres. Si elle l'est, toutes le sont (même boucle
    d'inclusion dans `apex.api.app.create_app`)."""

    def setUp(self) -> None:
        self.client = api_test_support.authed_client()

    def test_missing_authorization_header_is_rejected(self) -> None:
        anonymous = TestClient(create_app())
        response = anonymous.get("/api/v1/health")
        self.assertEqual(response.status_code, 401)

    def test_malformed_authorization_header_is_rejected(self) -> None:
        client = TestClient(create_app(), headers={"Authorization": "not-a-bearer-token"})
        response = client.get("/api/v1/health")
        self.assertEqual(response.status_code, 401)

    def test_wrong_token_is_rejected(self) -> None:
        client = TestClient(create_app(), headers={"Authorization": "Bearer wrong-token-value"})
        response = client.get("/api/v1/health")
        self.assertEqual(response.status_code, 401)

    def test_correct_token_is_accepted(self) -> None:
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)

    def test_rejection_never_echoes_the_provided_or_expected_token(self) -> None:
        """Le corps de la réponse 401 ne doit jamais citer une valeur de
        jeton — ni celle fournie (secret potentiellement intercepté par un
        tiers), ni celle attendue (ça reviendrait à publier le secret)."""

        marker = "MARKER-SECRET-SHOULD-NEVER-APPEAR-0000"
        client = TestClient(create_app(), headers={"Authorization": f"Bearer {marker}"})
        response = client.get("/api/v1/health")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(marker, response.text)
        self.assertNotIn(api_test_support.TEST_API_TOKEN, response.text)

    def test_rejection_is_handled_by_fastapi_not_the_generic_exception_handler(self) -> None:
        """Un `HTTPException(401)` doit être traité par le gestionnaire
        FastAPI standard, jamais intercepté par le filet de sécurité
        générique de `apex.api.app` (qui envelopperait la réponse
        différemment — `{"status": "unknown", ...}`, réservé aux bugs
        réels, pas à un refus d'authentification attendu)."""

        anonymous = TestClient(create_app())
        response = anonymous.get("/api/v1/health")
        body = response.json()
        self.assertNotIn("status", body)  # pas l'enveloppe DataEnvelope/générique
        self.assertIn("detail", body)

    def test_write_route_is_also_protected_not_only_reads(self) -> None:
        """Vérifie qu'une route d'ÉCRITURE (pas seulement `/health`, en
        lecture) exige elle aussi le jeton — la boucle d'inclusion dans
        `apex.api.app` ne distingue pas lecture/écriture, par décision
        explicite."""

        anonymous = TestClient(create_app())
        response = anonymous.post("/api/v1/projects", json={"name": "x"})
        self.assertEqual(response.status_code, 401)


class TestTokenResolution(unittest.TestCase):
    """Comportement de `get_or_create_api_token()` — isolé dans un
    répertoire temporaire, jamais `.apex/api-token` du dépôt réel."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._token_file = Path(self._tmpdir.name) / "api-token"
        self._patch = mock.patch.object(auth, "TOKEN_FILE", self._token_file)
        self._patch.start()
        self._env_saved = os.environ.pop("APEX_API_TOKEN", None)

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmpdir.cleanup()
        if self._env_saved is not None:
            os.environ["APEX_API_TOKEN"] = self._env_saved

    def test_env_var_takes_priority_over_file(self) -> None:
        os.environ["APEX_API_TOKEN"] = "from-env-var"
        try:
            self.assertFalse(self._token_file.exists())  # jamais touché
            self.assertEqual(auth.get_or_create_api_token(), "from-env-var")
        finally:
            del os.environ["APEX_API_TOKEN"]

    def test_first_call_generates_and_persists_a_token(self) -> None:
        self.assertFalse(self._token_file.exists())
        token = auth.get_or_create_api_token()
        self.assertTrue(self._token_file.is_file())
        self.assertEqual(self._token_file.read_text(encoding="utf-8").strip(), token)
        self.assertGreater(len(token), 20)  # entropie réelle, pas un placeholder

    def test_second_call_reuses_the_persisted_token(self) -> None:
        first = auth.get_or_create_api_token()
        second = auth.get_or_create_api_token()
        self.assertEqual(first, second)

    def test_token_file_has_owner_only_permissions(self) -> None:
        auth.get_or_create_api_token()
        mode = stat.S_IMODE(self._token_file.stat().st_mode)
        self.assertEqual(mode, stat.S_IRUSR | stat.S_IWUSR)

    def test_token_file_write_is_atomic_no_stray_temp_file_left(self) -> None:
        auth.get_or_create_api_token()
        leftover = list(self._token_file.parent.glob("*.tmp"))
        self.assertEqual(leftover, [])

    def test_each_generated_token_is_distinct(self) -> None:
        """Preuve minimale que la génération n'est pas déterministe/figée —
        pas une preuve cryptographique complète, mais détecterait une
        régression grossière (valeur codée en dur, graine fixe)."""
        token_a = auth._generate_token()
        token_b = auth._generate_token()
        self.assertNotEqual(token_a, token_b)


class TestTokenCreatedEagerlyAtStartup(unittest.TestCase):
    """Régression du bootstrap découvert lors de la validation physique du
    Dashboard Windows (`ApexSetup.Dashboard.Services.ApiHealthChecker`) :
    ce dernier lit `.apex/api-token` via WSL AVANT de faire la moindre
    requête HTTP (pour ne jamais sonder l'API sans jeton) — si ce fichier
    n'existe qu'après la première requête (ancien comportement, jeton
    résolu uniquement par la dépendance `require_api_token`), un
    contrôleur externe qui doit le LIRE avant de pouvoir faire cette
    première requête ne peut jamais la déclencher : blocage réel jusqu'au
    timeout, alors que l'API tourne parfaitement. `apex.api.app._lifespan`
    crée désormais le jeton avant que le serveur ASGI n'accepte des
    connexions (voir sa docstring) — prouvé ici en n'envoyant AUCUNE
    requête, seulement en entrant le context manager `TestClient` (ce qui
    exécute le lifespan `startup`, garanti par le protocole ASGI avant
    toute connexion acceptée)."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._token_file = Path(self._tmpdir.name) / "api-token"
        self._patch = mock.patch.object(auth, "TOKEN_FILE", self._token_file)
        self._patch.start()
        self._env_saved = os.environ.pop("APEX_API_TOKEN", None)

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmpdir.cleanup()
        if self._env_saved is not None:
            os.environ["APEX_API_TOKEN"] = self._env_saved

    def test_token_file_exists_after_lifespan_startup_before_any_request(self) -> None:
        self.assertFalse(self._token_file.exists())
        with TestClient(create_app()):
            # Aucune requête envoyée ici — seul `__enter__` a exécuté le
            # lifespan `startup`. Si le jeton n'existe qu'après ce bloc,
            # c'est la régression exacte que ce test doit détecter.
            self.assertTrue(self._token_file.is_file())

    def test_the_pre_created_token_is_the_one_that_actually_authenticates(self) -> None:
        """Le jeton créé par le lifespan n'est pas un artefact séparé — la
        toute première requête, avant même de le lire côté appelant,
        s'authentifie déjà avec exactement cette valeur persistée."""
        with TestClient(create_app()) as client:
            token = self._token_file.read_text(encoding="utf-8").strip()
            response = client.get(
                "/api/v1/health", headers={"Authorization": f"Bearer {token}"}
            )
            self.assertEqual(response.status_code, 200)

    def test_an_unwritable_token_directory_never_prevents_the_app_from_starting(self) -> None:
        """Même discipline que le scheduler autonome voisin (voir docstring
        de `_lifespan`) : une panne à CE stade ne doit jamais empêcher le
        serveur de démarrer — `require_api_token` reste le point qui échoue
        bruyamment (500) à la première requête réelle, jamais ce lifespan."""
        with tempfile.TemporaryDirectory() as tmp:
            blocking_file = Path(tmp) / "blocked"
            blocking_file.write_text("not a directory")
            bad_token_file = blocking_file / "api-token"
            with mock.patch.object(auth, "TOKEN_FILE", bad_token_file):
                # Ne doit lever aucune exception — la seule assertion utile
                # ici est que ce bloc `with` s'exécute jusqu'au bout.
                with TestClient(create_app()):
                    pass


class TestConstantTimeComparison(unittest.TestCase):
    """`require_api_token` doit comparer via `secrets.compare_digest`,
    jamais `==` — vérifié en lisant le module (le comportement de timing
    lui-même n'est pas mesurable de façon fiable dans une suite unitaire)."""

    def test_module_uses_compare_digest_not_naive_equality(self) -> None:
        import inspect

        source = inspect.getsource(auth.require_api_token)
        self.assertIn("secrets.compare_digest", source)

    def test_module_never_imports_a_logger(self) -> None:
        """Défense en profondeur statique : ce module ne doit jamais avoir
        la possibilité de journaliser le jeton par erreur — il n'importe
        même pas de logger. Même patron que les tests statiques du
        launcher (`tests/test_windows_launcher.py`)."""
        import inspect

        source = inspect.getsource(auth)
        self.assertNotIn("import logging", source)
        self.assertNotIn("apex.obs.logging", source)


class TestGitignoreExcludesTokenFile(unittest.TestCase):
    """Garde de non-régression explicitement demandée avant implémentation :
    `.apex/` (et donc `.apex/api-token`) doit rester exclu de Git."""

    def test_gitignore_excludes_the_apex_state_directory(self) -> None:
        gitignore = Path(__file__).resolve().parent.parent / ".gitignore"
        content = gitignore.read_text(encoding="utf-8")
        self.assertIn(".apex/", content.splitlines())


class TestEveryRegisteredRouteRequiresAuthentication(unittest.TestCase):
    """Frontière uniforme, vérifiée SYSTÉMATIQUEMENT (pas seulement par
    sondage sur `/health`/`/projects`) : chaque route enregistrée dans
    `apex.api.app.create_app()`, quelle que soit sa méthode HTTP ou ses
    paramètres de chemin, refuse un appelant anonyme. Les paramètres de
    chemin (`{clone_id}`, `{mission_id}`, ...) sont remplacés par une valeur
    factice — jamais résolue, la vérification `require_api_token` doit de
    toute façon s'exécuter avant toute tentative de résolution métier
    (confirmé empiriquement : l'authentification prime sur la validation de
    corps/chemin dans l'ordre de résolution des dépendances FastAPI)."""

    def test_every_route_and_every_method_is_protected(self) -> None:
        """`app.routes` (FastAPI >= 0.140, `_IncludedRouter`) n'expose plus
        directement les chemins concrets : ils restent portés par
        `original_router.routes` (l'`APIRouter` passé à `include_router`),
        SANS le préfixe `API_PREFIX` appliqué au moment de l'inclusion —
        recomposé explicitement ci-dessous plutôt que supposé."""
        from apex.api import ui
        from apex.api.app import API_PREFIX, create_app

        app = create_app()
        anonymous = TestClient(app)
        checked = 0
        for included in app.routes:
            original_router = getattr(included, "original_router", None)
            if original_router is None or original_router is ui.page_router:
                continue  # pages de l'interface : exception unique, bornée par le test ci-dessous
            for route in original_router.routes:
                path = getattr(route, "path", "")
                methods = getattr(route, "methods", None) or set()
                concrete_path = API_PREFIX + path.format_map(
                    {p.strip("{}"): "test-placeholder" for p in _path_params(path)}
                )
                for method in methods - {"HEAD", "OPTIONS"}:
                    with self.subTest(path=concrete_path, method=method):
                        response = anonymous.request(method, concrete_path, json={})
                        self.assertEqual(
                            response.status_code, 401,
                            f"{method} {concrete_path} n'a pas exigé d'authentification "
                            f"(status={response.status_code})",
                        )
                        checked += 1
        self.assertGreater(checked, 10)  # preuve qu'on a bien parcouru un ensemble réel


    def test_the_only_unauthenticated_routes_are_the_interface_pages(self) -> None:
        """Seule exception à la frontière : les pages de l'interface web (`apex.api.ui.page_router`),
        sans aucune donnée — fichiers du build, échange d'un code à usage unique, déconnexion. Figée
        ici : toute route ajoutée à ce routeur fait échouer ce test, et aucune ne sert /api/."""
        from apex.api import ui
        from apex.api.app import create_app

        routes = {(r.path, tuple(sorted(r.methods))) for r in ui.page_router.routes}
        self.assertEqual(routes, {("/ui/login", ("GET",)), ("/ui/logout", ("POST",)), ("/{path:path}", ("GET",))})
        anonymous = TestClient(create_app())
        for path in ("/api/v1/does-not-exist", "/api", "/api/v1"):
            with self.subTest(path=path):
                self.assertIn(anonymous.get(path).status_code, (401, 404))
        self.assertEqual(anonymous.get("/ui/login?code=forged").status_code, 401)


def _path_params(path: str) -> list[str]:
    import re

    return re.findall(r"\{([^}]+)\}", path)


class TestOpenApiDocsNotPubliclyExposed(unittest.TestCase):
    """`/docs`/`/redoc`/`/openapi.json` sont des routes FastAPI internes,
    ajoutées HORS `include_router` — elles ne portent jamais les dépendances
    passées à `include_router` (limitation documentée de FastAPI/Starlette,
    voir `apex.api.app`). Seule protection réelle : les désactiver par
    défaut, jamais compter sur `require_api_token` pour les couvrir."""

    def test_docs_disabled_by_default(self) -> None:
        saved = os.environ.pop("APEX_API_ENABLE_DOCS", None)
        try:
            anonymous = TestClient(create_app())
            for path in ("/docs", "/redoc", "/openapi.json"):
                with self.subTest(path=path):
                    self.assertEqual(anonymous.get(path).status_code, 404)
        finally:
            if saved is not None:
                os.environ["APEX_API_ENABLE_DOCS"] = saved


class TestCorsAllowsAuthorizationHeader(unittest.TestCase):
    """Sans `Authorization` dans `allow_headers`, un navigateur bloquerait
    lui-même l'en-tête au stade du préflight CORS avant d'atteindre
    `require_api_token` — un frontend qui attacherait le jeton serait
    silencieusement cassé. Vérifié via une requête préflight `OPTIONS`
    réelle, pas une lecture de configuration."""

    def test_preflight_allows_the_authorization_header(self) -> None:
        client = TestClient(create_app())
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        self.assertEqual(response.status_code, 200)
        allowed = response.headers.get("access-control-allow-headers", "").lower()
        self.assertIn("authorization", allowed)


class TestInsufficientPermissionRemainsAuthorizationNotAuthentication(unittest.TestCase):
    """Le jeton prouve seulement « ce processus connaît le secret local » —
    jamais une identité ni un rôle (voir docstring de `apex.api.auth`).
    L'autorisation fine reste entièrement déléguée au `PolicyEngine`
    EXISTANT, jamais dupliquée par ce module : une requête authentifiée
    n'implique pas qu'une action donnée soit autorisée pour le `Subject`
    effectivement utilisé — démontré directement contre le PolicyEngine
    réellement réutilisé par chaque route (`apex.policy.engine`), sans DB,
    sans inventer un second système de rôles à la frontière HTTP."""

    def test_policy_engine_still_denies_an_action_outside_the_subjects_allowlist(self) -> None:
        from apex.errors import PolicyDenied
        from apex.policy.engine import PolicyEngine
        from apex.policy.model import Action, Subject

        policy = PolicyEngine()
        # LAUNCH_MISSION n'est jamais accordée à HUMAN (réservée à CORE,
        # voir apex/policy/model.py) — même après authentification réussie,
        # ce garde-fou reste entier.
        with self.assertRaises(PolicyDenied) as ctx:
            policy.authorize(Subject.HUMAN, Action.LAUNCH_MISSION)
        message = str(ctx.exception)
        self.assertIn("launch_mission", message)
        self.assertIn("human", message)
        # Le message reste sans secret — seuls sujet/action/motif y figurent.
        self.assertNotIn(api_test_support.TEST_API_TOKEN, message)

    def test_policy_decision_never_raises_only_authorize_does(self) -> None:
        from apex.policy.engine import PolicyEngine
        from apex.policy.model import Action, Subject

        decision = PolicyEngine().check(Subject.HUMAN, Action.LAUNCH_MISSION)
        self.assertFalse(decision.allowed)


class TestConfigurationFailure(unittest.TestCase):
    """Une panne de configuration (répertoire du jeton non inscriptible)
    doit échouer BRUYAMMENT — jamais retomber silencieusement sur un jeton
    vide/deviné, jamais accepter tout appelant par défaut."""

    def setUp(self) -> None:
        self._env_saved = os.environ.pop("APEX_API_TOKEN", None)

    def tearDown(self) -> None:
        if self._env_saved is not None:
            os.environ["APEX_API_TOKEN"] = self._env_saved

    def test_unwritable_token_directory_raises_rather_than_silently_bypassing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocking_file = Path(tmp) / "blocked"
            blocking_file.write_text("not a directory")
            bad_token_file = blocking_file / "api-token"
            with mock.patch.object(auth, "TOKEN_FILE", bad_token_file), self.assertRaises(OSError):
                auth.get_or_create_api_token()

    def test_configuration_failure_surfaces_as_a_safe_500_not_a_silent_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocking_file = Path(tmp) / "blocked"
            blocking_file.write_text("not a directory")
            bad_token_file = blocking_file / "api-token"
            with mock.patch.object(auth, "TOKEN_FILE", bad_token_file):
                client = TestClient(create_app(), raise_server_exceptions=False)
                response = client.get("/api/v1/health")
                self.assertEqual(response.status_code, 500)
                self.assertNotIn("Bearer", response.text)


@unittest.skipUnless(
    __import__("tests.db_test_support", fromlist=["postgres_testable"]).postgres_testable(),
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL",
)
class TestAuditLogUnaffectedByAuthHardening(unittest.TestCase):
    """Non-régression explicitement requise par la passe « API
    Authentication Hardening » : le câblage du jeton ne doit rien changer
    au mécanisme d'audit EXISTANT (`AuditLogRepository`, INSERT-only,
    inchangé) — une action authentifiée continue de produire la même trace
    qu'avant, réutilisée telle quelle, jamais dupliquée."""

    def setUp(self) -> None:
        from tests import db_test_support as support

        self._support = support
        support.recreate_test_database()
        support.run_upgrade()
        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME
        self.client = api_test_support.authed_client()

    def tearDown(self) -> None:
        self._support.drop_test_database()
        self._support.drop_application_roles()

    def test_authenticated_write_still_produces_an_audit_log_row(self) -> None:
        create = self.client.post("/api/v1/projects", json={"name": "audit-hardening-check"})
        self.assertEqual(create.status_code, 200)
        self.assertEqual(create.json()["status"], "real")

        audit = self.client.get("/api/v1/audit")
        rows = audit.json()["data"]
        actions = [r["action"] for r in rows]
        self.assertIn("project.created", actions)


if __name__ == "__main__":
    unittest.main()

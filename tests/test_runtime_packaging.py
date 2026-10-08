"""Tests de `runtime_build/` — sélection, construction, contrôle anti-fuite
et exécution en environnement propre de l'artefact APEX Runtime (phase
« implémentation de la séparation Runtime »).

Construit un artefact RÉEL (via `git archive` sur HEAD, voir
`runtime_build/build.py`) dans un répertoire temporaire pour toute la
classe — jamais un mock du mécanisme de build, jamais une exécution
simulée du CLI construit : ces tests doivent détecter une régression
réelle du processus de construction ou de la frontière Runtime/Research.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from runtime_build import selection
from runtime_build.alembic_check import check_alembic_upgrade
from runtime_build.build import build_runtime, build_runtime_from_worktree
from runtime_build.hashing import diff_trees, hash_tree, hash_tree_detailed
from runtime_build.verify import verify_runtime_artifact
from tests import db_test_support as support

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestSelectionPartition(unittest.TestCase):
    """Garde de non-régression : un nouveau sous-paquet ajouté à `apex/`
    doit être classé explicitement, jamais oublié silencieusement."""

    def test_every_apex_subpackage_is_classified(self) -> None:
        selection.verify_partition_matches_repo(REPO_ROOT / "apex")

    def test_runtime_and_research_are_disjoint(self) -> None:
        self.assertEqual(selection.RUNTIME_PACKAGES & selection.RESEARCH_PACKAGES, frozenset())

    def test_unclassified_subpackage_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_apex = Path(tmp) / "apex"
            fake_apex.mkdir()
            for name in selection.RUNTIME_PACKAGES:
                (fake_apex / name).mkdir()
                (fake_apex / name / "__init__.py").write_text("", encoding="utf-8")
            (fake_apex / "brand_new_module").mkdir()
            (fake_apex / "brand_new_module" / "__init__.py").write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                selection.verify_partition_matches_repo(fake_apex)


class _BuiltArtifactTestCase(unittest.TestCase):
    """Construit l'artefact UNE FOIS pour toute la classe (coûteux : `git
    archive` + sonde d'import en sous-processus) — jamais un artefact par
    test, jamais un artefact simulé."""

    artifact_dir: Path
    manifest: dict
    _tmpdir: tempfile.TemporaryDirectory

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.artifact_dir = Path(cls._tmpdir.name) / "apex-runtime"
        cls.manifest = build_runtime("HEAD", cls.artifact_dir)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmpdir.cleanup()


class TestBuildRuntimeArtifact(_BuiltArtifactTestCase):
    def test_manifest_records_a_resolved_full_sha_not_a_moving_ref(self) -> None:
        self.assertEqual(len(self.manifest["source_commit"]), 40)
        self.assertNotEqual(self.manifest["source_commit"], "HEAD")

    def test_all_runtime_packages_are_present(self) -> None:
        for pkg in selection.RUNTIME_PACKAGES:
            self.assertTrue((self.artifact_dir / "apex" / pkg).is_dir(), pkg)

    def test_no_research_package_directory_is_present(self) -> None:
        for pkg in selection.RESEARCH_PACKAGES:
            self.assertFalse((self.artifact_dir / "apex" / pkg).exists(), pkg)

    def test_no_pycache_is_copied(self) -> None:
        leftovers = list((self.artifact_dir / "apex").rglob("__pycache__"))
        self.assertEqual(leftovers, [])

    def test_alembic_migration_chain_is_copied_whole_and_unmodified(self) -> None:
        """La chaîne officielle complète, jamais un sous-ensemble trié —
        décision explicite (runtime_build/README.md §1)."""
        src_versions = sorted(p.name for p in (REPO_ROOT / "migrations" / "versions").glob("*.py"))
        built_versions = sorted(
            p.name for p in (self.artifact_dir / "migrations" / "versions").glob("*.py")
        )
        self.assertEqual(src_versions, built_versions)
        self.assertGreater(len(built_versions), 0)

    def test_migrations_never_import_a_research_module(self) -> None:
        """Les migrations s'exécutent dans le Runtime : aucune ne doit IMPORTER un module de recherche. Des
        docstrings historiques MENTIONNENT des modules (`apex.metalearner...`) pour expliquer une table — ce
        n'est pas une dépendance (06/10/2026 : contrôle par AST au lieu d'une recherche de texte)."""
        import ast

        research_prefixes = tuple(f"apex.{pkg}" for pkg in selection.RESEARCH_PACKAGES)
        for py_file in (self.artifact_dir / "migrations").rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            imported = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
            imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
            for name in imported:
                self.assertFalse(name.startswith(research_prefixes), f"{py_file} importe {name}")

    def test_artifacts_package_is_present_but_unwired_and_memory_is_wired(self) -> None:
        """`apex.artifacts` : `runtime-capable / currently unused` (présent, rien
        ne l'active). `apex.memory` : branché depuis le 06/10/2026 (mémoire
        système du Chat et du TaskLoop, route `/memory`) — il doit donc être
        présent ET exposé dans le Runtime."""
        self.assertTrue((self.artifact_dir / "apex" / "artifacts").is_dir())
        self.assertTrue((self.artifact_dir / "apex" / "memory").is_dir())
        self.assertTrue((self.artifact_dir / "apex" / "api" / "routes" / "memory.py").is_file())
        app_source = (self.artifact_dir / "apex" / "api" / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("apex.artifacts", app_source)
        self.assertIn("memory.router", app_source)

    def test_native_inference_ships_in_the_runtime(self) -> None:
        """Question « le Runtime packagé contient-il ce qu'il faut pour exécuter un vrai modèle sans
        Ollama ? » (06/10/2026) : le backend natif, la récupération EXPLICITE des poids, l'extra « native »
        et un endpoint `transformers` dans la configuration livrée."""
        for module in ("native.py", "fetch.py", "backends.py"):
            self.assertTrue((self.artifact_dir / "apex" / "models" / module).is_file(), module)
        extras = tomllib.loads((self.artifact_dir / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"]["optional-dependencies"]
        native = " ".join(extras["native"]).lower()
        for package in ("torch", "transformers", "safetensors", "huggingface_hub"):
            self.assertIn(package, native)
        self.assertIn("runtime: transformers", (self.artifact_dir / "config.yaml").read_text(encoding="utf-8"))

    def test_frontend_bundle_copy_is_a_graceful_no_op_since_dist_is_gitignored(self) -> None:
        """`frontend/dist` est exclu de Git (`frontend/.gitignore`) — `git
        archive` ne le voit donc jamais, quel que soit l'état du répertoire
        de travail. Constat documenté, pas un défaut de cette phase : voir
        `runtime_build/README.md` §Limites connues. Ce test vérifie que
        l'absence ne fait pas planter le build (branche `is_dir()` dans
        `build.py`), pas que le bundle est présent."""
        self.assertFalse((self.artifact_dir / "frontend_dist").exists())

    def test_runtime_pyproject_declares_no_dev_or_test_dependency(self) -> None:
        """Vérifie les VALEURS de dépendances déclarées (parsing TOML réel),
        pas un simple grep texte — un commentaire du fichier mentionne ces
        noms d'outils pour expliquer pourquoi ils sont absents, ce qui
        ferait échouer un grep texte naïf sans révéler de vraie fuite."""
        content = (self.artifact_dir / "pyproject.toml").read_text(encoding="utf-8")
        doc = tomllib.loads(content)
        declared = list(doc["project"].get("dependencies", []))
        for extra_deps in doc["project"].get("optional-dependencies", {}).values():
            declared.extend(extra_deps)
        joined = " ".join(declared).lower()
        for forbidden in ("pytest", "ruff", "mypy", "hypothesis", "httpx"):
            self.assertNotIn(forbidden, joined)

    def test_dev_repo_is_untouched_by_the_build(self) -> None:
        """La construction elle-même ne doit introduire AUCUN changement
        dans le dépôt DEV. Ne suppose pas un arbre de travail propre au
        départ (des modifications non committées légitimes — travail des
        phases précédentes — peuvent déjà être présentes) : compare l'état
        `git status` immédiatement avant et après un appel à
        `build_runtime`, sur le même dépôt de travail."""

        def _status() -> str:
            return subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=REPO_ROOT, check=True, capture_output=True, text=True,
            ).stdout

        before = _status()
        with tempfile.TemporaryDirectory() as tmp:
            build_runtime("HEAD", Path(tmp) / "probe-build")
        after = _status()
        self.assertEqual(before, after)


class TestAntiLeakVerification(_BuiltArtifactTestCase):
    def test_clean_artifact_passes(self) -> None:
        report = verify_runtime_artifact(self.artifact_dir)
        self.assertTrue(report.ok, report.render())

    def test_checker_actually_detects_a_planted_research_directory(self) -> None:
        """Preuve que le contrôle n'est pas toujours vert : plante un faux
        `apex.weakness` dans une COPIE de l'artefact et vérifie la
        détection."""
        with tempfile.TemporaryDirectory() as poisoned_tmp:
            poisoned = Path(poisoned_tmp) / "poisoned"
            shutil.copytree(self.artifact_dir, poisoned)
            fake_pkg = poisoned / "apex" / "weakness"
            fake_pkg.mkdir()
            (fake_pkg / "__init__.py").write_text("", encoding="utf-8")
            report = verify_runtime_artifact(poisoned)
            self.assertFalse(report.ok)
            self.assertTrue(any("weakness" in v for v in report.violations))

    def test_checker_detects_a_planted_forbidden_import_without_the_directory(self) -> None:
        """Un import mort vers `apex.metalearner` (recherche) doit être détecté
        même si `apex/metalearner/` lui-même n'est jamais copié — la
        vérification statique des imports ne dépend pas de la vérification
        des répertoires. (`apex.genome`, utilisé ici avant le 06/10/2026, fait
        désormais partie du Runtime.)"""
        with tempfile.TemporaryDirectory() as poisoned_tmp:
            poisoned = Path(poisoned_tmp) / "poisoned"
            shutil.copytree(self.artifact_dir, poisoned)
            sneaky = poisoned / "apex" / "missions" / "_sneaky.py"
            sneaky.write_text("from apex.metalearner import evolution\n", encoding="utf-8")
            report = verify_runtime_artifact(poisoned)
            self.assertFalse(report.ok)
            self.assertTrue(any("apex.metalearner" in v for v in report.violations))


    def test_checker_detects_a_runtime_module_missing_from_the_artifact(self) -> None:
        """Complétude : `apex/discovery.py` (importé paresseusement par `apex serve`) manquait à l'artefact
        sans qu'aucun import de module ne le révèle. Le contrôle doit le voir."""
        with tempfile.TemporaryDirectory() as poisoned_tmp:
            poisoned = Path(poisoned_tmp) / "poisoned"
            shutil.copytree(self.artifact_dir, poisoned)
            (poisoned / "apex" / "discovery.py").unlink()
            report = verify_runtime_artifact(poisoned)
            self.assertFalse(report.ok)
            self.assertTrue(any("apex.discovery" in v and "absent" in v for v in report.violations), report.render())


class TestStandaloneFromTheArtifact(_BuiltArtifactTestCase):
    """L'application Windows (APEX.exe) embarque CET artefact : son lanceur autonome doit y être complet."""

    def test_the_standalone_profile_ships_with_its_configuration(self) -> None:
        self.assertTrue((self.artifact_dir / "apex" / "standalone" / "launcher.py").is_file())
        self.assertTrue((self.artifact_dir / "apex" / "discovery.py").is_file())
        self.assertTrue((self.artifact_dir / "config.standalone.yaml").is_file())

    def test_the_standalone_launcher_runs_from_the_artifact_alone(self) -> None:
        result = subprocess.run([sys.executable, "-m", "apex.standalone", "--help"], cwd=self.artifact_dir,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("serve", result.stdout)
        probe = subprocess.run(
            [sys.executable, "-c", "import apex.standalone.launcher, apex.discovery, apex; print(apex.__file__)"],
            cwd=self.artifact_dir, capture_output=True, text=True, timeout=30)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertTrue(Path(probe.stdout.strip()).is_relative_to(self.artifact_dir.resolve()), probe.stdout)


class TestRuntimeCliFromCleanEnvironment(_BuiltArtifactTestCase):
    """Exécute réellement `apex doctor --json` / `apex gate --json` en
    sous-processus, exécuté depuis `artifact_dir` — le dépôt DEV n'est
    jamais sur le chemin de recherche de modules de ce sous-processus
    (vérifié empiriquement : `apex` y est installé en mode editable et
    apparaît sur `sys.path`, mais APRÈS le répertoire courant — la
    résolution `cwd`-first prend le pas, confirmé par une sonde dédiée
    avant l'écriture de ces tests)."""

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "apex.cli.main", *args],
            cwd=self.artifact_dir, capture_output=True, text=True, timeout=30,
        )

    def test_doctor_json_runs_from_the_artifact_and_matches_the_base_contract(self) -> None:
        """`build_runtime("HEAD", ...)` construit depuis le dernier COMMIT,
        jamais depuis le répertoire de travail (git archive, par
        construction — voir `runtime_build/build.py`). Au moment de
        l'écriture de ce test, l'enrichissement du contrat Doctor (Phase 1 :
        `schema_version`/`apex_version`/`generated_at`) est encore non
        committé — donc absent d'un build depuis HEAD. Ce test vérifie donc
        le socle garanti par N'IMPORTE QUEL commit (`status`/`counts`/
        `duration_ms`/`results`, contrat pré-existant, jamais modifié par
        Phase 1), et vérifie la cohérence du contrat enrichi SEULEMENT s'il
        est déjà présent dans le commit construit — ne suppose jamais un
        état de commit particulier."""
        result = self._run_cli("doctor", "--json")
        payload = json.loads(result.stdout)
        for key in ("status", "counts", "duration_ms", "results"):
            self.assertIn(key, payload)
        if "schema_version" in payload:  # ce commit inclut déjà la Phase 1
            self.assertIn("apex_version", payload)
            self.assertIn("generated_at", payload)

    def test_gate_json_runs_from_the_artifact_and_matches_the_existing_contract(self) -> None:
        """Le contrat Gate est mature et déjà committé de longue date
        (Phase 1 ne l'a pas touché) — ses champs sont donc attendus quel
        que soit le commit construit."""
        result = self._run_cli("gate", "--json")
        payload = json.loads(result.stdout)
        for key in ("schema_version", "apex_version", "generated_at", "gate_status",
                    "phase_2_authorized", "checks"):
            self.assertIn(key, payload)

    def test_version_command_runs_from_the_artifact(self) -> None:
        result = self._run_cli("version", "--json")
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["version"], "0.1.2")

    def test_interfaces_command_runs_from_the_artifact_without_importing_research(self) -> None:
        """`apex interfaces` imprime des chemins de module recherche en
        texte (métadonnées statiques du registre) sans jamais les
        importer — voir runtime_build/README.md. Doit rester vrai depuis
        l'artefact construit."""
        result = self._run_cli("interfaces", "--json")
        self.assertEqual(result.returncode, 0)
        json.loads(result.stdout)  # sortie JSON valide

    def test_models_status_runs_from_the_artifact_and_never_downloads(self) -> None:
        """`apex models status` depuis l'artefact : le modèle natif est listé ; poids absents → dit
        indisponible avec la commande explicite, jamais téléchargé implicitement."""
        with tempfile.TemporaryDirectory() as empty:
            result = subprocess.run(
                [sys.executable, "-m", "apex.cli.main", "models", "status"], cwd=self.artifact_dir,
                capture_output=True, text=True, timeout=60, env={**os.environ, "APEX_MODELS_DIR": empty})
            self.assertEqual(result.returncode, 0, result.stderr[-500:])
            self.assertIn("transformers", result.stdout)
            self.assertIn("apex models fetch", result.stdout)
            self.assertEqual(os.listdir(empty), [])  # rien n'a été écrit (aucun téléchargement)

    def test_apex_module_imported_from_the_artifact_is_not_the_dev_repo_copy(self) -> None:
        probe = "import apex, json; print(json.dumps({'file': apex.__file__}))"
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=self.artifact_dir,
            capture_output=True, text=True, timeout=10,
        )
        payload = json.loads(result.stdout)
        resolved = Path(payload["file"]).resolve()
        self.assertEqual(resolved, (self.artifact_dir / "apex" / "__init__.py").resolve())
        self.assertNotEqual(resolved, (REPO_ROOT / "apex" / "__init__.py").resolve())


class TestAlembicFromCleanEnvironment(_BuiltArtifactTestCase):
    """Vérifie que la chaîne Alembic officielle (copiée telle quelle dans
    l'artefact, voir runtime_build/README.md §1) s'applique correctement
    depuis l'artefact SEUL, et que la présence de tables liées à la
    recherche dans le schéma qu'elle produit (skills, genome_versions,
    tasks, eval_runs) n'entraîne l'import d'AUCUN module recherche."""

    def test_migrations_env_apex_imports_resolve_from_the_artifact_alone_without_research(
        self,
    ) -> None:
        """Analyse statique (AST, pas d'exécution) des imports `apex.*` de
        `migrations/env.py`, puis résolution RÉELLE de chacun depuis un
        sous-processus n'ayant que l'artefact sur son chemin de recherche —
        preuve, pas supposition, qu'Alembic n'a besoin d'aucun module
        recherche pour fonctionner."""
        probe = (
            "import ast, json, importlib\n"
            "tree = ast.parse(open('migrations/env.py').read())\n"
            "names = [n.module for n in ast.walk(tree) "
            "if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith('apex')]\n"
            "for n in names:\n"
            "    importlib.import_module(n)\n"
            "print(json.dumps(names))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=self.artifact_dir,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        imported = json.loads(result.stdout)
        self.assertTrue(imported)
        research_prefixes = tuple(f"apex.{pkg}" for pkg in selection.RESEARCH_PACKAGES)
        for name in imported:
            self.assertFalse(name.startswith(research_prefixes), name)

    def test_alembic_upgrade_head_status_is_explicit_pass_fail_or_skip(self) -> None:
        """`check_alembic_upgrade` retourne toujours l'un de PASS / FAIL /
        SKIP_ENVIRONMENT — jamais un simple booléen qui confondrait
        « non testé » avec « réussi ». Dans ce bac à sable, PostgreSQL
        n'est pas joignable : SKIP_ENVIRONMENT est le résultat honnête,
        PAS une preuve de réussite — ce test échoue explicitement si le
        statut est FAIL (migration cassée sur un Postgres réellement
        joignable), il ne le masque jamais."""
        outcome = check_alembic_upgrade(self.artifact_dir)
        self.assertIn(outcome.status, ("PASS", "FAIL", "SKIP_ENVIRONMENT"))
        self.assertNotEqual(outcome.status, "FAIL", outcome.detail)
        if not support.postgres_testable():
            self.assertEqual(outcome.status, "SKIP_ENVIRONMENT")


class TestReproducibility(unittest.TestCase):
    """Deux builds RELEASE du même SHA propre doivent être identiques —
    pas « semblables ». Compare le contenu fichier par fichier (hash
    SHA-256), pas seulement les noms de fichiers."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        base = Path(cls._tmpdir.name)
        cls.manifest_a = build_runtime("HEAD", base / "build-a")
        cls.manifest_b = build_runtime("HEAD", base / "build-b")
        cls.dir_a = base / "build-a"
        cls.dir_b = base / "build-b"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmpdir.cleanup()

    def test_both_builds_resolve_to_the_same_commit(self) -> None:
        self.assertEqual(self.manifest_a["source_commit"], self.manifest_b["source_commit"])

    def test_content_hash_is_identical_across_two_independent_builds(self) -> None:
        """L'empreinte agrégée (tout l'artefact sauf le manifeste lui-même,
        qui contient `built_at` — seul champ non déterministe) doit être
        strictement égale."""
        self.assertEqual(self.manifest_a["content_hash_sha256"], self.manifest_b["content_hash_sha256"])

    def test_every_individual_file_hash_matches_with_a_readable_diff_on_failure(self) -> None:
        """Redondant avec le hash agrégé par construction, mais produit un
        diff exploitable (fichiers ajoutés/retirés/modifiés) si jamais il
        divergeait — un hash agrégé seul ne dit pas OÙ."""
        detail_a = hash_tree_detailed(self.dir_a, exclude_names=frozenset({"RUNTIME_BUILD_MANIFEST.json"}))
        detail_b = hash_tree_detailed(self.dir_b, exclude_names=frozenset({"RUNTIME_BUILD_MANIFEST.json"}))
        diff = diff_trees(detail_a, detail_b)
        self.assertEqual(diff, {"only_in_a": [], "only_in_b": [], "different": []})

    def test_only_the_built_at_timestamp_may_differ_between_manifests(self) -> None:
        """Le SEUL champ autorisé à différer entre deux manifestes du même
        commit est `built_at` (résolution à la seconde — deux builds
        rapides peuvent légitimement obtenir la même valeur, ce n'est donc
        pas une égalité stricte à un singleton) — toute autre différence
        serait une fuite de non-déterminisme (chemin absolu, ordre,
        métadonnée d'archive)."""
        keys = set(self.manifest_a) | set(self.manifest_b)
        differing = {k for k in keys if self.manifest_a.get(k) != self.manifest_b.get(k)}
        self.assertLessEqual(differing, {"built_at"})

    def test_file_count_and_total_size_are_identical(self) -> None:
        def _stats(root: Path) -> tuple[int, int]:
            files = [p for p in root.rglob("*") if p.is_file() and p.name != "RUNTIME_BUILD_MANIFEST.json"]
            return len(files), sum(p.stat().st_size for p in files)

        self.assertEqual(_stats(self.dir_a), _stats(self.dir_b))


class TestWorktreeValidationBuild(unittest.TestCase):
    """Build B (validation) — depuis l'arbre de travail courant, qui
    contient les Phases 0/1 non committées au moment de cette phase.
    Prouve qu'elles sont compatibles avec le packaging Runtime SANS
    prétendre qu'il s'agit d'une release reproductible depuis un SHA."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.artifact_dir = Path(cls._tmpdir.name) / "apex-runtime-worktree"
        cls.manifest = build_runtime_from_worktree(cls.artifact_dir)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmpdir.cleanup()

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "apex.cli.main", *args],
            cwd=self.artifact_dir, capture_output=True, text=True, timeout=30,
        )

    def test_manifest_is_explicitly_marked_non_release(self) -> None:
        self.assertFalse(self.manifest["release"])
        self.assertEqual(self.manifest["source"], "worktree")
        self.assertEqual(len(self.manifest["head_commit"]), 40)
        self.assertIn("warning", self.manifest)
        self.assertNotIn("source_commit", self.manifest)  # jamais un faux SHA de contenu

    def test_manifest_dirty_flag_matches_actual_repo_state(self) -> None:
        actual_dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=REPO_ROOT,
                check=True, capture_output=True, text=True,
            ).stdout
        )
        self.assertEqual(self.manifest["working_tree_dirty"], actual_dirty)

    def test_anti_leak_still_passes_on_a_worktree_build(self) -> None:
        report = verify_runtime_artifact(self.artifact_dir)
        self.assertTrue(report.ok, report.render())

    def test_phase0_api_auth_module_is_present_in_the_worktree_build(self) -> None:
        """Phase 0 (authentification API) n'est pas encore committée —
        seul un build worktree peut la contenir. Vérifie sa présence SANS
        réécrire son comportement."""
        self.assertTrue((self.artifact_dir / "apex" / "api" / "auth.py").is_file())
        app_source = (self.artifact_dir / "apex" / "api" / "app.py").read_text(encoding="utf-8")
        self.assertIn("require_api_token", app_source)

    def test_doctor_json_from_worktree_build_includes_the_phase1_contract_fields(self) -> None:
        """Phase 1 (contrat JSON Doctor enrichi) n'est pas encore
        committée — un build depuis HEAD seul ne les contient pas (voir
        TestRuntimeCliFromCleanEnvironment), un build worktree si."""
        result = self._run_cli("doctor", "--json")
        payload = json.loads(result.stdout)
        for key in ("schema_version", "apex_version", "generated_at", "status", "counts",
                    "duration_ms", "results"):
            self.assertIn(key, payload)
        self.assertEqual(payload["schema_version"], 1)

    def test_gate_json_from_worktree_build_matches_the_unmodified_contract(self) -> None:
        result = self._run_cli("gate", "--json")
        payload = json.loads(result.stdout)
        for key in ("schema_version", "apex_version", "generated_at", "gate_status",
                    "phase_2_authorized", "checks"):
            self.assertIn(key, payload)

    def test_api_module_importable_from_the_worktree_build_with_auth_wired(self) -> None:
        """Import réel de `apex.api.app` depuis l'artefact worktree seul —
        preuve que Phase 0 s'intègre sans erreur, pas une simple lecture
        de fichier."""
        probe = (
            "import apex.api.app as app_module\n"
            "import inspect, json\n"
            "src = inspect.getsource(app_module)\n"
            "print(json.dumps({'has_auth_dependency': 'require_api_token' in src}))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=self.artifact_dir,
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["has_auth_dependency"])


if __name__ == "__main__":
    unittest.main()

"""Tests de non-régression pour `tests/conftest.py` — le fix `APEX_DATA_ROOT`
(N+26). Teste les fonctions du hook directement (elles ne prennent aucune
dépendance à un vrai objet `pytest.Config`, seulement `os.environ`) plutôt
que de faire tourner un second processus pytest imbriqué — plus rapide,
tout aussi réel : ce sont exactement les mêmes fonctions que pytest invoque.

**Important** : ce fichier s'exécute lui-même SOUS un `pytest` dont le VRAI
`conftest.py` a déjà tourné (`APEX_DATA_ROOT` est donc déjà fixé dans
`os.environ` au moment où ces tests démarrent) — chaque test sauvegarde et
restaure explicitement `os.environ["APEX_DATA_ROOT"]` pour ne jamais
perturber le reste de la suite qui s'exécute après lui."""

from __future__ import annotations

import os
import unittest

from tests import conftest as _conftest


class TestDataRootIsolationHook(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = os.environ.get("APEX_DATA_ROOT")
        self._saved_created = _conftest._created_data_root
        if "APEX_DATA_ROOT" in os.environ:
            del os.environ["APEX_DATA_ROOT"]
        _conftest._created_data_root = None

    def tearDown(self) -> None:
        # Nettoyage propre de tout répertoire créé PAR CE TEST, avant de
        # restaurer l'état réel de la session (celui posé par le vrai hook).
        _conftest.pytest_unconfigure(config=None)
        if self._saved_env is not None:
            os.environ["APEX_DATA_ROOT"] = self._saved_env
        else:
            os.environ.pop("APEX_DATA_ROOT", None)
        _conftest._created_data_root = self._saved_created

    def test_configure_creates_a_real_writable_temp_directory(self) -> None:
        _conftest.pytest_configure(config=None)
        data_root = os.environ.get("APEX_DATA_ROOT")
        self.assertIsNotNone(data_root)
        self.assertTrue(os.path.isdir(data_root))
        probe = os.path.join(data_root, "probe.txt")
        with open(probe, "w") as fh:
            fh.write("x")
        self.assertTrue(os.path.exists(probe))

    def test_configure_never_overwrites_an_already_set_value(self) -> None:
        os.environ["APEX_DATA_ROOT"] = "/some/explicit/path"
        _conftest.pytest_configure(config=None)
        self.assertEqual(os.environ["APEX_DATA_ROOT"], "/some/explicit/path")
        self.assertIsNone(_conftest._created_data_root, "aucun répertoire ne doit être créé si déjà défini")

    def test_unconfigure_removes_the_created_directory_and_env_var(self) -> None:
        _conftest.pytest_configure(config=None)
        data_root = os.environ["APEX_DATA_ROOT"]
        _conftest.pytest_unconfigure(config=None)
        self.assertFalse(os.path.exists(data_root))
        self.assertNotIn("APEX_DATA_ROOT", os.environ)

    def test_unconfigure_never_removes_an_externally_set_value(self) -> None:
        """Le hook ne doit jamais supprimer une variable qu'il n'a pas
        lui-même posée — même garantie que « jamais écraser .env
        aveuglément » appliquée au nettoyage."""
        os.environ["APEX_DATA_ROOT"] = "/some/explicit/path"
        _conftest.pytest_configure(config=None)  # no-op : déjà défini
        _conftest.pytest_unconfigure(config=None)
        self.assertEqual(os.environ.get("APEX_DATA_ROOT"), "/some/explicit/path")

    def test_load_config_resolves_data_root_to_the_created_directory(self) -> None:
        """Preuve bout-en-bout : `apex.config.loader.load_config()` (le
        chemin RÉEL emprunté par les ~20 sites d'appel identifiés) résout
        bien `paths.data_root` vers le répertoire créé par ce hook — jamais
        vers le défaut littéral `/home/apex/apex-data`."""
        _conftest.pytest_configure(config=None)
        data_root = os.environ["APEX_DATA_ROOT"]

        from apex.config.loader import load_config

        config = load_config()
        self.assertEqual(config.paths.data_root, data_root)
        self.assertNotEqual(config.paths.data_root, "/home/apex/apex-data")


if __name__ == "__main__":
    unittest.main()

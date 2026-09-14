"""Tests d'`apex.config.dotenv` — corrige la cause racine d'un bug réel :
`.env` contenait `APEX_GPU_VRAM_GB=8` (vérifié directement par
l'utilisateur), mais `apex doctor` continuait de voir `24` car AUCUN code
d'APEX ne chargeait jamais `.env` dans `os.environ` (`docker compose` le
fait pour les conteneurs, pas les process Python `apex doctor`/`apex gate`/
`apex serve`). `TestApplyDotenvReproducesAndFixesTheRealBug` reproduit
exactement le scénario rapporté."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from apex.cli.main import main as cli_main
from apex.config.dotenv import apply_dotenv, load_dotenv_file, parse_dotenv
from apex.config.loader import load_config


class TestParseDotenv(unittest.TestCase):
    def test_a_simple_key_value(self) -> None:
        self.assertEqual(parse_dotenv("FOO=bar\n"), {"FOO": "bar"})

    def test_b_blank_lines_and_comments_skipped(self) -> None:
        text = "\n# un commentaire\nFOO=bar\n   # indenté aussi\nBAZ=qux\n\n"
        self.assertEqual(parse_dotenv(text), {"FOO": "bar", "BAZ": "qux"})

    def test_c_double_and_single_quotes_are_stripped(self) -> None:
        text = 'A="hello world"\nB=\'single quoted\'\n'
        self.assertEqual(parse_dotenv(text), {"A": "hello world", "B": "single quoted"})

    def test_d_export_prefix_is_tolerated(self) -> None:
        self.assertEqual(parse_dotenv("export FOO=bar\n"), {"FOO": "bar"})

    def test_e_value_with_spaces_no_quotes_is_trailing_stripped(self) -> None:
        self.assertEqual(parse_dotenv("FOO=NVIDIA GeForce RTX 4060 Ti  \n"), {"FOO": "NVIDIA GeForce RTX 4060 Ti"})

    def test_f_the_exact_real_env_file_reported(self) -> None:
        """Le contenu RÉEL rapporté (mission « Correction du doctor »)."""
        text = (
            'APEX_GPU_MODEL="NVIDIA GeForce RTX 4060 Ti"\n'
            "APEX_GPU_VRAM_GB=8\n"
            "APEX_CPU_CORES=12\n"
            "APEX_RAM_GB=48\n"
        )
        parsed = parse_dotenv(text)
        self.assertEqual(parsed["APEX_GPU_MODEL"], "NVIDIA GeForce RTX 4060 Ti")
        self.assertEqual(parsed["APEX_GPU_VRAM_GB"], "8")

    def test_g_malformed_lines_are_silently_skipped(self) -> None:
        self.assertEqual(parse_dotenv("this is not a valid line\nFOO=bar\n"), {"FOO": "bar"})


class TestLoadDotenvFile(unittest.TestCase):
    def test_a_missing_file_returns_none(self) -> None:
        self.assertIsNone(load_dotenv_file("/nonexistent/path/.env"))

    def test_b_existing_file_is_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            path.write_text("FOO=bar\n", encoding="utf-8")
            self.assertEqual(load_dotenv_file(path), {"FOO": "bar"})


class TestApplyDotenv(unittest.TestCase):
    def test_a_missing_file_applies_nothing(self) -> None:
        environ: dict[str, str] = {}
        applied = apply_dotenv("/nonexistent/path/.env", environ=environ)
        self.assertEqual(applied, ())
        self.assertEqual(environ, {})

    def test_b_applies_into_the_given_environ_never_the_real_process_one(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            path.write_text("FOO=bar\n", encoding="utf-8")
            environ: dict[str, str] = {}
            applied = apply_dotenv(path, environ=environ)
            self.assertEqual(applied, ("FOO",))
            self.assertEqual(environ, {"FOO": "bar"})

    def test_c_an_already_exported_value_is_never_overridden(self) -> None:
        """Priorité : une variable RÉELLEMENT exportée par le shell (déjà
        présente dans `environ`) doit toujours gagner sur `.env`."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            path.write_text("FOO=from_dotenv\n", encoding="utf-8")
            environ = {"FOO": "from_real_shell_export"}
            applied = apply_dotenv(path, environ=environ)
            self.assertEqual(applied, ())
            self.assertEqual(environ["FOO"], "from_real_shell_export")

    def test_d_only_newly_applied_keys_are_returned(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            path.write_text("A=1\nB=2\n", encoding="utf-8")
            environ = {"A": "already-set"}
            applied = apply_dotenv(path, environ=environ)
            self.assertEqual(applied, ("B",))
            self.assertEqual(environ, {"A": "already-set", "B": "2"})


class TestApplyDotenvReproducesAndFixesTheRealBug(unittest.TestCase):
    """Reproduit EXACTEMENT le scénario rapporté : `.env` contient
    `APEX_GPU_VRAM_GB=8`, mais sans ce chargement, `load_config()` (utilisé
    tel quel par `apex doctor`) ne le voit jamais et retombe sur le défaut
    `24` codé dans `config.yaml` (`${APEX_GPU_VRAM_GB:-24}`)."""

    def test_a_without_loading_dotenv_the_default_24gb_leaks_through(self) -> None:
        """Preuve que le bug existe bel et bien SANS le correctif : un
        `environ` vide (aucune variable réellement exportée, exactement la
        situation d'un `.env` jamais chargé) retombe sur le défaut."""
        config = load_config(environ={"POSTGRES_PASSWORD": "x", "MINIO_ROOT_PASSWORD": "x"})
        declared = max(p.total_vram_gb for p in config.pools.values())
        self.assertEqual(declared, 24.0)

    def test_b_with_the_real_reported_dotenv_content_declared_vram_is_8(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            path.write_text(
                'APEX_GPU_MODEL="NVIDIA GeForce RTX 4060 Ti"\n'
                "APEX_GPU_VRAM_GB=8\n"
                "APEX_CPU_CORES=12\n"
                "APEX_RAM_GB=48\n"
                "POSTGRES_PASSWORD=x\n"
                "MINIO_ROOT_PASSWORD=x\n",
                encoding="utf-8",
            )
            environ: dict[str, str] = {}
            apply_dotenv(path, environ=environ)
            config = load_config(environ=environ)
            declared = max(p.total_vram_gb for p in config.pools.values())
            self.assertEqual(declared, 8.0)
            gpu_model = next(iter(config.pools.values())).gpus[0].model
            self.assertEqual(gpu_model, "NVIDIA GeForce RTX 4060 Ti")


class TestCliMainLoadsDotenvBeforeAnyCommand(unittest.TestCase):
    """Bout en bout RÉEL : `apex.cli.main.main()` — jamais un appel direct à
    `apply_dotenv`/`load_config` — doit charger `.env` AVANT `apex config`.
    `APEX_ENV_FILE` pointe vers un fichier temporaire pour ne jamais dépendre
    d'un `.env` réel à la racine du dépôt (absent dans ce bac à sable,
    gitignored) ; nettoyage explicite de `os.environ` après coup — `main()`
    mute le VRAI `os.environ` (`setdefault`), jamais un environ de test."""

    def setUp(self) -> None:
        self._original_environ = dict(os.environ)

    def tearDown(self) -> None:
        for key in list(os.environ):
            if key not in self._original_environ:
                del os.environ[key]
        os.environ.update(self._original_environ)

    def test_a_apex_config_reflects_the_real_reported_dotenv_content(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            path.write_text(
                'APEX_GPU_MODEL="NVIDIA GeForce RTX 4060 Ti"\n'
                "APEX_GPU_VRAM_GB=8\n"
                "APEX_CPU_CORES=12\n"
                "APEX_RAM_GB=48\n"
                "POSTGRES_PASSWORD=x\n"
                "MINIO_ROOT_PASSWORD=x\n",
                encoding="utf-8",
            )
            os.environ["APEX_ENV_FILE"] = str(path)
            out = io.StringIO()
            with redirect_stdout(out):
                code = cli_main(["config"])
            self.assertEqual(code, 0)
            payload = json.loads(out.getvalue())
            declared_vram = payload["pools"]["local-gpu"]["vram_gb"]
            self.assertEqual(declared_vram, 8.0, f"payload complet : {payload}")


if __name__ == "__main__":
    unittest.main()

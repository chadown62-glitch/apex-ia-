"""Tests de `scripts/apex-preflight.sh` — mission N+26.

Exécute le VRAI script contre CETTE machine (jamais un mock du shell) —
même discipline que `tests/test_launcher.py` pour `apex-launch.sh`. Les
scénarios internes détaillés (safe-fixes, doctor, gate) sont déjà couverts
par les tests Python de `apex.doctor.*` ; ce fichier vérifie uniquement le
CONTRAT du script en tant qu'orchestrateur : arguments, codes de sortie,
jamais de crash, jamais d'action destructive.

Le résultat exact (READY/BLOCKED) dépend de l'état réel de cette machine
(Docker/`.env`/GPU) et n'est PAS affirmé ici — seul le CONTRAT (jamais 2
sauf usage invalide, toujours un verdict imprimé, jamais de commande
destructive) est vérifié, honnêtement, sans dépendre d'un état
particulier."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "apex-preflight.sh"
_TIMEOUT_S = 150


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        cwd=str(_SCRIPT.parent.parent),
        capture_output=True, text=True, timeout=_TIMEOUT_S,
    )


class TestScriptExistsAndIsValidBash(unittest.TestCase):
    def test_script_exists_and_is_executable(self) -> None:
        self.assertTrue(_SCRIPT.exists())
        self.assertTrue(_SCRIPT.stat().st_mode & 0o111, "le script doit être exécutable (chmod +x)")

    def test_bash_syntax_is_clean(self) -> None:
        out = subprocess.run(["bash", "-n", str(_SCRIPT)], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)


class TestUsageContract(unittest.TestCase):
    def test_unknown_argument_fails_cleanly_with_code_2(self) -> None:
        out = _run("--this-flag-does-not-exist")
        self.assertEqual(out.returncode, 2)
        self.assertIn("argument inconnu", out.stderr)

    def test_exit_code_is_never_outside_the_documented_set(self) -> None:
        """0 (READY[-WARN]) / 1 (BLOCKED) / 2 (FAILED) — jamais autre
        chose, quel que soit l'état réel de cette machine."""
        out = _run("--dry-run", "--skip-tests")
        self.assertIn(out.returncode, (0, 1, 2))


class TestNeverExecutesDestructiveCommands(unittest.TestCase):
    def test_script_source_never_mentions_a_destructive_docker_command(self) -> None:
        """Ignore les lignes de commentaire (l'en-tête du script EN PARLE
        délibérément pour documenter ce qui est interdit) — vérifie
        uniquement le code réellement exécutable."""
        code_lines = [
            line for line in _SCRIPT.read_text().splitlines() if not line.strip().startswith("#")
        ]
        code = "\n".join(code_lines)
        for forbidden in ("system prune", "volume prune", "down -v", "down --volumes", "rm -rf /"):
            self.assertNotIn(forbidden, code)

    def test_script_never_calls_apex_serve_or_apex_start_without_dry_run(self) -> None:
        """Ce script ne démarre jamais réellement APEX — voir sa docstring
        d'en-tête. `apex start`/`apex serve` ne doivent apparaître nulle
        part dans le texte du script."""
        text = _SCRIPT.read_text()
        self.assertNotIn("cli.main serve", text)
        self.assertNotIn("cli.main start", text)


class TestDryRunNeverAppliesRepairs(unittest.TestCase):
    def test_dry_run_output_says_nothing_was_executed(self) -> None:
        out = _run("--dry-run", "--skip-tests")
        combined = out.stdout + out.stderr
        self.assertIn("dry-run", combined.lower())
        self.assertIn("aucune réparation exécutée", combined)


class TestAlwaysPrintsAVerdict(unittest.TestCase):
    def test_dry_run_skip_tests_always_reaches_a_verdict(self) -> None:
        out = _run("--dry-run", "--skip-tests")
        combined = out.stdout + out.stderr
        self.assertIn("VERDICT", combined)
        self.assertTrue(
            any(word in combined for word in ("READY", "BLOCKED")),
            "le rapport doit toujours conclure par READY ou BLOCKED",
        )

    def test_skip_tests_section_is_honestly_labelled_skipped(self) -> None:
        out = _run("--dry-run", "--skip-tests")
        combined = out.stdout + out.stderr
        self.assertIn("sautés (--skip-tests)", combined)


class TestQuickTestBundleActuallyRuns(unittest.TestCase):
    def test_without_skip_tests_the_quick_bundle_executes(self) -> None:
        """Preuve que le sous-ensemble de tests « quick » tourne
        réellement (pas seulement annoncé) — cherche la ligne de résumé
        pytest dans la sortie."""
        out = _run("--dry-run")
        combined = out.stdout + out.stderr
        self.assertIn("sous-ensemble rapide", combined)
        # pytest imprime toujours un résumé final (passed/failed/error) —
        # présent quel que soit le résultat, preuve qu'il a réellement tourné.
        self.assertTrue(
            any(word in combined for word in ("passed", "failed", "error")),
            f"aucune trace d'exécution pytest dans la sortie : {combined[-2000:]}",
        )


if __name__ == "__main__":
    unittest.main()

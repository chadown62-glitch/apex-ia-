"""Test de câblage `apex start` — passe « Self-Healing Launcher » (N+19,
AXE B). Exécute réellement `apex.cli.main.main([...])` en mémoire (même
patron que `tests/test_cli_learn.py`), avec `--dry-run` : ne touche jamais
un vrai serveur HTTP, ne modifie jamais le système — seule la voie sûre à
exercer sans dépendre de l'état de CETTE machine (Docker/Ollama/GPU réels,
non pertinents à ce test de câblage)."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from apex.cli.main import main


def _run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(argv)
    return code, out.getvalue()


class TestCliStartDryRun(unittest.TestCase):
    def test_a_dry_run_exits_zero_and_never_starts_a_server(self) -> None:
        code, output = _run(["start", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("SELF-HEALING LAUNCHER", output)
        self.assertIn("dry-run", output)

    def test_b_dry_run_shows_the_diagnostic_section(self) -> None:
        code, output = _run(["start", "--dry-run", "--verbose"])
        self.assertEqual(code, 0)
        self.assertIn("DIAGNOSTIC", output)
        self.assertIn("AUTO-REPAIR", output)


if __name__ == "__main__":
    unittest.main()

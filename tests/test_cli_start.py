"""Test de câblage `apex start` — passe « Self-Healing Launcher » (N+19,
AXE B) et sa correction « Gate authorization wiring » (N+27 ter). Exécute
réellement `apex.cli.main.main([...])` en mémoire (même patron que
`tests/test_cli_learn.py`), avec `--dry-run` : ne touche jamais un vrai
serveur HTTP, ne modifie jamais le système — seule la voie sûre à exercer
sans dépendre de l'état de CETTE machine (Docker/Ollama/GPU réels, non
pertinents à ce test de câblage).

Les tests qui exercent le chemin non-dry-run patchent `apex.gate.runner.
run_gate` (jamais un vrai Docker/Ollama sondé par un test de câblage CLI)
et `apex.gate.report.EnvironmentReport.write` (jamais de fichier parasite
écrit dans le repo par une suite de tests)."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from apex.cli.main import build_parser, main
from apex.doctor.runner import DoctorReport
from apex.gate.report import EnvironmentReport


def _run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(argv)
    return code, out.getvalue()


def _fake_report(*, authorized: bool) -> EnvironmentReport:
    """`checks=()` -> `blocking`/`unresolved` vides -> `phase_2_authorized`
    ne dépend plus que de `probe_is_real` (voir `EnvironmentReport.
    phase_2_authorized`) — un rapport réel et structurellement valide,
    jamais un double approximatif de sa forme."""
    return EnvironmentReport(checks=(), probe_is_real=authorized)


def _fake_healthy_doctor_report(ctx=None, checks=None) -> DoctorReport:
    """Résultats vides -> `DoctorReport.worst` == `Severity.SKIP` (jamais
    `FAIL`) -> `run_preflight_and_repair` ne bloque jamais sur le doctor et
    atteint bien Gate — l'état RÉEL de Docker/Postgres/Redis/Ollama sur LA
    machine qui exécute ces tests (variable, non pertinent à un test de
    câblage CLI) ne doit jamais déterminer si ce test passe."""
    return DoctorReport(results=(), duration_ms=0.0)


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

    def test_c_dry_run_with_allow_flags_still_never_starts_a_server(self) -> None:
        """`--dry-run` reste prioritaire : même avec les deux autorisations
        explicites, `DRY_RUN` s'arrête avant tout appel Gate réel (voir
        `run_preflight_and_repair` : Gate n'est jamais sollicité en
        dry-run) — jamais de démarrage de serveur."""
        code, output = _run(
            ["start", "--dry-run", "--allow-container-exec", "--allow-network-probe"]
        )
        self.assertEqual(code, 0)
        self.assertIn("dry-run", output)


class TestCliStartAllowFlagsParsing(unittest.TestCase):
    """Câblage pur argparse — jamais dépendant de Docker/Ollama/GPU réels :
    ce que `main(["start", ...])` construit comme `argparse.Namespace`
    avant même d'atteindre `_cmd_start`."""

    def test_a_no_flags_means_both_authorizations_stay_false(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["start", "--dry-run"])
        self.assertFalse(args.allow_container_exec)
        self.assertFalse(args.allow_network_probe)

    def test_b_flags_are_parsed_to_true_when_passed(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["start", "--dry-run", "--allow-container-exec", "--allow-network-probe"]
        )
        self.assertTrue(args.allow_container_exec)
        self.assertTrue(args.allow_network_probe)

    def test_c_apex_gate_default_still_false_without_flags(self) -> None:
        """Non-régression : `apex gate` (déjà existant) garde son propre
        comportement par défaut — cette mission ne touche pas son parser."""
        parser = build_parser()
        args = parser.parse_args(["gate"])
        self.assertFalse(args.allow_container_exec)
        self.assertFalse(args.allow_network_probe)


class TestCliStartPropagatesGateKwargs(unittest.TestCase):
    """`_cmd_start` doit transmettre les autorisations demandées à
    `run_preflight_and_repair(..., gate_kwargs={...})`, qui les relaie tel
    quel à `apex.gate.runner.run_gate` — jamais une politique
    d'autorisation dupliquée dans `_cmd_start` lui-même."""

    def setUp(self) -> None:
        self._write_patch = patch.object(EnvironmentReport, "write", lambda self, path=None: None)
        self._write_patch.start()
        self.addCleanup(self._write_patch.stop)
        self._doctor_patch = patch(
            "apex.doctor.launcher.run_checks", side_effect=_fake_healthy_doctor_report,
        )
        self._doctor_patch.start()
        self.addCleanup(self._doctor_patch.stop)
        # `_cmd_start` passe `history_path=DEFAULT_HISTORY_PATH` (un chemin
        # relatif, `state/startup_history.jsonl`) — jamais de fichier
        # parasite écrit dans le repo par un test de câblage CLI.
        self._history_patch = patch("apex.doctor.launcher.append_record", lambda *a, **k: None)
        self._history_patch.start()
        self.addCleanup(self._history_patch.stop)

    def test_a_without_flags_run_gate_receives_false_false_on_the_only_call_reached(self) -> None:
        """Sans flags, la première sonde Gate (`run_preflight_and_repair`)
        refuse déjà (`probe_is_real=False` -> `phase_2_authorized=False`) —
        `_cmd_serve` n'est jamais atteint, exactement le comportement
        sécurisé attendu par défaut."""
        calls: list[dict] = []

        def fake_run_gate(**kwargs):
            calls.append(kwargs)
            return _fake_report(authorized=False)

        with patch("apex.gate.runner.run_gate", side_effect=fake_run_gate):
            code, _ = _run(["start"])
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["allow_container_exec"])
        self.assertFalse(calls[0]["allow_network_probe"])

    def test_b_with_flags_run_gate_receives_true_true_on_both_calls(self) -> None:
        """Le coeur de la mission : `apex start --allow-container-exec
        --allow-network-probe` doit transmettre les MÊMES autorisations que
        `apex gate` avec les mêmes flags — sur les DEUX appels réels à Gate
        (le premier via `run_preflight_and_repair`, le second via
        `_cmd_serve`), jamais seulement le premier. Le premier appel
        autorise (pour que `_cmd_start` atteigne bien `_cmd_serve`) ; le
        second refuse délibérément — uniquement pour observer ses kwargs
        sans jamais démarrer un vrai serveur uvicorn."""
        calls: list[dict] = []

        def fake_run_gate(**kwargs):
            calls.append(kwargs)
            return _fake_report(authorized=len(calls) == 1)

        with patch("apex.gate.runner.run_gate", side_effect=fake_run_gate):
            code, output = _run(["start", "--allow-container-exec", "--allow-network-probe"])

        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertTrue(call["allow_container_exec"])
            self.assertTrue(call["allow_network_probe"])
        # Le premier appel autorise -> `_cmd_start` imprime READY_TO_SERVE
        # et atteint bien `_cmd_serve` ; le second refuse délibérément ->
        # `_cmd_serve` retourne 1 (message d'erreur sur stderr, non
        # capturé ici) sans jamais démarrer de vrai serveur uvicorn.
        self.assertIn("READY_TO_SERVE", output)
        self.assertEqual(code, 1)

    def test_c_gate_refusal_still_blocks_even_with_flags_requested(self) -> None:
        """Les autorisations explicites élargissent ce que Gate peut sonder
        — elles ne forcent jamais son verdict : Gate refuse toujours si les
        contrôles échouent, flags ou pas."""
        calls: list[dict] = []

        def fake_run_gate(**kwargs):
            calls.append(kwargs)
            return _fake_report(authorized=False)

        with patch("apex.gate.runner.run_gate", side_effect=fake_run_gate):
            code, output = _run(["start", "--allow-container-exec", "--allow-network-probe"])
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED_GATE", output)
        # Un seul appel réel : `run_preflight_and_repair` s'arrête à
        # BLOCKED_GATE, `_cmd_serve` n'est jamais atteint.
        self.assertEqual(len(calls), 1)


class TestCliServeDirectInvocationUnaffected(unittest.TestCase):
    """`apex serve` invoqué directement (jamais via `start`) ne déclare pas
    `allow_container_exec`/`allow_network_probe` sur son propre parser —
    `_cmd_serve` doit donc retomber sur `False`/`False`, exactement le
    comportement d'avant cette mission."""

    def setUp(self) -> None:
        self._write_patch = patch.object(EnvironmentReport, "write", lambda self, path=None: None)
        self._write_patch.start()
        self.addCleanup(self._write_patch.stop)

    def test_a_serve_without_start_still_requests_no_probes(self) -> None:
        calls: list[dict] = []

        def fake_run_gate(**kwargs):
            calls.append(kwargs)
            return _fake_report(authorized=False)

        with patch("apex.gate.runner.run_gate", side_effect=fake_run_gate):
            code, _ = _run(["serve"])
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["allow_container_exec"])
        self.assertFalse(calls[0]["allow_network_probe"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()

"""Tests des ajouts « doctor/preflight » de la mission N+26 :
- `apex.doctor.checks.environment.check_data_root` (+ son safe-fix)
- `apex.doctor.checks.sandbox.check_sandbox_selection` (+ `FULL_CHECKS`)
- `apex.util.probe.SystemProbe.is_writable_dir` (réel, pas de mock)

Suit exactement le patron déjà établi par `tests/test_doctor.py`
(`FakeProbe` hors ligne) et `tests/test_doctor_repair.py` (classification
pure)."""

from __future__ import annotations

import os
import tempfile
import unittest

from apex.config.loader import parse_config
from apex.doctor.check import CheckContext, CheckResult, Severity
from apex.doctor.checks import environment, sandbox
from apex.doctor.repair import RepairAction, classify
from apex.doctor.runner import ALL_CHECKS, FULL_CHECKS
from apex.util.probe import FakeProbe, SystemProbe
from tests.test_config import config as base_config


def context(probe: FakeProbe, cfg_overrides: dict | None = None) -> CheckContext:
    document = base_config()
    if cfg_overrides:
        document.update(cfg_overrides)
    return CheckContext(probe=probe, config=parse_config(document), timeout_s=1.0)


class TestCheckDataRoot(unittest.TestCase):
    def test_missing_directory_is_warn_never_fail(self) -> None:
        """Un `data_root` pas encore créé est l'état ATTENDU sur une
        première installation — jamais bloquant."""
        probe = FakeProbe()
        r = environment.check_data_root(context(probe, {"paths": {**base_config()["paths"], "data_root": "/nope"}}))
        self.assertIs(r.severity, Severity.WARN)
        self.assertFalse(r.facts["exists"])
        self.assertIn("mkdir -p", r.remediation)

    def test_existing_writable_directory_passes(self) -> None:
        probe = FakeProbe(paths={"/home/apex/data"}, writable_dirs={"/home/apex/data"})
        r = environment.check_data_root(
            context(probe, {"paths": {**base_config()["paths"], "data_root": "/home/apex/data"}})
        )
        self.assertIs(r.severity, Severity.PASS)
        self.assertTrue(r.facts["writable"])

    def test_existing_non_writable_directory_fails(self) -> None:
        probe = FakeProbe(paths={"/home/apex/data"})  # exists, mais absent de writable_dirs
        r = environment.check_data_root(
            context(probe, {"paths": {**base_config()["paths"], "data_root": "/home/apex/data"}})
        )
        self.assertIs(r.severity, Severity.FAIL)
        self.assertFalse(r.facts["writable"])

    def test_no_config_is_skipped(self) -> None:
        ctx = CheckContext(probe=FakeProbe(), config=None, timeout_s=1.0)
        self.assertIs(environment.check_data_root(ctx).severity, Severity.SKIP)

    def test_registered_in_environment_checks(self) -> None:
        names = [name for name, _ in environment.CHECKS]
        self.assertIn("fs.data_root", names)


class TestSystemProbeIsWritableDirReal(unittest.TestCase):
    """Seul test de ce fichier contre le VRAI système de fichiers — même
    discipline que le reste du dépôt pour une primitive OS élémentaire."""

    def test_a_real_temp_directory_is_writable(self) -> None:
        probe = SystemProbe()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(probe.is_writable_dir(tmp))

    def test_a_nonexistent_path_is_not_writable(self) -> None:
        probe = SystemProbe()
        self.assertFalse(probe.is_writable_dir("/this/path/does/not/exist/at/all"))

    def test_a_plain_file_is_not_a_writable_dir(self) -> None:
        probe = SystemProbe()
        with tempfile.NamedTemporaryFile() as fh:
            self.assertFalse(probe.is_writable_dir(fh.name))

    def test_a_read_only_directory_is_not_writable(self) -> None:
        probe = SystemProbe()
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o500)
            try:
                if os.geteuid() == 0:
                    self.skipTest("root ignore les permissions d'écriture (environnement CI en root)")
                self.assertFalse(probe.is_writable_dir(tmp))
            finally:
                os.chmod(tmp, 0o700)


class TestDataRootSafeFix(unittest.TestCase):
    def _missing_data_root_result(self, path: str = "/home/apex/apex-data") -> CheckResult:
        return CheckResult(
            name="fs.data_root", category="environnement", severity=Severity.WARN,
            message=f"répertoire de données absent : {path}", remediation="mkdir -p " + path,
            facts={"path": path, "exists": False},
        )

    def test_missing_data_root_is_a_safe_mkdir_p(self) -> None:
        plan = classify(self._missing_data_root_result("/home/apex/apex-data"))
        self.assertIs(plan.action, RepairAction.SAFE_AUTO_FIX)
        self.assertEqual(plan.command, ("mkdir", "-p", "/home/apex/apex-data"))

    def test_existing_but_unwritable_data_root_is_never_auto_fixed(self) -> None:
        """`mkdir -p` sur un répertoire déjà existant ne corrige rien —
        jamais prétendre réparer un problème de permissions tout seul."""
        result = CheckResult(
            name="fs.data_root", category="environnement", severity=Severity.FAIL,
            message="non accessible en écriture", remediation="chown/chmod",
            facts={"path": "/home/apex/apex-data", "exists": True, "writable": False},
        )
        plan = classify(result)
        self.assertIs(plan.action, RepairAction.MANUAL_REQUIRED)

    def test_never_produces_a_destructive_command(self) -> None:
        plan = classify(self._missing_data_root_result())
        joined = " ".join(plan.command or ())
        for forbidden in ("rm ", "prune", "-rf", "--force", "delete"):
            self.assertNotIn(forbidden, joined)


class TestSandboxSelectionCheck(unittest.TestCase):
    def test_check_runs_and_returns_a_real_result(self) -> None:
        """Sonde RÉELLE (namespaces sur cette machine) — pas de mock,
        même discipline que le reste des tests sandbox du dépôt."""
        ctx = context(FakeProbe())
        r = sandbox.check_sandbox_selection(ctx)
        self.assertIn(r.severity, (Severity.PASS, Severity.FAIL))
        self.assertIn("provider_name", r.facts)
        self.assertIn("reason", r.facts)
        self.assertIn("isolation_level", r.facts)

    def test_registered_in_full_checks_but_not_in_default_checks(self) -> None:
        """La sonde est coûteuse (sous-processus réels) — jamais dans le
        diagnostic rapide par défaut, seulement `--full`."""
        default_names = [name for name, _ in ALL_CHECKS]
        full_names = [name for name, _ in FULL_CHECKS]
        self.assertNotIn("sandbox.provider", default_names)
        self.assertIn("sandbox.provider", full_names)
        # FULL_CHECKS est une extension additive — jamais un jeu de checks
        # différent qui omettrait silencieusement une vérification rapide.
        self.assertEqual(set(default_names) - {"sandbox.provider"}, set(default_names))
        self.assertTrue(set(default_names).issubset(set(full_names)))


class TestDoctorCliFullFlag(unittest.TestCase):
    def test_full_flag_is_registered_and_defaults_to_false(self) -> None:
        from apex.cli.main import build_parser

        parser = build_parser()
        args = parser.parse_args(["doctor"])
        self.assertFalse(args.full)
        args_full = parser.parse_args(["doctor", "--full"])
        self.assertTrue(args_full.full)


if __name__ == "__main__":
    unittest.main()

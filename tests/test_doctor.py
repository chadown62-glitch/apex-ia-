"""Tests de `apex doctor`, entièrement hors ligne grâce à FakeProbe.

Chaque scénario est une machine simulée : WSL2 sain, WSL2 mal configuré,
absence de GPU, isolation insuffisante. C'est ce qui permet de tester des
comportements qu'on ne peut pas provoquer sur la machine de développement.
"""

from __future__ import annotations

import unittest

from apex.config.loader import parse_config
from apex.doctor.check import CheckContext, Severity
from apex.doctor.checks import environment, platform, services
from apex.doctor.runner import render_text, run_checks
from apex.util.probe import CommandResult, FakeProbe, HttpResult
from tests.test_config import config as base_config


def healthy_probe() -> FakeProbe:
    """Machine WSL2 correctement configurée."""
    return FakeProbe(
        programs={"docker": "/usr/bin/docker", "nvidia-smi": "/usr/lib/wsl/lib/nvidia-smi"},
        commands={
            ("docker", "info", "--format", "{{.ServerVersion}}"):
                CommandResult(("docker",), 0, "27.1.1\n", ""),
            ("docker", "context", "show"):
                CommandResult(("docker",), 0, "default\n", ""),
            ("docker", "info", "--format", "{{.Runtimes}}"):
                CommandResult(("docker",), 0, "map[runc:{} runsc:{}]\n", ""),
            ("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"):
                CommandResult(("nvidia-smi",), 0, "NVIDIA GeForce RTX 4090, 24564\n", ""),
            # Requête du Gate : quatre champs, distincte de celle du doctor.
            ("nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version",
             "--format=csv,noheader,nounits"):
                CommandResult(("nvidia-smi",), 0,
                              "NVIDIA GeForce RTX 4090, 24564, 23100, 560.94\n", ""),
            ("nvidia-smi",):
                CommandResult(("nvidia-smi",), 0,
                              "Driver Version: 560.94       CUDA Version: 12.6\n", ""),
        },
        files={"/etc/wsl.conf": "[boot]\nsystemd=true\n\n[interop]\nenabled=false\nappendWindowsPath=false\n"},
        paths={"/sys/fs/cgroup/cgroup.controllers", "/usr/lib/wsl/lib"},
        free_gb={"/home/apex/data": 400.0},
        open_ports={("127.0.0.1", 5432)},
        environ={"WSL_DISTRO_NAME": "apex"},
    )


def context(probe: FakeProbe, cfg_overrides: dict | None = None) -> CheckContext:
    document = base_config()
    if cfg_overrides:
        document.update(cfg_overrides)
    return CheckContext(probe=probe, config=parse_config(document), timeout_s=1.0)


class TestEnvironmentChecks(unittest.TestCase):
    def test_healthy_wsl_passes(self) -> None:
        ctx = context(healthy_probe())
        self.assertIs(environment.check_wsl_interop(ctx).severity, Severity.PASS)
        self.assertIs(environment.check_wsl_systemd(ctx).severity, Severity.PASS)
        self.assertIs(environment.check_cgroups_v2(ctx).severity, Severity.PASS)

    def test_interop_enabled_warns_with_remediation(self) -> None:
        probe = healthy_probe()
        probe.files["/etc/wsl.conf"] = "[boot]\nsystemd=true\n"
        outcome = environment.check_wsl_interop(context(probe))
        self.assertIs(outcome.severity, Severity.WARN)
        self.assertIn("interop", outcome.remediation.lower())

    def test_missing_cgroups_v2_is_blocking(self) -> None:
        probe = healthy_probe()
        probe.paths.discard("/sys/fs/cgroup/cgroup.controllers")
        outcome = environment.check_cgroups_v2(context(probe))
        self.assertIs(outcome.severity, Severity.FAIL)

    def test_non_linux_host_is_blocking(self) -> None:
        probe = healthy_probe()
        probe.platform = {"system": "Windows", "release": "11", "machine": "AMD64", "python": "3.12.3"}
        probe.environ = {}
        self.assertIs(environment.check_host_platform(context(probe)).severity, Severity.FAIL)


class TestFilesystemPlacement(unittest.TestCase):
    def test_paths_on_windows_mount_are_blocking(self) -> None:
        document = base_config()
        document["paths"] = {
            "data_root": "/mnt/c/apex",
            "workspaces": "/mnt/c/apex/w",
            "artifacts": "/mnt/c/apex/a",
            "logs": "/mnt/c/apex/l",
        }
        ctx = CheckContext(probe=healthy_probe(), config=parse_config(document), timeout_s=1.0)
        outcome = platform.check_paths_on_ext4(ctx)
        self.assertIs(outcome.severity, Severity.FAIL)
        self.assertEqual(len(outcome.facts["offenders"]), 4)

    def test_ext4_paths_pass(self) -> None:
        self.assertIs(platform.check_paths_on_ext4(context(healthy_probe())).severity, Severity.PASS)


class TestIsolationCeiling(unittest.TestCase):
    """Principe 4 : le doctor énonce ce qui est PERMIS, pas seulement installé."""

    def test_runsc_present_allows_red_team(self) -> None:
        outcome = platform.check_isolation_runtimes(context(healthy_probe()))
        self.assertIs(outcome.severity, Severity.PASS)
        self.assertTrue(outcome.facts["red_team_allowed"])
        self.assertEqual(outcome.facts["isolation_level"], "gvisor")

    def test_runsc_absent_disables_red_team(self) -> None:
        probe = healthy_probe()
        probe.commands[("docker", "info", "--format", "{{.Runtimes}}")] = CommandResult(
            ("docker",), 0, "map[runc:{}]\n", ""
        )
        outcome = platform.check_isolation_runtimes(context(probe))
        self.assertIs(outcome.severity, Severity.WARN)
        self.assertFalse(outcome.facts["red_team_allowed"])
        self.assertIn("DÉSACTIVÉ", outcome.remediation)

    def test_docker_desktop_is_flagged(self) -> None:
        probe = healthy_probe()
        probe.commands[("docker", "context", "show")] = CommandResult(
            ("docker",), 0, "desktop-linux\n", ""
        )
        outcome = platform.check_docker_flavour(context(probe))
        self.assertIs(outcome.severity, Severity.WARN)
        self.assertIn("runsc", outcome.remediation)


class TestGpuChecks(unittest.TestCase):
    def test_gpu_detected(self) -> None:
        outcome = platform.check_gpu(context(healthy_probe()))
        self.assertIs(outcome.severity, Severity.PASS)
        self.assertAlmostEqual(outcome.facts["total_vram_gb"], 24.0, places=0)

    def test_missing_gpu_warns_not_fails(self) -> None:
        """Sans GPU, APEX reste installable ; seul le mode autonome est non viable."""
        probe = healthy_probe()
        probe.programs.pop("nvidia-smi")
        self.assertIs(platform.check_gpu(context(probe)).severity, Severity.WARN)

    def test_config_vram_mismatch_is_reported(self) -> None:
        probe = healthy_probe()
        probe.commands[
            ("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits")
        ] = CommandResult(("nvidia-smi",), 0, "NVIDIA GeForce RTX 3060, 12288\n", "")
        outcome = platform.check_gpu_matches_config(context(probe))
        self.assertIs(outcome.severity, Severity.WARN)
        self.assertEqual(outcome.facts["declared_gb"], 24.0)


class TestHardwareIsNotAnArchitecturalCeiling(unittest.TestCase):
    """Le poste utilisé pour développer APEX n'est ni la machine cible ni la
    machine de production — ses limites ne doivent jamais devenir celles
    d'APEX. Voir aussi tests/test_gate.py::TestHardwareThresholdsAreNotACeiling
    et tests/test_config.py::TestCapabilityAwareArchitecture."""

    def test_disk_and_vram_thresholds_are_configurable_not_hardcoded(self) -> None:
        document = base_config()
        document["gate"] = {"hardware": {"min_free_disk_gb": 1, "min_vram_gb": 0}}
        probe = healthy_probe()
        probe.free_gb["/home/apex/data"] = 5.0  # bien sous l'ancien défaut (150), au-dessus du choisi (1)
        ctx = CheckContext(probe=probe, config=parse_config(document), timeout_s=1.0)
        self.assertIs(platform.check_disk_space(ctx).severity, Severity.PASS)

    def test_target_pool_never_counted_in_declared_vram(self) -> None:
        """Un pool status=target bien plus gros que le GPU mesuré ne doit
        jamais faire croire à une non-concordance de configuration."""
        document = base_config()
        document["compute"]["pools"]["future-machine"] = {
            "location": "local", "trust_level": "trusted", "status": "target",
            "capabilities": ["inference"], "gpus": [{"model": "TBD", "vram_gb": 300}],
        }
        ctx = CheckContext(probe=healthy_probe(), config=parse_config(document), timeout_s=1.0)
        outcome = platform.check_gpu_matches_config(ctx)
        self.assertIs(outcome.severity, Severity.PASS)
        self.assertEqual(outcome.facts["declared_gb"], 24.0)

    def test_active_pool_mismatch_still_warns(self) -> None:
        """La correction n'a pas désactivé le contrôle pour un vrai pool ACTIVE."""
        document = base_config()
        document["compute"]["pools"]["local-gpu"]["gpus"][0]["vram_gb"] = 300
        ctx = CheckContext(probe=healthy_probe(), config=parse_config(document), timeout_s=1.0)
        self.assertIs(platform.check_gpu_matches_config(ctx).severity, Severity.WARN)


class TestServiceChecks(unittest.TestCase):
    def test_required_service_down_is_blocking(self) -> None:
        probe = healthy_probe()
        probe.open_ports.clear()
        self.assertIs(services.check_postgres(context(probe)).severity, Severity.FAIL)

    def test_ollama_missing_reference_model_is_blocking(self) -> None:
        probe = healthy_probe()
        probe.open_ports.add(("127.0.0.1", 11434))
        probe.http["http://127.0.0.1:11434/api/tags"] = HttpResult(
            200, '{"models": [{"name": "gemma4:12b-q4_K_M"}]}'
        )
        document = base_config()
        document["services"]["ollama"] = {"host": "127.0.0.1", "port": 11434, "required": False}
        ctx = CheckContext(probe=probe, config=parse_config(document), timeout_s=1.0)
        outcome = services.check_ollama_models(ctx)
        self.assertIs(outcome.severity, Severity.FAIL)

    def test_registry_integrity_holds(self) -> None:
        """Le registre ne doit jamais déclarer disponible ce qui n'existe pas."""
        outcome = services.check_interfaces_integrity(context(healthy_probe()))
        self.assertIs(outcome.severity, Severity.PASS, outcome.message)


class TestOllamaHardwareAwareDoctor(unittest.TestCase):
    """Un modèle de référence qui ne tient structurellement pas sur la VRAM du
    pool local ACTIVE (ex. 8 Go déclarés sur une carte à 8 Go, une fois la
    réserve `vram_headroom_fraction` retenue) ne doit jamais provoquer un FAIL
    « modèle manquant » : c'est un dimensionnement matériel attendu, pas un
    défaut d'installation. Voir
    `apex.doctor.checks.services._reference_is_hardware_compatible`.

    Cas réel diagnostiqué : machine à 8 Go de VRAM réelle (RTX 4060 Ti),
    `judge:gemma4:12b:q4_k_m` déclaré à EXACTEMENT 8 Go, `apex doctor`
    bloquant à tort en FAIL avant ce correctif."""

    @staticmethod
    def _ctx(document: dict, present: list[str]) -> CheckContext:
        probe = healthy_probe()
        probe.open_ports.add(("127.0.0.1", 11434))
        models = ", ".join(f'{{"name": "{name}"}}' for name in present)
        probe.http["http://127.0.0.1:11434/api/tags"] = HttpResult(200, f'{{"models": [{models}]}}')
        document["services"]["ollama"] = {"host": "127.0.0.1", "port": 11434, "required": False}
        return CheckContext(probe=probe, config=parse_config(document), timeout_s=1.0)

    @staticmethod
    def _with_gemma4_endpoint(document: dict, vram_gb: float) -> dict:
        document["models"]["endpoints"].append(
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "gemma4:12b-q4_K_M", "max_context": 16384, "vram_gb": vram_gb}
        )
        return document

    def test_8gb_hw_8gb_reference_is_incompatible_never_blocking(self) -> None:
        """Cas réel diagnostiqué : 8 Go de VRAM totale + référence déclarée à
        EXACTEMENT 8 Go ⇒ incompatible (WARN), jamais bloquant. Sans réserve
        de VRAM, `8.0 <= 8.0` juge à tort ce modèle compatible — le contexte
        CUDA/le runtime/l'OS ne laissent jamais la totalité de la VRAM
        rapportée disponible pour les poids du modèle."""
        document = self._with_gemma4_endpoint(base_config(), vram_gb=8.0)
        document["compute"]["pools"]["local-gpu"]["gpus"][0]["vram_gb"] = 8
        ctx = self._ctx(document, ["qwen3-coder:30b"])  # coder_main présent, judge absent
        outcome = services.check_ollama_models(ctx)
        self.assertIs(outcome.severity, Severity.WARN)
        self.assertIn("judge:gemma4:12b:q4_k_m", outcome.facts["missing_reference_incompatible"])
        self.assertEqual(outcome.facts["missing_reference"], [])

    def test_headroom_is_the_real_mechanism_not_a_hardcoded_special_case(self) -> None:
        """Avec une réserve explicitement mise à 0, 8 Go/8 Go redevient
        « compatible » (donc bloquant) — preuve que la correction est une
        réserve proportionnelle configurable, jamais un cas spécial câblé en
        dur sur « 8 Go »/« gemma4 »."""
        document = self._with_gemma4_endpoint(base_config(), vram_gb=8.0)
        document["gate"] = {"hardware": {"vram_headroom_fraction": 0.0}}
        document["compute"]["pools"]["local-gpu"]["gpus"][0]["vram_gb"] = 8
        ctx = self._ctx(document, ["qwen3-coder:30b"])
        outcome = services.check_ollama_models(ctx)
        self.assertIs(outcome.severity, Severity.FAIL)
        self.assertIn("judge:gemma4:12b:q4_k_m", outcome.facts["missing_reference"])

    def test_measured_vram_takes_priority_over_declared_for_compatibility(self) -> None:
        """`effective_vram_gb` (mesure prime sur déclaration) doit être ce que
        la comparaison de compatibilité utilise réellement — jamais la valeur
        documentaire une fois qu'une mesure existe."""
        document = self._with_gemma4_endpoint(base_config(), vram_gb=5.0)  # déclaré : compatible
        document["compute"]["pools"]["local-gpu"]["gpus"][0]["vram_gb"] = 8
        document["models"]["endpoints"][-1]["measured"] = {
            "vram_gb": 9.0, "tokens_per_s": 10.0,  # mesuré : incompatible même sans réserve
            "measured_at": "2026-01-01T00:00:00Z", "environment_fingerprint": "test-fp",
        }
        ctx = self._ctx(document, ["qwen3-coder:30b"])
        outcome = services.check_ollama_models(ctx)
        self.assertIs(outcome.severity, Severity.WARN)
        self.assertIn("judge:gemma4:12b:q4_k_m", outcome.facts["missing_reference_incompatible"])

    def test_target_only_pool_is_never_treated_as_a_real_local_active_pool(self) -> None:
        """Aucun pool local ACTIVE (seulement un pool `status=target`, machine
        future) ⇒ information insuffisante ⇒ comportement conservateur
        (absence toujours bloquante) — un pool visé ne doit jamais se faire
        passer pour une disponibilité réelle aujourd'hui."""
        document = self._with_gemma4_endpoint(base_config(), vram_gb=8.0)
        document["compute"]["pools"]["local-gpu"]["status"] = "target"
        document["compute"]["pools"]["local-gpu"]["gpus"][0]["vram_gb"] = 300
        document["compute"]["pools"]["local-cpu"] = {
            "location": "local", "trust_level": "trusted", "status": "active",
            "capabilities": ["inference"], "cpu_cores": 4, "ram_gb": 8,
        }
        ctx = self._ctx(document, ["qwen3-coder:30b"])
        outcome = services.check_ollama_models(ctx)
        self.assertIs(outcome.severity, Severity.FAIL)
        self.assertIn("judge:gemma4:12b:q4_k_m", outcome.facts["missing_reference"])

    def test_headroom_scales_proportionally_not_as_a_fixed_amount(self) -> None:
        """La réserve est un POURCENTAGE, pas un montant fixe : sur le pool par
        défaut (24 Go), la référence coder_main (18 Go, voir BASE) reste
        compatible avec une réserve de 20 % (24*0.8=19.2 ≥ 18) mais devient
        incompatible avec une réserve de 30 % (24*0.7=16.8 < 18) — le
        mécanisme doit rester raisonnable pour de futures machines disposant
        de davantage de VRAM, jamais un seuil arbitraire câblé sur la machine
        actuelle."""
        document = base_config()
        document["gate"] = {"hardware": {"vram_headroom_fraction": 0.2}}
        ctx = self._ctx(document, [])
        outcome = services.check_ollama_models(ctx)
        self.assertIs(outcome.severity, Severity.FAIL)  # 18 Go compatible sur 24 Go, réserve 20 %

        document2 = base_config()
        document2["gate"] = {"hardware": {"vram_headroom_fraction": 0.3}}
        ctx2 = self._ctx(document2, [])
        outcome2 = services.check_ollama_models(ctx2)
        self.assertIs(outcome2.severity, Severity.WARN)  # 18 Go incompatible sur 24 Go, réserve 30 %
        self.assertIn(
            "coder_main:qwen3-coder:30b-a3b-instruct:q4_k_m",
            outcome2.facts["missing_reference_incompatible"],
        )


class TestRunner(unittest.TestCase):
    def test_runner_never_crashes_on_a_broken_check(self) -> None:
        def exploding(ctx: CheckContext):
            raise RuntimeError("boum")

        report = run_checks(context(healthy_probe()), checks=(("broken", exploding),))
        self.assertIs(report.worst, Severity.FAIL)
        self.assertIn("boum", report.results[0].message)

    def test_exit_code_zero_when_only_warnings(self) -> None:
        report = run_checks(
            context(healthy_probe()),
            checks=(("wsl.systemd", environment.check_wsl_systemd),),
        )
        self.assertEqual(report.exit_code, 0)

    def test_full_run_on_healthy_machine_has_no_failures(self) -> None:
        probe = healthy_probe()
        probe.open_ports.update({("127.0.0.1", 6379), ("127.0.0.1", 9000)})
        document = base_config()
        document["services"] = {
            "postgres": {"host": "127.0.0.1", "port": 5432},
            "redis": {"host": "127.0.0.1", "port": 6379},
            "minio": {"host": "127.0.0.1", "port": 9000},
        }
        ctx = CheckContext(probe=probe, config=parse_config(document), timeout_s=1.0)
        report = run_checks(ctx)
        failures = [r for r in report.results if r.severity is Severity.FAIL]
        # python.packages peut échouer selon l'environnement de test ; on l'exclut.
        blocking = [r for r in failures if r.name != "python.packages"]
        self.assertEqual(blocking, [], [r.message for r in blocking])

    def test_text_rendering_is_readable(self) -> None:
        report = run_checks(
            context(healthy_probe()), checks=(("gpu.present", platform.check_gpu),)
        )
        text = render_text(report)
        self.assertIn("APEX DOCTOR", text)
        self.assertIn("lecture seule", text)


if __name__ == "__main__":
    unittest.main()

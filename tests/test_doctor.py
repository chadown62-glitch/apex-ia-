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
from apex.errors import ConfigError
from apex.util.probe import CommandResult, FakeProbe, HttpResult, Probe
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
    """Passe « hardware-aware doctor » (audit blocker POSTGRES/Ollama/GPU).

    Un modèle de référence qui ne tient structurellement pas sur la VRAM du
    pool local ACTIVE (ex. une référence à 18 Go sur une machine à 8 Go) ne
    doit jamais provoquer un FAIL « modèle manquant » : c'est un
    dimensionnement matériel attendu, pas un défaut d'installation. Voir
    `apex.doctor.checks.services._reference_is_hardware_compatible`.

    Le catalogue de `config.yaml` (candidats, `selection: benchmark_driven`)
    n'est jamais modifié par cette passe : seule l'interprétation d'une
    absence change selon la compatibilité matérielle.
    """

    @staticmethod
    def _ctx(document: dict, present: list[str]) -> CheckContext:
        probe = healthy_probe()
        probe.open_ports.add(("127.0.0.1", 11434))
        models = ", ".join(f'{{"name": "{name}"}}' for name in present)
        probe.http["http://127.0.0.1:11434/api/tags"] = HttpResult(200, f'{{"models": [{models}]}}')
        document["services"]["ollama"] = {"host": "127.0.0.1", "port": 11434, "required": False}
        return CheckContext(probe=probe, config=parse_config(document), timeout_s=1.0)

    def test_a_8gb_hw_18gb_reference_is_incompatible_not_missing_install(self) -> None:
        """A. 8 Go de VRAM + référence à 18 Go ⇒ incompatible, jamais un FAIL."""
        document = base_config()
        document["compute"]["pools"]["local-gpu"]["gpus"][0]["vram_gb"] = 8
        ctx = self._ctx(document, ["gemma4:12b-q4_K_M"])
        outcome = services.check_ollama_models(ctx)
        self.assertIs(outcome.severity, Severity.WARN)
        self.assertIn(
            "coder_main:qwen3-coder:30b-a3b-instruct:q4_k_m",
            outcome.facts["missing_reference_incompatible"],
        )
        self.assertEqual(outcome.facts["missing_reference"], [])

    def test_b_8gb_hw_5gb_reference_stays_compatible_and_blocking(self) -> None:
        """B. 8 Go de VRAM + référence à 5 Go ⇒ compatible ; absente ⇒ FAIL."""
        document = base_config()
        document["compute"]["pools"]["local-gpu"]["gpus"][0]["vram_gb"] = 8
        document["models"]["endpoints"][0]["vram_gb"] = 5.0
        ctx = self._ctx(document, ["gemma4:12b-q4_K_M"])
        outcome = services.check_ollama_models(ctx)
        self.assertIs(outcome.severity, Severity.FAIL)
        self.assertIn(
            "coder_main:qwen3-coder:30b-a3b-instruct:q4_k_m", outcome.facts["missing_reference"]
        )

    def test_c_24gb_hw_18gb_reference_stays_compatible_and_blocking(self) -> None:
        """C. 24 Go + 18 Go ⇒ compatible — comportement historique inchangé."""
        ctx = self._ctx(base_config(), ["gemma4:12b-q4_K_M"])
        outcome = services.check_ollama_models(ctx)
        self.assertIs(outcome.severity, Severity.FAIL)
        self.assertIn(
            "coder_main:qwen3-coder:30b-a3b-instruct:q4_k_m", outcome.facts["missing_reference"]
        )

    def test_d_24gb_hw_17gb_reference_stays_compatible_and_blocking(self) -> None:
        """D. 24 Go + 17 Go ⇒ compatible ⇒ absence toujours bloquante."""
        document = base_config()
        document["models"]["endpoints"][0]["vram_gb"] = 17.0
        ctx = self._ctx(document, ["gemma4:12b-q4_K_M"])
        outcome = services.check_ollama_models(ctx)
        self.assertIs(outcome.severity, Severity.FAIL)

    def test_e_small_hardware_never_removes_large_models_from_catalog(self) -> None:
        """E. 8 Go de VRAM ne doit jamais faire disparaître un gros modèle du catalogue."""
        document = base_config()
        document["compute"]["pools"]["local-gpu"]["gpus"][0]["vram_gb"] = 8
        cfg = parse_config(document)
        families = {c.family for c in cfg.model_roles["coder_main"].candidates}
        self.assertEqual(families, {"qwen3-coder", "devstral-2"})
        # Le candidat de référence (index 0) reste bien le 30B, jamais remplacé.
        self.assertEqual(cfg.model_roles["coder_main"].reference.family, "qwen3-coder")

    def test_f_selection_benchmark_driven_is_never_altered(self) -> None:
        """F. `selection: benchmark_driven` reste inchangé après la passe doctor."""
        document = base_config()
        document["models"]["roles"]["coder_main"]["selection"] = "benchmark_driven"
        document["compute"]["pools"]["local-gpu"]["gpus"][0]["vram_gb"] = 8
        ctx = self._ctx(document, ["gemma4:12b-q4_K_M"])
        services.check_ollama_models(ctx)
        self.assertEqual(ctx.config.model_roles["coder_main"].selection, "benchmark_driven")

    def test_g_doctor_cannot_install_or_download_anything(self) -> None:
        """G. Aucune installation/téléchargement automatique n'est possible.

        `Probe` (apex/util/probe.py) est un protocole intégralement en lecture
        seule (§53) : aucune méthode d'écriture, d'installation ou de
        téléchargement n'existe sur lui, donc `check_ollama_models` ne peut
        matériellement pas déclencher de `ollama pull`.
        """
        writer_like = {
            name
            for name in dir(Probe)
            if not name.startswith("_")
            and any(word in name for word in ("pull", "install", "download", "write", "fetch"))
        }
        self.assertEqual(writer_like, set())

        document = base_config()
        document["compute"]["pools"]["local-gpu"]["gpus"][0]["vram_gb"] = 8
        ctx = self._ctx(document, ["gemma4:12b-q4_K_M"])
        services.check_ollama_models(ctx)
        self.assertNotIn(
            "run", {kind for kind, _ in ctx.probe.calls}
        )  # aucune commande shell exécutée par ce check
        self.assertFalse(any("pull" in payload for _, payload in ctx.probe.calls))

    def test_i_same_family_different_size_are_never_confused_by_served_endpoints(self) -> None:
        """`served_endpoints()` doit apparier par family+version, jamais par
        famille seule : un endpoint 18 Go qwen3-coder 30B (coder_main, non
        résident) sur une machine 8 Go est incompatible ; un endpoint 5 Go
        qwen3-coder 8B (fast) est compatible — le Doctor ne doit jamais
        confondre les deux uniquement parce qu'ils partagent la famille
        « qwen3-coder »."""
        document = base_config()
        document["compute"]["pools"]["local-gpu"]["gpus"][0]["vram_gb"] = 8
        document["models"]["roles"] = {
            "coder_main": {
                "selection": "benchmark_driven",
                "resident": False,
                "candidates": [
                    {"family": "qwen3-coder", "version": "30b-a3b-instruct",
                     "quantization": "q4_k_m", "capability_class": "coder"},
                ],
            },
            "fast": {
                "selection": "static_prior",
                "resident": True,
                "candidates": [
                    {"family": "qwen3-coder", "version": "8b",
                     "quantization": "q4_k_m", "capability_class": "fast"},
                ],
            },
            "judge": {
                "candidates": [
                    {"family": "gemma4", "version": "12b",
                     "quantization": "q4_k_m", "capability_class": "judge"}
                ]
            },
        }
        document["models"]["endpoints"] = [
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "qwen3-coder:30b-a3b-instruct-q4_K_M", "max_context": 65536, "vram_gb": 18.0},
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "qwen3-coder:8b-q4_K_M", "max_context": 32768, "vram_gb": 5.0},
        ]
        # Ni l'un ni l'autre n'est réellement installé dans Ollama ici — seul
        # le Judge (déjà présent) l'est, pour isoler le comportement testé.
        ctx = self._ctx(document, ["gemma4:12b-q4_K_M"])
        outcome = services.check_ollama_models(ctx)

        # coder_main (18 Go sur 8 Go dispo) : incompatible, jamais un FAIL.
        self.assertIn(
            "coder_main:qwen3-coder:30b-a3b-instruct:q4_k_m",
            outcome.facts["missing_reference_incompatible"],
        )
        # fast (5 Go sur 8 Go dispo) : compatible et absent ⇒ bloquant.
        self.assertIn("fast:qwen3-coder:8b:q4_k_m", outcome.facts["missing_reference"])
        # Aucune confusion croisée entre les deux candidats de la famille.
        self.assertNotIn(
            "fast:qwen3-coder:8b:q4_k_m", outcome.facts["missing_reference_incompatible"]
        )
        self.assertNotIn(
            "coder_main:qwen3-coder:30b-a3b-instruct:q4_k_m", outcome.facts["missing_reference"]
        )
        self.assertIs(outcome.severity, Severity.FAIL)

    def test_h_genuinely_invalid_config_still_raises_a_real_error(self) -> None:
        """H. Une configuration invalide continue de provoquer une vraie erreur —
        la passe hardware-aware ne doit jamais rendre le doctor artificiellement
        vert en avalant une erreur de configuration réelle."""
        document = base_config()
        del document["paths"]["artifacts"]
        with self.assertRaises(ConfigError):
            parse_config(document)


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

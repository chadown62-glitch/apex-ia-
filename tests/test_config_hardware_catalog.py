"""Verrouille le comportement du VRAI `config.yaml` (pas un document
synthétique) sur une machine réellement diagnostiquée pendant la mission
« Hardware-accurate local runtime configuration » (N+21) : RTX 4060 Ti,
8 Go de VRAM, 3 modèles Ollama réellement installés
(qwen2.5-coder:7b-instruct, gemma4:12b, bge-m3:latest).

Sans ce test, une future modification du catalogue pourrait silencieusement
réintroduire le FAIL bloquant que cette passe a corrigé (coder_main sans
aucun candidat installé+compatible sur du matériel à 8 Go) sans qu'aucun
test existant (`tests/test_doctor.py::TestOllamaHardwareAwareDoctor` utilise
un document SYNTHÉTIQUE, jamais le fichier réel) ne le détecte."""

from __future__ import annotations

import unittest

from apex.config.loader import load_config
from apex.doctor.check import CheckContext, Severity
from apex.doctor.checks.platform import check_gpu_matches_config
from apex.doctor.checks.services import check_ollama_models
from apex.util.probe import CommandResult, FakeProbe, HttpResult

#: Environnement RÉEL diagnostiqué (RTX 4060 Ti 8 Go, 16 threads) — les
#: valeurs par défaut de `config.yaml` (RTX 4090 24 Go) ne doivent JAMAIS
#: être utilisées telles quelles sur cette machine (§ mission, problème 3).
_REAL_HARDWARE_ENVIRON = {
    "APEX_GPU_MODEL": "NVIDIA GeForce RTX 4060 Ti",
    "APEX_GPU_VRAM_GB": "8",
    "APEX_CPU_CORES": "16",
    "APEX_RAM_GB": "10",
    "POSTGRES_PASSWORD": "test-only",
    "MINIO_ROOT_PASSWORD": "test-only",
}

_REAL_INSTALLED_MODELS = ("qwen2.5-coder:7b-instruct", "gemma4:12b", "bge-m3:latest")


def _real_config():
    return load_config(environ=_REAL_HARDWARE_ENVIRON)


def _ollama_ctx(config, *, present: tuple[str, ...] = _REAL_INSTALLED_MODELS) -> CheckContext:
    probe = FakeProbe()
    probe.open_ports.add(("127.0.0.1", 11434))
    models = ", ".join(f'{{"name": "{name}"}}' for name in present)
    probe.http["http://127.0.0.1:11434/api/tags"] = HttpResult(200, f'{{"models": [{models}]}}')
    return CheckContext(probe=probe, config=config, timeout_s=1.0)


class TestRealConfigOnConstrainedHardware(unittest.TestCase):
    """`config.yaml` réel + variables d'environnement réelles d'une machine
    à 8 Go de VRAM — jamais un document de test reconstruit à la main."""

    def test_a_config_loads_and_validates(self) -> None:
        config = _real_config()
        config.validate()

    def test_b_gpu_config_match_is_pass_once_env_reflects_real_hardware(self) -> None:
        """§ problème 3 : le mismatch 24 Go déclaré / 8 Go mesurés n'est pas
        un bug de code — c'est `.env` non personnalisé. Une fois les
        variables d'environnement réelles fournies, `gpu.config_match`
        devient PASS avec le MÊME code, sans aucune modification."""
        config = _real_config()
        probe = FakeProbe(
            programs={"nvidia-smi": "/usr/lib/wsl/lib/nvidia-smi"},
            commands={
                ("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"):
                    CommandResult(("nvidia-smi",), 0, "NVIDIA GeForce RTX 4060 Ti, 8188\n", ""),
            },
        )
        ctx = CheckContext(probe=probe, config=config, timeout_s=1.0)
        outcome = check_gpu_matches_config(ctx)
        self.assertIs(outcome.severity, Severity.PASS, outcome.message)

    def test_c_coder_main_reference_is_the_verified_working_local_model(self) -> None:
        """§ problème 2 : `coder_main.candidates[0]` doit être un modèle
        RÉELLEMENT installé et vérifié fonctionner sur cette carte — pas un
        modèle 30B inatteignable sur 8 Go."""
        config = _real_config()
        self.assertEqual(config.model_roles["coder_main"].reference.family, "qwen2.5-coder")

    def test_d_ollama_models_is_never_fail_on_real_hardware_with_real_models(self) -> None:
        """Le cœur de la correction : avec le matériel et les modèles
        RÉELLEMENT présents sur cette machine, `ollama.models` ne doit
        JAMAIS retomber en FAIL bloquant (coder_main a désormais un
        candidat de référence installé et compatible)."""
        config = _real_config()
        outcome = check_ollama_models(_ollama_ctx(config))
        self.assertIsNot(outcome.severity, Severity.FAIL, outcome.message)
        self.assertEqual(outcome.facts["missing_reference"], [])

    def test_e_reasoner_gap_is_signaled_honestly_never_fabricated_as_pass(self) -> None:
        """§ problème 2 : aucun modèle installé n'est réaffecté au rôle
        reasoner sans preuve — le manque doit rester visible (WARN,
        `missing_reference_incompatible`), jamais un PASS fabriqué en
        prétendant qu'un modèle codeur/juge sait raisonner sans évidence."""
        config = _real_config()
        outcome = check_ollama_models(_ollama_ctx(config))
        self.assertIn(
            "reasoner:qwen3.6:27b:q4_k_m",
            outcome.facts["missing_reference_incompatible"],
        )

    def test_f_gate_16_make_absent_never_blocks_phase_2_authorization(self) -> None:
        """§ problème 4 : `make` est un outil « souple » (`GATE-16`,
        `apex.gate.checks.gate_16_tools`) — son absence doit rester
        DEGRADED, jamais bloquer `GateReport.phase_2_authorized`."""
        from apex.gate.checks import GateContext, gate_16_tools
        from apex.gate.model import GateStatus

        probe = FakeProbe(programs={"git": "/usr/bin/git", "curl": "/usr/bin/curl", "ollama": "/usr/bin/ollama"})
        ctx = GateContext(probe=probe, config=None, timeout_s=1.0)
        outcome = gate_16_tools(ctx)
        self.assertIs(outcome.status, GateStatus.DEGRADED)
        self.assertEqual(outcome.blocks_phases, ())


if __name__ == "__main__":
    unittest.main()

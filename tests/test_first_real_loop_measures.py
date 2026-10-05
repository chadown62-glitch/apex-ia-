"""Mesures de la machine cible (`first_real_loop.py`) : lecture de nvidia-smi et d'Ollama `/api/ps`.

Entrées saisies à la main pour vérifier la LECTURE seulement — jamais une mesure de machine."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "setup" / "packaging" / "windows" / "ci" / "first_real_loop.py"
_spec = importlib.util.spec_from_file_location("first_real_loop", _PATH)
loop = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loop)


def test_gpu_query_is_read_never_invented() -> None:
    assert loop.parse_gpu_query("NVIDIA GeForce RTX 4060 Ti, 5321, 8188, 97\n") == (
        "NVIDIA GeForce RTX 4060 Ti : VRAM 5321/8188 Mio, GPU 97 %"
    )
    assert loop.parse_gpu_query("") == "GPU non mesuré"
    assert loop.parse_gpu_query("format inattendu") == "GPU non mesuré"


def test_ollama_ps_reports_the_vram_share_ollama_gives() -> None:
    payload = {"models": [{"name": "qwen2.5-coder:7b-instruct", "size": 5_000_000_000, "size_vram": 4_500_000_000}]}
    assert loop.parse_ollama_ps(payload) == (
        "Ollama : qwen2.5-coder:7b-instruct 5.0 Go dont 4.5 Go en VRAM (90 %)"
    )
    assert loop.parse_ollama_ps({"models": []}) == "Ollama : aucun modèle chargé"
    assert "illisible" in loop.parse_ollama_ps({})

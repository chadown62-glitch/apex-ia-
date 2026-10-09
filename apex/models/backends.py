"""Catalogue FERMÉ des backends d'inférence (`ModelEndpoint.runtime`) — ce qui est réel, ce qui est prévu.

Interface préparée pour les variantes de modèles (quantification, chargement couche par couche type AirLLM,
adaptateurs LoRA) SANS aucun faux backend :

- **Quantification** : une propriété du modèle SERVI (`ModelRef.quantization`, ex. `q4_k_m`), choisie au niveau
  du runtime (Ollama/llama.cpp : fichier GGUF ; vLLM : AWQ/GPTQ). APEX la décrit et la route, il ne quantifie
  rien lui-même.
- **LoRA** : réel aujourd'hui uniquement quand le RUNTIME charge l'adaptateur et l'expose sous un nom de modèle
  servi (Ollama : `ADAPTER` dans un Modelfile ; vLLM : `--lora-modules nom=chemin`) — `served_model` désigne
  alors l'adaptateur. Charger/entraîner un adaptateur DANS APEX (PEFT) : PLANNED, rien n'est implémenté.
- **Natif en processus (`transformers`)** : IMPLÉMENTÉ depuis le 06/10/2026 (`apex.models.native`) — poids
  chargés dans le processus APEX, aucun serveur de modèles. Quantification : poids tels que distribués (CPU
  float32, GPU bf16) ; 4/8 bits non implémentés.
- **Cerveau APEX (`apex_brain`)** : IMPLÉMENTÉ le 09/10/2026 (`apex.models.apex_brain`) — le réseau propre à
  APEX (`apex.brain`), servi en processus ; EXPÉRIMENTAL (pré-entraîné, jamais instruit à converser). Jamais
  candidat d'un rôle de la configuration : seulement choisi explicitement dans l'assistant.
- **AirLLM** (chargement couche par couche) : PLANNED. Un endpoint qui le demande obtient un fournisseur
  honnêtement INDISPONIBLE (`UnavailableRuntimeProvider`) — jamais redirigé vers un adaptateur HTTP qui ne
  parlerait pas son protocole, jamais une réponse simulée.

Un runtime inconnu est traité comme PLANNED inconnu : refusé (fail-closed), plus jamais « deviné »
OpenAI-compatible.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BackendStatus(str, Enum):
    IMPLEMENTED = "implemented"
    PLANNED = "planned"


@dataclass(frozen=True)
class RuntimeBackend:
    runtime: str
    status: BackendStatus
    #: `ollama` | `openai_compatible` | `native` : l'adaptateur réel utilisé ; `None` si PLANNED.
    adapter: str | None
    description: str
    lora: str


_HTTP_LORA = "via le modèle servi (adaptateur chargé par le serveur et exposé sous un nom de modèle)"

BACKENDS: tuple[RuntimeBackend, ...] = (
    RuntimeBackend("ollama", BackendStatus.IMPLEMENTED, "ollama",
                   "serveur Ollama local (GGUF quantifié)", "via Modelfile ADAPTER (modèle servi dédié)"),
    RuntimeBackend("openai_compatible", BackendStatus.IMPLEMENTED, "openai_compatible",
                   "toute API compatible OpenAI (chat completions)", _HTTP_LORA),
    *(
        RuntimeBackend(alias, BackendStatus.IMPLEMENTED, "openai_compatible",
                       f"{alias} : API compatible OpenAI", _HTTP_LORA)
        for alias in ("vllm", "llama_cpp", "lmstudio", "external", "openai", "openrouter", "together", "groq",
                      "fireworks", "deepseek", "mistral", "xai", "qwen")
    ),
    RuntimeBackend("airllm", BackendStatus.PLANNED, None,
                   "inférence en processus couche par couche (gros modèles sur peu de VRAM) — non implémentée",
                   "non applicable"),
    RuntimeBackend("transformers", BackendStatus.IMPLEMENTED, "native",
                   "inférence NATIVE dans le processus APEX (PyTorch + transformers), sans serveur de modèles",
                   "PEFT en processus — non implémenté"),
    RuntimeBackend("apex_brain", BackendStatus.IMPLEMENTED, "apex_brain",
                   "Cerveau APEX : ApexLM, le réseau propre à APEX, en processus — EXPÉRIMENTAL (pré-entraîné, "
                   "pas instruit à converser)", "non applicable"),
)
_BY_RUNTIME = {b.runtime: b for b in BACKENDS}


def backend_for(runtime: str) -> RuntimeBackend | None:
    """Le backend déclaré pour `runtime`, ou `None` s'il est inconnu (à traiter comme indisponible)."""
    return _BY_RUNTIME.get(runtime)

"""Configuration du modèle APEX (architecture) — aucune taille codée en dur. Partagée par l'inférence
(`apex.brain`, application) et l'entraînement (`apex.neural`, recherche)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

_DTYPES = ("float32", "bfloat16", "float16")
_V2_DEFAULTS: dict[str, Any] = {"num_kv_heads": None, "qk_norm": False}


@dataclass(frozen=True)
class ModelConfig:
    vocab_size: int
    hidden_size: int = 128
    num_layers: int = 2
    num_heads: int = 4
    intermediate_size: int = 344
    context_length: int = 128
    dropout: float = 0.0
    norm_eps: float = 1e-5
    rope_theta: float = 10000.0
    tie_embeddings: bool = True
    dtype: str = "float32"
    device: str = "cpu"
    pad_token_id: int | None = None
    #: Génération 2 — têtes clé/valeur partagées (attention groupée, GQA) : `None` = une par tête de requête
    #: (attention multi-têtes classique, Cerveau v1). Moins de têtes K/V = cache d'inférence plus petit.
    num_kv_heads: int | None = None
    #: Génération 2 — normalisation RMS des requêtes et des clés par tête (stabilité à fort taux
    #: d'apprentissage).
    qk_norm: bool = False

    def __post_init__(self) -> None:
        positive = (
            "vocab_size",
            "hidden_size",
            "num_layers",
            "num_heads",
            "intermediate_size",
            "context_length",
        )
        for name in positive:
            if getattr(self, name) < 1:
                raise ValueError(f"{name} doit être >= 1")
        if self.hidden_size % self.num_heads:
            raise ValueError("hidden_size doit être divisible par num_heads")
        if (self.hidden_size // self.num_heads) % 2:
            raise ValueError("la dimension par tête doit être paire (encodage rotatif)")
        if not 0.0 <= self.dropout < 1.0:
            raise ValueError("dropout doit être dans [0, 1)")
        if self.dtype not in _DTYPES:
            raise ValueError(f"dtype inconnu : {self.dtype}")
        if self.pad_token_id is not None and not 0 <= self.pad_token_id < self.vocab_size:
            raise ValueError("pad_token_id hors du vocabulaire")
        if self.num_kv_heads is not None and (self.num_kv_heads < 1 or self.num_heads % self.num_kv_heads):
            raise ValueError("num_heads doit être un multiple de num_kv_heads")

    @property
    def head_dim(self) -> int:
        return self.hidden_size // self.num_heads

    @property
    def kv_heads(self) -> int:
        return self.num_kv_heads if self.num_kv_heads is not None else self.num_heads

    def to_dict(self) -> dict[str, Any]:
        """Les champs de la génération 2 n'apparaissent que s'ils sont utilisés : une configuration v1 garde
        exactement son contenu (et son empreinte)."""
        data = asdict(self)
        for name, default in _V2_DEFAULTS.items():
            if data[name] == default:
                del data[name]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelConfig:
        return cls(**data)

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()

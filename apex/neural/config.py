"""Configuration du modèle et de l'entraînement — aucune taille codée en dur."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

_DTYPES = ("float32", "bfloat16", "float16")


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

    @property
    def head_dim(self) -> int:
        return self.hidden_size // self.num_heads

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelConfig:
        return cls(**data)

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class OptimizerSpec:
    name: str = "adamw"
    lr: float = 3e-3
    weight_decay: float = 0.1
    betas: tuple[float, float] = (0.9, 0.95)
    momentum: float = 0.9

    def __post_init__(self) -> None:
        if self.lr <= 0:
            raise ValueError("lr doit être > 0")
        if self.weight_decay < 0:
            raise ValueError("weight_decay doit être >= 0")


@dataclass(frozen=True)
class SchedulerSpec:
    name: str = "cosine"
    warmup_steps: int = 0
    min_lr_ratio: float = 0.1

    def __post_init__(self) -> None:
        if self.name not in ("constant", "cosine"):
            raise ValueError(f"scheduler inconnu : {self.name}")
        if self.warmup_steps < 0 or not 0.0 <= self.min_lr_ratio <= 1.0:
            raise ValueError("warmup_steps >= 0 et min_lr_ratio dans [0, 1]")


@dataclass(frozen=True)
class TrainingConfig:
    max_steps: int = 100
    batch_size: int = 8
    grad_accumulation_steps: int = 1
    grad_clip_norm: float | None = 1.0
    mixed_precision: bool = False
    seed: int = 0
    eval_every: int = 0
    optimizer: OptimizerSpec = field(default_factory=OptimizerSpec)
    scheduler: SchedulerSpec = field(default_factory=SchedulerSpec)

    def __post_init__(self) -> None:
        if self.max_steps < 1 or self.batch_size < 1 or self.grad_accumulation_steps < 1:
            raise ValueError("max_steps, batch_size et grad_accumulation_steps doivent être >= 1")
        if self.grad_clip_norm is not None and self.grad_clip_norm <= 0:
            raise ValueError("grad_clip_norm doit être > 0 ou None")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrainingConfig:
        values = dict(data)
        optimizer = dict(values.pop("optimizer", {}))
        if "betas" in optimizer:
            optimizer["betas"] = tuple(optimizer["betas"])
        return cls(
            optimizer=OptimizerSpec(**optimizer),
            scheduler=SchedulerSpec(**values.pop("scheduler", {})),
            **values,
        )

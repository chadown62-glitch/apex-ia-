"""Checkpoints : tout ce qu'il faut pour arrêter, recharger et continuer.

Contenu : états du modèle, de l'optimiseur, du scheduler, du scaler ; étape,
époque, dernière perte ; configurations du modèle et de l'entraînement ;
tokenizer ; empreinte et provenance du dataset ; versions logicielles ; graine
et états des générateurs aléatoires (global et échantillonneur de lots)."""

from __future__ import annotations

import hashlib
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

from apex.neural.config import ModelConfig, TrainingConfig
from apex.neural.model import ApexLM
from apex.neural.tokenizer import ByteBPETokenizer
from apex.neural.training import TokenBatcher, TrainingEngine
from apex.version import __version__ as APEX_VERSION

FORMAT_VERSION = 1


@dataclass(frozen=True)
class CheckpointInfo:
    step: int
    epoch: float
    loss: float | None
    dataset_fingerprint: str
    model_config: ModelConfig
    training_config: TrainingConfig
    software: dict[str, str]
    seed: int
    dataset_provenance: dict[str, Any]


def save_checkpoint(
    path: str | Path,
    engine: TrainingEngine,
    *,
    tokenizer: ByteBPETokenizer,
    batcher: TokenBatcher | None,
    dataset_fingerprint: str,
    dataset_provenance: dict[str, Any],
    dataset_tokens: int,
) -> str:
    """Écrit le checkpoint (écriture atomique) et retourne son SHA-256."""
    payload = {
        "format_version": FORMAT_VERSION,
        "engine": engine.state_dict(),
        "epoch": engine.tokens_seen / max(1, dataset_tokens),
        "model_config": engine.model.config.to_dict(),
        "training_config": engine.config.to_dict(),
        "tokenizer": tokenizer.to_dict(),
        "dataset_fingerprint": dataset_fingerprint,
        "dataset_provenance": dataset_provenance,
        "software": {
            "apex": APEX_VERSION,
            "torch": torch.__version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "seed": engine.config.seed,
        "rng": {"torch": torch.get_rng_state()},
        "batcher": batcher.state_dict() if batcher is not None else None,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    torch.save(payload, tmp)
    tmp.replace(target)
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _read(path: str | Path) -> dict[str, Any]:
    payload: dict[str, Any] = torch.load(Path(path), map_location="cpu", weights_only=False)
    if payload.get("format_version") != FORMAT_VERSION:
        raise ValueError(f"format de checkpoint inconnu : {payload.get('format_version')}")
    return payload


def checkpoint_info(path: str | Path) -> CheckpointInfo:
    payload = _read(path)
    return CheckpointInfo(
        step=int(payload["engine"]["step"]),
        epoch=float(payload["epoch"]),
        loss=payload["engine"]["last_loss"],
        dataset_fingerprint=payload["dataset_fingerprint"],
        model_config=ModelConfig.from_dict(payload["model_config"]),
        training_config=TrainingConfig.from_dict(payload["training_config"]),
        software=dict(payload["software"]),
        seed=int(payload["seed"]),
        dataset_provenance=dict(payload["dataset_provenance"]),
    )


def load_checkpoint(
    path: str | Path,
    *,
    batcher: TokenBatcher | None = None,
    pad_id: int | None = None,
    dataset_fingerprint: str | None = None,
) -> tuple[TrainingEngine, ByteBPETokenizer, CheckpointInfo]:
    """Reconstruit le moteur (modèle, optimiseur, scheduler) et, si fourni,
    restaure l'échantillonneur de lots : la suite de l'entraînement est celle
    qu'aurait produite une exécution ininterrompue. Avec `dataset_fingerprint`,
    une reprise sur un autre dataset que celui du checkpoint est refusée."""
    payload = _read(path)
    if dataset_fingerprint is not None and payload["dataset_fingerprint"] != dataset_fingerprint:
        raise ValueError(
            f"checkpoint entraîné sur le dataset {payload['dataset_fingerprint']}, "
            f"reprise demandée sur {dataset_fingerprint} : refusée"
        )
    model = ApexLM(ModelConfig.from_dict(payload["model_config"]))
    engine = TrainingEngine(model, TrainingConfig.from_dict(payload["training_config"]), pad_id=pad_id)
    engine.load_state_dict(payload["engine"])
    torch.set_rng_state(payload["rng"]["torch"])
    if batcher is not None:
        if payload["batcher"] is None:
            raise ValueError("ce checkpoint ne contient pas l'état de l'échantillonneur de lots")
        batcher.load_state_dict(payload["batcher"])
    return engine, ByteBPETokenizer.from_dict(payload["tokenizer"]), checkpoint_info(path)

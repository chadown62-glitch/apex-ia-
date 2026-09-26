"""Moteur d'entraînement d'APEX.

Chaque étape est explicite :

    forward(batch) -> loss -> backward(loss) -> optimizer_step() -> zero_grad()

`TrainingEngine` ne dépend de PyTorch qu'à travers ces opérations ; les
optimiseurs et schedulers sont décrits par `OptimizerSpec`/`SchedulerSpec`
et construits ici. Une baisse de la perte d'entraînement est une mesure
d'optimisation, jamais une preuve de capacité : voir `apex.neural.evaluation`."""

from __future__ import annotations

import math
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from apex.neural.config import OptimizerSpec, SchedulerSpec, TrainingConfig
from apex.neural.model import ApexLM, shift_for_language_modeling


def build_optimizer(model: nn.Module, spec: OptimizerSpec) -> torch.optim.Optimizer:
    """Pas de weight decay sur les normes ni les vecteurs (convention standard)."""
    unique = {id(p): p for p in model.parameters() if p.requires_grad}
    decay = [p for p in unique.values() if p.dim() >= 2]
    no_decay = [p for p in unique.values() if p.dim() < 2]
    groups = [
        {"params": decay, "weight_decay": spec.weight_decay},
        {"params": no_decay, "weight_decay": 0.0},
    ]
    if spec.name == "adamw":
        return torch.optim.AdamW(groups, lr=spec.lr, betas=spec.betas)
    if spec.name == "sgd":
        return torch.optim.SGD(groups, lr=spec.lr, momentum=spec.momentum)
    raise ValueError(f"optimiseur inconnu : {spec.name}")


def lr_multiplier(step: int, spec: SchedulerSpec, max_steps: int) -> float:
    if spec.warmup_steps and step < spec.warmup_steps:
        return (step + 1) / spec.warmup_steps
    if spec.name == "constant":
        return 1.0
    span = max(1, max_steps - spec.warmup_steps)
    progress = min(1.0, (step - spec.warmup_steps) / span)
    return spec.min_lr_ratio + (1 - spec.min_lr_ratio) * 0.5 * (1 + math.cos(math.pi * progress))


class TokenBatcher:
    """Fenêtres aléatoires d'un flux de tokens, avec un générateur PROPRE
    (son état fait partie du checkpoint : une reprise voit les mêmes lots)."""

    def __init__(self, tokens: Sequence[int], *, window: int, batch_size: int, seed: int) -> None:
        if len(tokens) < window + 1:
            raise ValueError(f"flux de {len(tokens)} tokens trop court pour des fenêtres de {window + 1}")
        self.tokens = torch.tensor(list(tokens), dtype=torch.long)
        self.window = window
        self.batch_size = batch_size
        self.generator = torch.Generator().manual_seed(seed)

    def next_batch(self) -> torch.Tensor:
        starts = torch.randint(
            0, len(self.tokens) - self.window, (self.batch_size,), generator=self.generator
        )
        return torch.stack([self.tokens[s : s + self.window + 1] for s in starts.tolist()])

    def state_dict(self) -> dict[str, Any]:
        return {"generator": self.generator.get_state()}

    def load_state_dict(self, state: dict[str, Any]) -> None:
        self.generator.set_state(state["generator"])


@dataclass(frozen=True)
class StepResult:
    step: int
    loss: float
    grad_norm: float
    lr: float


class TrainingEngine:
    def __init__(self, model: ApexLM, config: TrainingConfig, *, pad_id: int | None = None) -> None:
        self.model = model
        self.config = config
        self.pad_id = pad_id
        self.device = next(model.parameters()).device
        self.optimizer = build_optimizer(model, config.optimizer)
        self.scheduler = torch.optim.lr_scheduler.LambdaLR(
            self.optimizer, lambda step: lr_multiplier(step, config.scheduler, config.max_steps)
        )
        self.step = 0
        self.tokens_seen = 0
        self.last_loss: float | None = None
        amp_dtype = torch.float16 if self.device.type == "cuda" else torch.bfloat16
        self._amp = config.mixed_precision
        self._amp_dtype = amp_dtype
        self._scaler = torch.amp.GradScaler(
            self.device.type, enabled=self._amp and amp_dtype is torch.float16
        )

    def _autocast(self) -> Any:
        if not self._amp:
            return nullcontext()
        return torch.autocast(device_type=self.device.type, dtype=self._amp_dtype)

    def forward(self, batch: torch.Tensor) -> torch.Tensor:
        inputs, targets = shift_for_language_modeling(batch.to(self.device), self.pad_id)
        with self._autocast():
            _, loss = self.model(inputs, targets)
        if loss is None:
            raise RuntimeError("le modèle n'a pas calculé de perte")
        result: torch.Tensor = loss
        return result

    def backward(self, loss: torch.Tensor) -> None:
        scaled = self._scaler.scale(loss / self.config.grad_accumulation_steps)
        torch.autograd.backward(scaled)

    def optimizer_step(self) -> float:
        self._scaler.unscale_(self.optimizer)
        grads = [p.grad for p in self.model.parameters() if p.grad is not None]
        if not grads:
            raise RuntimeError("aucun gradient calculé : backward n'a pas eu lieu")
        max_norm = self.config.grad_clip_norm if self.config.grad_clip_norm is not None else float("inf")
        grad_norm = float(torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm))
        if not math.isfinite(grad_norm):
            raise FloatingPointError(f"norme de gradient non finie ({grad_norm}) à l'étape {self.step}")
        self._scaler.step(self.optimizer)
        self._scaler.update()
        self.scheduler.step()
        return grad_norm

    def zero_grad(self) -> None:
        self.optimizer.zero_grad(set_to_none=True)

    def train_step(self, batcher: TokenBatcher) -> StepResult:
        self.model.train()
        lr = self.optimizer.param_groups[0]["lr"]
        total = 0.0
        for _ in range(self.config.grad_accumulation_steps):
            batch = batcher.next_batch()
            loss = self.forward(batch)
            if not torch.isfinite(loss):
                raise FloatingPointError(f"perte non finie à l'étape {self.step}")
            self.backward(loss)
            total += loss.item()
            self.tokens_seen += batch[:, 1:].numel()
        grad_norm = self.optimizer_step()
        self.zero_grad()
        self.step += 1
        self.last_loss = total / self.config.grad_accumulation_steps
        return StepResult(step=self.step, loss=self.last_loss, grad_norm=grad_norm, lr=lr)

    def fit(self, batcher: TokenBatcher, steps: int | None = None) -> list[StepResult]:
        remaining = steps if steps is not None else self.config.max_steps - self.step
        return [self.train_step(batcher) for _ in range(max(0, remaining))]

    def state_dict(self) -> dict[str, Any]:
        return {
            "model": self.model.state_dict(),
            "optimizer": self.optimizer.state_dict(),
            "scheduler": self.scheduler.state_dict(),
            "scaler": self._scaler.state_dict(),
            "step": self.step,
            "tokens_seen": self.tokens_seen,
            "last_loss": self.last_loss,
        }

    def load_state_dict(self, state: dict[str, Any]) -> None:
        self.model.load_state_dict(state["model"])
        self.optimizer.load_state_dict(state["optimizer"])
        self.scheduler.load_state_dict(state["scheduler"])
        self._scaler.load_state_dict(state["scaler"])
        self.step = int(state["step"])
        self.tokens_seen = int(state["tokens_seen"])
        self.last_loss = state["last_loss"]

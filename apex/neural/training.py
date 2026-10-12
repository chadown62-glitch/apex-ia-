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
from typing import Any, Protocol

import torch
from torch import nn

from apex.neural.config import OptimizerSpec, SchedulerSpec, TrainingConfig
from apex.neural.model import ApexLM, shift_for_language_modeling


def build_optimizer(model: nn.Module, spec: OptimizerSpec, *, fused: bool = False) -> torch.optim.Optimizer:
    """Pas de weight decay sur les normes ni les vecteurs (convention standard). `fused` : noyau AdamW
    fusionné (GPU CUDA seulement — une seule passe sur les paramètres au lieu d'une par opération)."""
    unique = {id(p): p for p in model.parameters() if p.requires_grad}
    decay = [p for p in unique.values() if p.dim() >= 2]
    no_decay = [p for p in unique.values() if p.dim() < 2]
    groups = [
        {"params": decay, "weight_decay": spec.weight_decay},
        {"params": no_decay, "weight_decay": 0.0},
    ]
    if spec.name == "adamw":
        if fused:
            return torch.optim.AdamW(groups, lr=spec.lr, betas=spec.betas, fused=True)
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


class Batcher(Protocol):
    """Source de lots : `TokenBatcher` (flux en mémoire), `apex.neural.shards.ShardSampler` (corpus
    tokenisé), `apex.neural.sft.ChatBatcher` (instruction : paires entrées/cibles masquées)."""

    def next_batch(self) -> Any: ...

    def state_dict(self) -> dict[str, Any]: ...

    def load_state_dict(self, state: dict[str, Any]) -> None: ...


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


#: Le bfloat16 n'est gardé sur CPU que s'il est NETTEMENT plus rapide (au moins 10 %) : sous une charge
#: concurrente, deux mesures proches ne disent rien (le 12/10/2026, 246 ms contre 241 ms sous charge avaient
#: fait choisir le bfloat16, en réalité 2,4 fois plus lent sur ce processeur).
CPU_BF16_MIN_GAIN = 0.9


def cpu_bf16_timings(
    width: int = 384, rows: int = 2048, repeats: int = 4, rounds: int = 5
) -> tuple[float, float]:
    """(secondes en float32, secondes en bfloat16) pour un même calcul avant/arrière d'une couche linéaire,
    MESURÉS sur ce processeur. Les deux modes sont mesurés en alternance, plusieurs fois, et le MEILLEUR
    temps de chacun est gardé (la charge d'autres processus ne fait que ralentir). Sans instructions
    bfloat16 natives (AVX512-BF16, AMX), le bfloat16 est émulé : mesuré 2,4 à 3,4 fois plus lent que le
    float32 le 12/10/2026 (Xeon avec AVX-512 sans BF16)."""
    import time

    layer = nn.Linear(width, width * 2)
    x = torch.randn(rows, width)

    def context(amp: bool) -> Any:
        return torch.autocast("cpu", dtype=torch.bfloat16) if amp else nullcontext()

    for amp in (False, True):
        with context(amp):
            layer(x).sum().backward()  # préchauffage (choix des noyaux)
    best = {False: float("inf"), True: float("inf")}
    for _ in range(rounds):
        for amp in (False, True):
            started = time.perf_counter()
            for _ in range(repeats):
                with context(amp):
                    layer(x).sum().backward()
            best[amp] = min(best[amp], time.perf_counter() - started)
    return best[False], best[True]


class TrainingEngine:
    def __init__(
        self,
        model: ApexLM,
        config: TrainingConfig,
        *,
        pad_id: int | None = None,
        compile_model: bool = False,
    ) -> None:
        self.model = model
        self.config = config
        self.pad_id = pad_id
        # `torch.compile` (noyaux fusionnés) : mêmes paramètres, même état — seul le calcul avant/arrière
        # passe par le modèle compilé. Mesuré 1,64 fois plus rapide sur CPU (modèle de validation). Un échec
        # de compilation n'arrête rien : retour au calcul normal, raison gardée dans `compile_error`. Le
        # calcul compilé n'est pas identique au bit près d'un processus à l'autre (ordre des réductions).
        self._forward_model: Any = torch.compile(model) if compile_model else model
        self.compile_error: str | None = None
        self.device = next(model.parameters()).device
        self.optimizer = build_optimizer(model, config.optimizer, fused=self.device.type == "cuda")
        self.scheduler = torch.optim.lr_scheduler.LambdaLR(
            self.optimizer, lambda step: lr_multiplier(step, config.scheduler, config.max_steps)
        )
        self.step = 0
        self.tokens_seen = 0
        self.last_loss: float | None = None
        # bfloat16 partout où le matériel le calcule (CPU, GPU NVIDIA depuis Ampere — RTX 30/40) : même
        # plage que float32, aucune mise à l'échelle de la perte. float16 + GradScaler seulement sur les GPU
        # plus anciens.
        bf16 = self.device.type != "cuda" or torch.cuda.is_bf16_supported()
        amp_dtype = torch.bfloat16 if bf16 else torch.float16
        self._amp = config.mixed_precision
        self._amp_dtype = amp_dtype
        #: Choix de précision sur CPU, mesuré (jamais supposé) : le bfloat16 n'est gardé que s'il est plus
        #: rapide.
        self.precision_note: str | None = None
        if self._amp and self.device.type == "cpu":
            fp32_s, bf16_s = cpu_bf16_timings()
            self._amp = bf16_s < fp32_s * CPU_BF16_MIN_GAIN
            self.precision_note = (
                f"{'bfloat16' if self._amp else 'float32'} sur ce processeur (mesuré : float32 "
                f"{fp32_s * 1000:.0f} ms, bfloat16 {bf16_s * 1000:.0f} ms)"
            )
        self._scaler = torch.amp.GradScaler(
            self.device.type, enabled=self._amp and amp_dtype is torch.float16
        )

    def _autocast(self) -> Any:
        if not self._amp:
            return nullcontext()
        return torch.autocast(device_type=self.device.type, dtype=self._amp_dtype)

    def forward(self, batch: torch.Tensor | tuple[torch.Tensor, torch.Tensor]) -> torch.Tensor:
        """`batch` : jetons (cible = jeton suivant), ou paire (entrées, cibles) déjà alignées —
        instruction : les positions hors réponse de l'assistant valent `IGNORE_INDEX`."""
        if isinstance(batch, tuple):
            inputs, targets = batch[0].to(self.device), batch[1].to(self.device)
        else:
            inputs, targets = shift_for_language_modeling(batch.to(self.device), self.pad_id)
        with self._autocast():
            try:
                _, loss = self._forward_model(inputs, targets)
            except torch.cuda.OutOfMemoryError:
                # Mémoire insuffisante, pas un échec de compilation : l'ajustement du lot au GPU
                # (`Run.fit_memory`) s'en charge, la version compilée est gardée.
                raise
            except Exception as exc:
                if self._forward_model is self.model:
                    raise
                self.compile_error = f"{exc.__class__.__name__}: {str(exc)[:300]}"
                self._forward_model = self.model
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

    def train_step(self, batcher: Batcher) -> StepResult:
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
            self.tokens_seen += batch[1].numel() if isinstance(batch, tuple) else batch[:, 1:].numel()
        grad_norm = self.optimizer_step()
        self.zero_grad()
        self.step += 1
        self.last_loss = total / self.config.grad_accumulation_steps
        return StepResult(step=self.step, loss=self.last_loss, grad_norm=grad_norm, lr=lr)

    def fit(self, batcher: Batcher, steps: int | None = None) -> list[StepResult]:
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

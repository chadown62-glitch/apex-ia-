"""Entraînement du Cerveau APEX v2 : pré-entraînement (corpus tokenisé) puis instruction (conversations).

Une exécution = un répertoire :

    run.json          configuration (modèle, données, optimisation) — figée au premier lancement
    checkpoint.pt     dernier point de reprise complet (modèle, optimiseur, échantillonneur, générateurs)
    metrics.jsonl     une ligne par étape journalisée et par évaluation (perte, débit, mémoire)

Robustesse : reprise exacte après interruption (Ctrl+C, arrêt, plantage — le checkpoint est écrit dans un
fichier temporaire puis renommé : jamais à moitié écrit), budget de temps (`max_hours`) respecté avec un
checkpoint final, perte non finie = arrêt immédiat avec la raison (jamais un entraînement qui continue sur
des poids corrompus).

Matériel : GPU CUDA s'il est présent (bfloat16, TF32, AdamW fusionné), sinon CPU (bfloat16 si demandé,
threads réglables). Les mesures (jetons/s, mémoire GPU maximale) sont RÉELLES, lues pendant l'entraînement.
"""

from __future__ import annotations

import json
import math
import os
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import torch

from apex.brain.config import ModelConfig
from apex.brain.model import ApexLM, model_fingerprint
from apex.brain.tokenizer import ByteBPETokenizer
from apex.neural.config import OptimizerSpec, SchedulerSpec, TrainingConfig
from apex.neural.training import Batcher, TrainingEngine

CHECKPOINT = "checkpoint.pt"


@dataclass(frozen=True)
class RunConfig:
    model: dict[str, Any]
    data_dir: str
    stage: str = "pretrain"  # pretrain | sft
    weights: dict[str, float] = field(default_factory=dict)
    window: int = 512
    batch_size: int = 8
    grad_accumulation_steps: int = 1
    max_steps: int = 1000
    lr: float = 3e-3
    min_lr_ratio: float = 0.1
    warmup_steps: int = 100
    weight_decay: float = 0.1
    grad_clip_norm: float = 1.0
    mixed_precision: bool = True
    seed: int = 0
    eval_every: int = 250
    eval_tokens: int = 200_000
    log_every: int = 10
    checkpoint_every_s: float = 600.0
    threads: int = 0
    device: str = "auto"
    init_from: str | None = None  # checkpoint ou poids de départ (instruction après pré-entraînement)
    #: Recalcul des activations (mémoire GPU) ; `auto_batch` : sur GPU, si un lot ne tient pas en mémoire,
    #: il est divisé par deux et l'accumulation doublée (mêmes jetons par étape, même optimisation), puis le
    #: recalcul des activations est activé — décidé une fois, gardé dans `resolved.json` pour les reprises.
    activation_checkpointing: bool = False
    auto_batch: bool = True
    #: `torch.compile` pour l'entraînement : `None` = automatique (Linux/WSL oui ; Windows non, faute de
    #: compilateur C++ et de Triton).
    compile: bool | None = None
    #: Instruction : part des lignes de chaque lot prises dans le corpus de pré-entraînement
    #: (`replay_data_dir`) pour ne pas oublier la langue générale en apprenant à converser.
    replay_data_dir: str | None = None
    replay_fraction: float = 0.0

    def model_config(self) -> ModelConfig:
        return ModelConfig.from_dict(self.model)

    def training_config(self) -> TrainingConfig:
        return TrainingConfig(
            max_steps=self.max_steps,
            batch_size=self.batch_size,
            grad_accumulation_steps=self.grad_accumulation_steps,
            grad_clip_norm=self.grad_clip_norm,
            mixed_precision=self.mixed_precision,
            seed=self.seed,
            optimizer=OptimizerSpec(
                name="adamw", lr=self.lr, weight_decay=self.weight_decay, betas=(0.9, 0.95)
            ),
            scheduler=SchedulerSpec(
                name="cosine", warmup_steps=self.warmup_steps, min_lr_ratio=self.min_lr_ratio
            ),
        )


def pick_device(requested: str = "auto") -> torch.device:
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    device = torch.device(requested)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("GPU CUDA demandé mais indisponible (pilote NVIDIA ou PyTorch sans CUDA)")
    return device


def hardware_facts(device: torch.device) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "device": device.type,
        "torch": torch.__version__,
        "threads": torch.get_num_threads(),
    }
    if device.type == "cuda":
        props = torch.cuda.get_device_properties(device)
        facts.update(
            gpu=props.name,
            vram_gb=round(props.total_memory / 1024**3, 2),
            capability=f"{props.major}.{props.minor}",
            bf16=torch.cuda.is_bf16_supported(),
        )
    return facts


def _atomic_save(state: dict[str, Any], path: Path) -> None:
    temporary = path.with_suffix(".tmp")
    torch.save(state, temporary)
    os.replace(temporary, path)


class Run:
    """Une exécution d'entraînement reprise là où elle s'est arrêtée."""

    def __init__(
        self,
        run_dir: Path,
        config: RunConfig | None = None,
        *,
        batcher_factory: Callable[[RunConfig, str], Batcher] | None = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self.dir = run_dir
        self.log = log
        run_dir.mkdir(parents=True, exist_ok=True)
        saved = run_dir / "run.json"
        if saved.is_file():
            on_disk = RunConfig(**json.loads(saved.read_text(encoding="utf-8")))
            if config is not None and asdict(config) != asdict(on_disk):
                raise ValueError(
                    f"{run_dir} contient une autre configuration : nouveau répertoire, ou reprise "
                    "sans changer la configuration"
                )
            config = on_disk
        elif config is None:
            raise ValueError(f"aucune configuration dans {run_dir}")
        else:
            saved.write_text(json.dumps(asdict(config), indent=1) + "\n", encoding="utf-8")
        self.config = config
        if config.threads:
            torch.set_num_threads(config.threads)
        self.device = pick_device(config.device)
        if self.device.type == "cuda":
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
        torch.manual_seed(config.seed)
        self.model = ApexLM(config.model_config()).to(self.device)
        self.model.activation_checkpointing = config.activation_checkpointing
        if config.init_from:
            self._initialize_from(Path(config.init_from))
        compile_model = config.compile if config.compile is not None else sys.platform.startswith("linux")
        self.engine = TrainingEngine(self.model, config.training_config(), compile_model=compile_model)
        factory = batcher_factory or default_batcher
        self.batcher = factory(config, "train")
        self.resolved_file = run_dir / "resolved.json"
        if self.resolved_file.is_file():
            self._apply(json.loads(self.resolved_file.read_text(encoding="utf-8")))
        self.eval_batcher_factory = factory
        self.checkpoint = run_dir / CHECKPOINT
        if self.checkpoint.is_file():
            self._resume()

    def _apply(self, resolved: dict[str, Any]) -> None:
        from dataclasses import replace

        self.engine.config = replace(
            self.engine.config,
            batch_size=int(resolved["batch_size"]),
            grad_accumulation_steps=int(resolved["grad_accumulation_steps"]),
        )
        self.batcher.batch_size = int(resolved["batch_size"])  # type: ignore[attr-defined]
        self.model.activation_checkpointing = bool(resolved["activation_checkpointing"])

    def fit_memory(self) -> dict[str, Any]:
        """GPU : essaie un passage avant/arrière du lot configuré (jetons aléatoires : l'échantillonneur
        n'est pas consommé) en comptant la mémoire de l'optimiseur ; réduit le lot si besoin. Rend la
        configuration retenue (écrite dans `resolved.json`)."""
        import torch

        batch = self.engine.config.batch_size
        accumulation = self.engine.config.grad_accumulation_steps
        checkpointing = self.model.activation_checkpointing
        torch.cuda.empty_cache()
        # Budget = ce que ce processus détient déjà (le modèle) + la mémoire LIBRE, pas la VRAM totale :
        # sous Windows, l'affichage et les autres programmes en occupent une partie, et le pilote déborde
        # alors en mémoire système (beaucoup plus lent) au lieu de signaler le manque.
        free, total = torch.cuda.mem_get_info(self.device)
        available = torch.cuda.memory_allocated(self.device) + free
        params = sum(p.numel() * p.element_size() for p in self.model.parameters())
        while True:
            self.model.activation_checkpointing = checkpointing
            try:
                torch.cuda.empty_cache()
                torch.cuda.reset_peak_memory_stats(self.device)
                window = self.config.window
                tokens = torch.randint(0, self.config.model_config().vocab_size, (batch, window + 1))
                loss = self.engine.forward(tokens)
                loss.backward()
                self.engine.zero_grad()
                peak = torch.cuda.max_memory_allocated(self.device)
                needed = peak + 2 * params  # états d'Adam (deux moments en float32), alloués au premier pas
                if needed > 0.9 * available:
                    raise torch.cuda.OutOfMemoryError(f"{needed / 1024**3:.2f} Go nécessaires")
                break
            except torch.cuda.OutOfMemoryError as exc:
                self.engine.zero_grad()
                torch.cuda.empty_cache()
                if batch > 1:
                    batch //= 2
                    accumulation *= 2
                elif not checkpointing:
                    checkpointing = True
                else:
                    raise RuntimeError(
                        f"le modèle ne tient pas dans la mémoire du GPU même avec un lot de 1 et le "
                        f"recalcul des activations ({exc}) : choisir un préréglage plus petit"
                    ) from exc
        resolved = {
            "batch_size": batch,
            "grad_accumulation_steps": accumulation,
            "activation_checkpointing": checkpointing,
            "peak_memory_gb": round(peak / 1024**3, 3),
            "optimizer_state_gb": round(2 * params / 1024**3, 3),
            "vram_gb": round(total / 1024**3, 3),
            "vram_available_gb": round(available / 1024**3, 3),
        }
        self.resolved_file.write_text(json.dumps(resolved, indent=1) + "\n", encoding="utf-8")
        self._apply(resolved)
        self.log(
            f"mémoire GPU : lot {batch}, accumulation {accumulation}, recalcul des activations "
            f"{'oui' if checkpointing else 'non'}, pic mesuré {resolved['peak_memory_gb']} Go "
            f"(+ {resolved['optimizer_state_gb']} Go d'optimiseur) ; {resolved['vram_available_gb']} Go "
            f"disponibles sur {resolved['vram_gb']} Go"
        )
        return resolved

    def _initialize_from(self, source: Path) -> None:
        state = torch.load(source, map_location="cpu", weights_only=False)
        weights = state["engine"]["model"] if "engine" in state else state
        self.model.load_state_dict(weights)
        self.log(f"poids initiaux : {source} ({model_fingerprint(self.model)[:23]}…)")

    def _resume(self) -> None:
        state = torch.load(self.checkpoint, map_location="cpu", weights_only=False)
        self.engine.load_state_dict(state["engine"])
        self.batcher.load_state_dict(state["batcher"])
        torch.set_rng_state(state["rng"])
        self.log(f"reprise à l'étape {self.engine.step} ({self.engine.tokens_seen / 1e6:.1f} M jetons vus)")

    def save(self) -> None:
        _atomic_save(
            {
                "engine": self.engine.state_dict(),
                "batcher": self.batcher.state_dict(),
                "rng": torch.get_rng_state(),
                "config": asdict(self.config),
                "saved_at": time.time(),
            },
            self.checkpoint,
        )

    def _metric(self, row: dict[str, Any]) -> None:
        with (self.dir / "metrics.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    @torch.no_grad()
    def evaluate(self) -> dict[str, Any]:
        """Perte de validation par groupe, sur des fenêtres DÉTERMINISTES (mêmes fenêtres à chaque
        évaluation)."""
        from apex.neural.evaluation_v2 import validation_loss

        self.model.eval()
        result = validation_loss(self.model, self.config, self.device)
        self.model.train()
        return result

    def train(
        self, *, max_hours: float | None = None, stop_after_steps: int | None = None
    ) -> dict[str, Any]:
        started = time.time()
        last_checkpoint = time.time()
        interrupted = {"signal": None}

        def _handler(signum: int, _frame: Any) -> None:
            interrupted["signal"] = signum

        previous = {sig: signal.signal(sig, _handler) for sig in (signal.SIGINT, signal.SIGTERM)}
        if self.device.type == "cuda" and self.config.auto_batch and not self.resolved_file.is_file():
            self.fit_memory()
        facts = hardware_facts(self.device)
        facts["compiled"] = self.engine._forward_model is not self.model
        facts["mixed_precision"] = self.engine._amp
        if self.engine.precision_note:
            facts["precision"] = self.engine.precision_note
        self._metric(
            {
                "event": "start",
                "step": self.engine.step,
                "time": started,
                **facts,
                "parameters": self.model.num_parameters(),
            }
        )
        self.log(
            f"entraînement {self.config.stage} : {self.model.num_parameters() / 1e6:.2f} M paramètres, "
            f"{facts}"
        )
        tokens_window, time_window = 0, time.perf_counter()
        steps_done = 0
        reason = "max_steps"
        try:
            while self.engine.step < self.config.max_steps:
                if interrupted["signal"] is not None:
                    reason = f"signal {interrupted['signal']}"
                    break
                if max_hours is not None and time.time() - started > max_hours * 3600:
                    reason = "budget de temps"
                    break
                if stop_after_steps is not None and steps_done >= stop_after_steps:
                    reason = "nombre d'étapes demandé"
                    break
                before = self.engine.tokens_seen
                result = self.engine.train_step(self.batcher)
                steps_done += 1
                if self.engine.compile_error and not getattr(self, "_compile_reported", False):
                    self._compile_reported = True
                    self._metric(
                        {
                            "event": "compile_fallback",
                            "step": result.step,
                            "error": self.engine.compile_error,
                        }
                    )
                    self.log(f"torch.compile indisponible, calcul normal : {self.engine.compile_error}")
                tokens_window += self.engine.tokens_seen - before
                if result.step % self.config.log_every == 0:
                    elapsed = time.perf_counter() - time_window
                    row = {
                        "step": result.step,
                        "loss": round(result.loss, 5),
                        "lr": result.lr,
                        "grad_norm": round(result.grad_norm, 4),
                        "tokens_seen": self.engine.tokens_seen,
                        "tokens_per_s": round(tokens_window / elapsed, 1) if elapsed > 0 else None,
                        "elapsed_s": round(time.time() - started, 1),
                    }
                    if self.device.type == "cuda":
                        row["vram_max_gb"] = round(
                            torch.cuda.max_memory_allocated(self.device) / 1024**3, 3
                        )
                    self._metric(row)
                    self.log(
                        f"étape {result.step}/{self.config.max_steps} perte {result.loss:.4f} "
                        f"{row['tokens_per_s']} jetons/s"
                    )
                    tokens_window, time_window = 0, time.perf_counter()
                if self.config.eval_every and result.step % self.config.eval_every == 0:
                    evaluation = self.evaluate()
                    self._metric({"event": "eval", "step": result.step, **evaluation})
                    self.log(f"évaluation étape {result.step} : {evaluation}")
                if time.time() - last_checkpoint > self.config.checkpoint_every_s:
                    self.save()
                    last_checkpoint = time.time()
        finally:
            self.save()
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        final = {
            "event": "stop",
            "reason": reason,
            "step": self.engine.step,
            "tokens_seen": self.engine.tokens_seen,
            "elapsed_s": round(time.time() - started, 1),
            "loss": self.engine.last_loss,
        }
        self._metric(final)
        return final


def default_batcher(config: RunConfig, split: str) -> Batcher:
    if config.stage == "sft":
        from apex.neural.sft import ChatBatcher, ReplayBatcher

        chat = ChatBatcher(
            Path(config.data_dir),
            split=split,
            window=config.window,
            batch_size=config.batch_size,
            seed=config.seed,
        )
        if not config.replay_data_dir or config.replay_fraction <= 0:
            return chat
        from apex.neural.shards import ShardSampler

        corpus = ShardSampler(
            Path(config.replay_data_dir),
            split=split,
            window=config.window,
            batch_size=1,
            seed=config.seed + 1,
            weights=config.weights or None,
        )
        return ReplayBatcher(chat, corpus, replay_fraction=config.replay_fraction, seed=config.seed)
    from apex.neural.shards import ShardSampler

    return ShardSampler(
        Path(config.data_dir),
        split=split,
        window=config.window,
        batch_size=config.batch_size,
        seed=config.seed,
        weights=config.weights or None,
    )


def export_weights(
    run_dir: Path,
    tokenizer: ByteBPETokenizer,
    target: Path,
    provenance: dict[str, Any],
    *,
    quantization: str | None = None,
) -> dict[str, Any]:
    """Poids SERVIS (format `apex.brain.artifact`) depuis le checkpoint d'une exécution ; `quantization` :
    `apex-q5` pour des poids APEX-Q5."""
    from apex.brain.artifact import export

    state = torch.load(run_dir / CHECKPOINT, map_location="cpu", weights_only=False)
    config = RunConfig(**state["config"])
    model = ApexLM(config.model_config())
    model.load_state_dict(state["engine"]["model"])
    model.eval()
    return export(model, tokenizer, target, provenance, quantization=quantization)


def run_summary(run_dir: Path) -> dict[str, Any]:
    """Résumé MESURÉ d'une exécution (lu dans `metrics.jsonl`) : étapes, jetons, durée, matériel, dernières
    mesures de validation."""
    rows = [
        json.loads(line)
        for line in (run_dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    starts = [r for r in rows if r.get("event") == "start"]
    stops = [r for r in rows if r.get("event") == "stop"]
    evals = [r for r in rows if r.get("event") == "eval"]
    speeds = [r["tokens_per_s"] for r in rows if r.get("tokens_per_s")]
    config = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    return {
        "stage": config.get("stage"),
        "steps": stops[-1]["step"] if stops else None,
        "max_steps": config.get("max_steps"),
        "tokens_seen": stops[-1]["tokens_seen"] if stops else None,
        "training_time_s": round(sum(r.get("elapsed_s", 0.0) for r in stops), 1),
        "sessions": len(starts),
        "hardware": {k: starts[-1].get(k) for k in ("device", "gpu", "vram_gb", "torch", "threads")}
        if starts
        else {},
        "median_tokens_per_s": sorted(speeds)[len(speeds) // 2] if speeds else None,
        "final_loss": stops[-1].get("loss") if stops else None,
        "last_validation": {k: v for k, v in evals[-1].items() if k not in ("event",)} if evals else None,
        "config": {
            k: config.get(k)
            for k in (
                "window",
                "batch_size",
                "grad_accumulation_steps",
                "lr",
                "warmup_steps",
                "min_lr_ratio",
                "weights",
                "seed",
            )
        },
    }


def perplexity(loss: float) -> float:
    return math.exp(min(loss, 50.0))

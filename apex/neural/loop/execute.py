"""Exécution RÉELLE d'un plan : poids de départ -> flux de données du plan ->
entraînement (forward, loss, backward, optimizer.step) -> checkpoint dans
l'ArtifactStore -> mesures déclarées.

L'exécuteur ne décide de rien : il rapporte ce qu'il a fait et ce qu'il a
mesuré ; le Judge re-mesure lui-même. Garde-fous vérifiés à CHAQUE pas :
arrêt demandé, délai, budget. Un checkpoint de reprise est écrit tous les
`checkpoint_every` pas : après un crash, la même expérience reprend là où
elle s'était arrêtée (état de l'optimiseur, du scheduler, de l'échantillonneur
et du générateur aléatoire compris) au lieu de repartir de zéro."""

from __future__ import annotations

import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import torch

from apex.artifacts.provider import ArtifactStoreProvider
from apex.errors import TimeoutExceeded
from apex.neural.checkpoint import load_checkpoint, save_checkpoint
from apex.neural.config import OptimizerSpec, SchedulerSpec, TrainingConfig
from apex.neural.evaluation import EvalReport, evaluate
from apex.neural.loop.plan import ExperimentPlan
from apex.neural.loop.world import PRIMARY, World
from apex.neural.model import ApexLM
from apex.neural.training import TokenBatcher, TrainingEngine


class StopRequested(RuntimeError):
    """Arrêt demandé proprement (drapeau ou fichier STOP)."""


@dataclass(frozen=True)
class ExecutionResult:
    artifact: str
    checkpoint_sha256: str
    claimed_reports: dict[str, EvalReport]
    curve: list[dict[str, float]]
    steps: int
    tokens_seen: int
    training_candidate_ids: list[str]
    resumed_from_step: int
    duration_s: float
    software: dict[str, str] = field(default_factory=dict)


def measure(model: ApexLM, world: World) -> dict[str, EvalReport]:
    return measure_with_details(model, world)[0]


def measure_with_details(
    model: ApexLM, world: World
) -> tuple[dict[str, EvalReport], dict[str, list[dict[str, Any]]]]:
    """Toutes les suites du monde. Chaque document n'est évalué qu'une fois :
    les tranches par domaine sont des sous-ensembles des scores du held-out
    (scores par document indépendants), avec l'empreinte de leur benchmark.
    Les suites de CAPACITÉ sont mesurées par exécution réelle dans le sandbox
    (jamais en dégradé) ; leurs détails par tâche sont renvoyés à part."""
    reports = measure_text(model, world)
    details: dict[str, list[dict[str, Any]]] = {}
    if world.capabilities:
        from apex.capabilities.registry import sandbox_provider

        provider = sandbox_provider()
        for capability, data in world.capabilities.values():
            evaluation = capability.evaluate(model, world.tokenizer, data, provider=provider)
            reports.update(evaluation.reports)
            details.update(evaluation.details)
    return reports, details


def measure_text(model: ApexLM, world: World) -> dict[str, EvalReport]:
    heldout = evaluate(model, world.tokenizer, world.benchmarks[PRIMARY])
    reports = {
        PRIMARY: heldout,
        "validation": evaluate(model, world.tokenizer, world.benchmarks["validation"]),
    }
    by_id = {s.doc_id: s for s in heldout.scores}
    for name, bench in world.benchmarks.items():
        if name.startswith(f"{PRIMARY}:"):
            reports[name] = EvalReport(
                name,
                bench.fingerprint,
                heldout.model_fingerprint,
                tuple(by_id[doc_id] for doc_id, _ in bench.documents if doc_id in by_id),
            )
    return reports


def load_model_from_artifact(store: ArtifactStoreProvider, artifact: str, world: World) -> ApexLM:
    """Recharge un modèle depuis l'ArtifactStore (intégrité vérifiée par le
    hash) en exigeant qu'il ait été entraîné sur le dataset de ce monde, avec
    son tokenizer."""
    data = store.get(artifact)
    with tempfile.TemporaryDirectory(prefix="apex-loop-") as tmp:
        path = Path(tmp) / "model.pt"
        path.write_bytes(data)
        engine, tokenizer, _ = load_checkpoint(path, dataset_fingerprint=world.dataset.fingerprint)
    if tokenizer.fingerprint() != world.tokenizer.fingerprint():
        raise ValueError("checkpoint entraîné avec un autre tokenizer que celui du monde")
    return engine.model


def initial_model(world: World, seed: int) -> ApexLM:
    torch.manual_seed(seed)
    return ApexLM(world.spec.model_config(world.tokenizer.vocab_size))


def execute(
    plan: ExperimentPlan,
    world: World,
    store: ArtifactStoreProvider,
    run_dir: Path,
    *,
    stop: threading.Event | None = None,
    stop_file: Path | None = None,
    checkpoint_every: int = 50,
    on_step: Callable[[int], None] | None = None,
) -> ExecutionResult:
    started = time.monotonic()
    recipe = plan.recipe
    run_dir.mkdir(parents=True, exist_ok=True)
    resume_path = run_dir / "resume.pt"
    stream, used_ids = world.token_stream(recipe.mix, recipe.tokens, seed=recipe.seed)
    batcher = TokenBatcher(
        stream, window=world.spec.context_length, batch_size=recipe.batch_size, seed=recipe.seed
    )
    training = TrainingConfig(
        max_steps=recipe.steps,
        batch_size=recipe.batch_size,
        seed=recipe.seed,
        optimizer=OptimizerSpec(lr=recipe.lr, weight_decay=recipe.weight_decay),
        scheduler=SchedulerSpec(name="cosine", warmup_steps=recipe.warmup_steps),
    )
    resumed_from = 0
    if resume_path.exists():
        engine, _, info = load_checkpoint(
            resume_path, batcher=batcher, dataset_fingerprint=world.dataset.fingerprint
        )
        resumed_from = info.step
    else:
        if recipe.init == "champion":
            model = load_model_from_artifact(store, str(plan.baseline["artifact"]), world)
        else:
            model = initial_model(world, recipe.seed)
        torch.manual_seed(recipe.seed)
        engine = TrainingEngine(model, training)

    def save(path: Path) -> str:
        return save_checkpoint(
            path,
            engine,
            tokenizer=world.tokenizer,
            batcher=batcher,
            dataset_fingerprint=world.dataset.fingerprint,
            dataset_provenance={
                "world": world.fingerprint,
                "plan": plan.fingerprint,  # identité déterministe (pas d'uuid) : même plan → mêmes octets
                "training_candidates": len(used_ids),
            },
            dataset_tokens=len(stream),
        )

    curve: list[dict[str, float]] = []
    while engine.step < recipe.steps:
        if (stop is not None and stop.is_set()) or (stop_file is not None and stop_file.exists()):
            save(resume_path)
            raise StopRequested(f"arrêt demandé au pas {engine.step}")
        if time.monotonic() - started > plan.budget.timeout_s:
            raise TimeoutExceeded(f"expérience {plan.experiment_id}", plan.budget.timeout_s)
        result = engine.train_step(batcher)
        if result.step == 1 or result.step % 25 == 0 or result.step == recipe.steps:
            curve.append({"step": result.step, "loss": round(result.loss, 4), "lr": result.lr})
        if on_step is not None:
            on_step(result.step)
        if result.step % checkpoint_every == 0 and result.step < recipe.steps:
            save(resume_path)

    final = run_dir / "final.pt"
    sha = save(final)
    metadata = store.put(final.read_bytes(), content_type="application/x-apex-checkpoint")
    claimed = measure(engine.model, world)
    return ExecutionResult(
        artifact=metadata.content_hash,
        checkpoint_sha256=sha,
        claimed_reports=claimed,
        curve=curve,
        steps=engine.step,
        tokens_seen=engine.tokens_seen,
        training_candidate_ids=used_ids,
        resumed_from_step=resumed_from,
        duration_s=round(time.monotonic() - started, 2),
        software={"torch": torch.__version__},
    )


def reports_to_dict(reports: dict[str, EvalReport]) -> dict[str, Any]:
    return {name: report.to_dict() for name, report in reports.items()}

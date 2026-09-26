"""Solveur CODE REPAIR : le modèle APEX classe les correctifs candidats, les
K premiers sont EXÉCUTÉS réellement dans le sandbox, dans l'ordre du
classement ; l'échec d'une tentative (sortie fausse, exception, délai) fait
passer à la suivante. Le score d'une tâche vient de l'exécution, jamais du
modèle :

    coût = (rang du premier candidat qui passe TOUS les tests - 1) / K,
    ou 1.0 si aucun des K candidats exécutés ne passe.

Chaque tentative est consignée en distinguant : code proposé (édition),
code exécuté, tests exécutés, tests réussis, première cause d'échec.

Le classement est une probabilité sous le modèle : log-vraisemblance MOYENNE
par jeton de la fonction candidate (règle fixée par le contrat : la somme
favorise mécaniquement les candidats les plus courts — biais constaté sur
l'expérience de faisabilité)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import torch
import torch.nn.functional as F

from apex.capabilities.code_repair.benchmark import RepairInstance, RepairSuite, RepairTaskView
from apex.capabilities.code_repair.harness import TestOutcome, judge_run, run_sources
from apex.capabilities.code_repair.mutation import Edit, candidates
from apex.neural.evaluation import DocScore, EvalReport, model_fingerprint
from apex.neural.model import ApexLM
from apex.neural.tokenizer import ByteBPETokenizer
from apex.sandbox.provider import SandboxProvider

METRIC = "repair_cost"


class TaskTooLong(ValueError):
    """Une fonction candidate dépasse le contexte du modèle."""


@dataclass(frozen=True)
class RankedCandidate:
    source: str
    edit: Edit
    score: float  # NLL moyenne par jeton (plus petit = plus probable)


@torch.no_grad()
def rank_candidates(
    model: ApexLM, tokenizer: ByteBPETokenizer, task: RepairTaskView, *, batch_size: int = 64
) -> list[RankedCandidate]:
    cands = candidates(task.buggy)
    if not cands:
        return []
    was_training = model.training
    model.eval()
    context = model.config.context_length
    encoded = [tokenizer.encode(text, add_bos=True, add_eos=True) for text, _ in cands]
    longest = max(len(ids) for ids in encoded)
    if longest > context + 1:
        raise TaskTooLong(f"{task.instance_id} : {longest} jetons > contexte {context + 1}")
    scores: list[float] = []
    try:
        for start in range(0, len(encoded), batch_size):
            batch = encoded[start : start + batch_size]
            width = max(len(ids) for ids in batch)
            pad = tokenizer.pad_id
            ids = torch.tensor([row + [pad] * (width - len(row)) for row in batch])
            logits, _ = model(ids[:, :-1])
            nll = F.cross_entropy(
                logits.float().transpose(1, 2), ids[:, 1:], reduction="none"
            )  # [batch, width - 1]
            for row, length in enumerate(len(r) for r in batch):
                # Masque causal : le remplissage en fin de séquence n'influence pas les positions réelles.
                scores.append(float(nll[row, : length - 1].mean()))
    finally:
        model.train(was_training)
    ranked = [RankedCandidate(text, edit, score) for (text, edit), score in zip(cands, scores, strict=True)]
    # Égalités départagées par l'ordre déterministe du générateur.
    order = sorted(range(len(ranked)), key=lambda i: (ranked[i].score, i))
    return [ranked[i] for i in order]


@dataclass(frozen=True)
class Attempt:
    rank: int
    edit: str
    candidate_sha256: str
    outcome: TestOutcome

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "edit": self.edit,
            "candidate": self.candidate_sha256[:19],
            **self.outcome.to_dict(),
        }


@dataclass(frozen=True)
class InstanceResult:
    instance_id: str
    fid: str
    category: str
    level: int
    n_candidates: int
    attempts: tuple[Attempt, ...]
    first_pass_rank: int | None
    cost: float

    @property
    def solved(self) -> bool:
        return self.first_pass_rank is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance_id,
            "fid": self.fid,
            "category": self.category,
            "level": self.level,
            "candidates": self.n_candidates,
            "solved": self.solved,
            "rank": self.first_pass_rank,
            "cost": round(self.cost, 4),
            "attempts": [a.to_dict() for a in self.attempts],
        }


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def solve_suite(
    model: ApexLM,
    tokenizer: ByteBPETokenizer,
    suite: RepairSuite,
    provider: SandboxProvider,
    *,
    attempts: int = 3,
    child_timeout_s: float = 1.0,
    workers: int = 4,
) -> list[InstanceResult]:
    """Classe (modèle) puis exécute (sandbox) ; aucun score sans exécution."""
    views = [inst.view() for inst in suite.instances]
    ranked = [rank_candidates(model, tokenizer, view) for view in views]

    def execute(pair: tuple[RepairInstance, list[RankedCandidate]]) -> InstanceResult:
        instance, order = pair
        top = order[:attempts]
        runs = run_sources(
            provider,
            name=instance.name,
            sources=[c.source for c in top],
            inputs=list(instance.inputs),
            child_timeout_s=child_timeout_s,
        )
        tried = tuple(
            Attempt(rank, cand.edit.describe(), _sha(cand.source), judge_run(run, instance.expected))
            for rank, (cand, run) in enumerate(zip(top, runs, strict=True), start=1)
        )
        first = next((a.rank for a in tried if a.outcome.passed), None)
        cost = (first - 1) / attempts if first is not None else 1.0
        return InstanceResult(
            instance.instance_id,
            instance.fid,
            instance.category,
            instance.level,
            len(order),
            tried,
            first,
            cost,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(execute, zip(suite.instances, ranked, strict=True)))


def suite_report(suite: RepairSuite, results: Sequence[InstanceResult], model: ApexLM) -> EvalReport:
    """Un score PAR FONCTION (moyenne de ses bugs) : les bugs d'une même
    fonction ne sont pas des observations indépendantes."""
    by_fn: dict[str, list[InstanceResult]] = {}
    for result in results:
        by_fn.setdefault(result.fid, []).append(result)
    scores = tuple(
        DocScore(
            fid,
            sum(r.cost for r in group) / len(group),  # valeur de la métrique (coût, plus bas = mieux)
            sum(r.solved for r in group) / len(group),  # fraction de bugs réparés
            sum(len(r.attempts) for r in group),  # exécutions réelles
            len(group),  # bugs de la fonction
        )
        for fid, group in sorted(by_fn.items())
    )
    return EvalReport(suite.name, suite.fingerprint, model_fingerprint(model), scores, metric=METRIC)

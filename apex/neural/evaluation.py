"""Évaluation du modèle APEX et Judge d'acceptation d'une nouvelle version.

Mesure : bits par octet (BPB) par document, indépendants du tokenizer, et
précision top-1 du prochain token. Une baisse de loss d'ENTRAÎNEMENT n'est
jamais une preuve : seules comptent les mesures sur des benchmarks séparés
(held-out test jamais vu, jeux de garde), comparées document par document.

Le Judge refuse :
- une comparaison sur des benchmarks différents (même nom, autre empreinte) :
  changer le benchmark ne peut pas masquer une régression ;
- toute régression significative ou au-delà de la tolérance sur un jeu de
  garde, même si le jeu principal s'améliore : une moyenne ne masque pas une
  perte ;
- une amélioration non distinguable du bruit (INCONCLUSIVE, jamais ACCEPT)."""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import torch
import torch.nn.functional as F

from apex.knowledge.filters import canonical
from apex.neural.dataset import Split, TrainingDataset
from apex.neural.model import IGNORE_INDEX, ApexLM
from apex.neural.tokenizer import ByteBPETokenizer
from apex.stattest.comparator import StatTest
from apex.stattest.model import StatTestResult, StatTestVerdict


@dataclass(frozen=True)
class Benchmark:
    name: str
    documents: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        """Un document répété gonfle l'échantillon apparié du Judge et peut
        fabriquer une significativité : identifiants et textes (vue
        canonique) doivent être uniques."""
        ids = [doc_id for doc_id, _ in self.documents]
        if len(set(ids)) != len(ids):
            raise ValueError(f"benchmark « {self.name} » : identifiants de documents répétés")
        texts = [" ".join(canonical(text).lower().split()) for _, text in self.documents]
        if len(set(texts)) != len(texts):
            raise ValueError(f"benchmark « {self.name} » : textes de documents répétés")

    @property
    def fingerprint(self) -> str:
        payload = json.dumps([self.name, list(self.documents)], ensure_ascii=False)
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_dataset(cls, name: str, dataset: TrainingDataset, split: Split) -> Benchmark:
        return cls(name, tuple((c.candidate_id, c.text) for c in dataset.splits[split]))


@dataclass(frozen=True)
class DocScore:
    doc_id: str
    bits_per_byte: float
    token_accuracy: float
    n_bytes: int
    n_tokens: int


@dataclass(frozen=True)
class EvalReport:
    benchmark: str
    benchmark_fingerprint: str
    model_fingerprint: str
    scores: tuple[DocScore, ...]

    @property
    def bits_per_byte(self) -> float:
        """Moyenne pondérée par les octets (le BPB du benchmark entier)."""
        total = sum(s.n_bytes for s in self.scores)
        return sum(s.bits_per_byte * s.n_bytes for s in self.scores) / total if total else math.nan

    @property
    def token_accuracy(self) -> float:
        total = sum(s.n_tokens for s in self.scores)
        return sum(s.token_accuracy * s.n_tokens for s in self.scores) / total if total else math.nan

    def to_dict(self) -> dict[str, object]:
        return {
            **self.summary(),
            "scores": [
                [s.doc_id, s.bits_per_byte, s.token_accuracy, s.n_bytes, s.n_tokens] for s in self.scores
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvalReport:
        return cls(
            str(data["benchmark"]),
            str(data["benchmark_fingerprint"]),
            str(data["model_fingerprint"]),
            tuple(DocScore(str(d), float(b), float(a), int(n), int(t)) for d, b, a, n, t in data["scores"]),
        )

    def summary(self) -> dict[str, object]:
        return {
            "benchmark": self.benchmark,
            "benchmark_fingerprint": self.benchmark_fingerprint,
            "model_fingerprint": self.model_fingerprint,
            "documents": len(self.scores),
            "bits_per_byte": round(self.bits_per_byte, 5),
            "token_accuracy": round(self.token_accuracy, 5),
        }


def model_fingerprint(model: ApexLM) -> str:
    """SHA-256 des poids réels (octets des tenseurs, dans l'ordre du state_dict)."""
    digest = hashlib.sha256()
    for name, tensor in model.state_dict().items():
        data = tensor.detach().to("cpu").contiguous()
        digest.update(name.encode())
        digest.update(ctypes.string_at(data.data_ptr(), data.numel() * data.element_size()))
    return "sha256:" + digest.hexdigest()


@torch.no_grad()
def evaluate(model: ApexLM, tokenizer: ByteBPETokenizer, benchmark: Benchmark) -> EvalReport:
    was_training = model.training
    model.eval()
    context = model.config.context_length
    device = next(model.parameters()).device
    scores: list[DocScore] = []
    try:
        for doc_id, text in benchmark.documents:
            ids = tokenizer.encode(text, add_bos=True, add_eos=True)
            windows = []
            for start in range(0, max(1, len(ids) - 1), context):
                chunk = ids[start : start + context + 1]
                if len(chunk) >= 2:
                    windows.append(chunk)
            nll_total, correct, n_tokens, n_bytes = 0.0, 0, 0, 0
            for chunk in windows:
                inputs = torch.tensor([chunk[:-1]], device=device)
                targets = torch.tensor([chunk[1:]], device=device)
                logits, _ = model(inputs)
                nll = F.cross_entropy(
                    logits[0].float(), targets[0], reduction="none", ignore_index=IGNORE_INDEX
                )
                nll_total += float(nll.sum())
                correct += int((logits[0].argmax(-1) == targets[0]).sum())
                n_tokens += targets.numel()
                n_bytes += sum(len(tokenizer.token_bytes(t)) for t in chunk[1:])
            if n_tokens == 0:
                continue
            bpb = nll_total / math.log(2) / max(1, n_bytes)
            scores.append(DocScore(doc_id, bpb, correct / n_tokens, n_bytes, n_tokens))
    finally:
        model.train(was_training)
    return EvalReport(benchmark.name, benchmark.fingerprint, model_fingerprint(model), tuple(scores))


class JudgeDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class SuiteComparison:
    suite: str
    baseline_bpb: float
    candidate_bpb: float
    baseline_accuracy: float
    candidate_accuracy: float
    improvement: StatTestResult

    def to_dict(self) -> dict[str, object]:
        return {
            "suite": self.suite,
            "baseline_bpb": round(self.baseline_bpb, 5),
            "candidate_bpb": round(self.candidate_bpb, 5),
            "baseline_accuracy": round(self.baseline_accuracy, 5),
            "candidate_accuracy": round(self.candidate_accuracy, 5),
            "bpb_gain_mean": self.improvement.observed_diff,
            "bpb_gain_ci": [self.improvement.ci_lower, self.improvement.ci_upper],
            "verdict": self.improvement.verdict.value,
            "n": self.improvement.sample_size,
        }


@dataclass(frozen=True)
class NeuralVerdict:
    decision: JudgeDecision
    reasons: tuple[str, ...]
    comparisons: tuple[SuiteComparison, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "suites": [c.to_dict() for c in self.comparisons],
        }


class NeuralJudge:
    """Compare une version candidate à la version de référence, suite par
    suite, document par document (bootstrap apparié de `apex.stattest`)."""

    def __init__(self, *, primary: str, max_guard_regression_bpb: float = 0.01, seed: int = 0) -> None:
        self.primary = primary
        self.max_guard_regression_bpb = max_guard_regression_bpb
        self.stat = StatTest(n_bootstrap=2000, confidence_level=0.95, seed=seed)

    def _compare(self, suite: str, baseline: EvalReport, candidate: EvalReport) -> SuiteComparison:
        base = {s.doc_id: s for s in baseline.scores}
        cand = {s.doc_id: s for s in candidate.scores}
        ids = sorted(base)
        # Gain = BPB de référence - BPB candidat : positif si le candidat est meilleur.
        result = self.stat.compare_paired(
            [cand[i].bits_per_byte for i in ids], [base[i].bits_per_byte for i in ids]
        )
        return SuiteComparison(
            suite,
            baseline.bits_per_byte,
            candidate.bits_per_byte,
            baseline.token_accuracy,
            candidate.token_accuracy,
            result,
        )

    def judge(self, baseline: dict[str, EvalReport], candidate: dict[str, EvalReport]) -> NeuralVerdict:
        if self.primary not in baseline:
            return NeuralVerdict(JudgeDecision.REJECT, (f"suite principale « {self.primary} » absente",))
        if set(baseline) != set(candidate):
            return NeuralVerdict(
                JudgeDecision.REJECT,
                (f"suites différentes : référence {sorted(baseline)}, candidat {sorted(candidate)}",),
            )
        for suite in baseline:
            if baseline[suite].benchmark_fingerprint != candidate[suite].benchmark_fingerprint:
                return NeuralVerdict(
                    JudgeDecision.REJECT,
                    (f"benchmark « {suite} » modifié entre les deux mesures : comparaison refusée",),
                )
            if {s.doc_id for s in baseline[suite].scores} != {s.doc_id for s in candidate[suite].scores}:
                return NeuralVerdict(
                    JudgeDecision.REJECT, (f"documents évalués différents sur « {suite} »",)
                )
            if not all(math.isfinite(s.bits_per_byte) for s in candidate[suite].scores):
                return NeuralVerdict(JudgeDecision.REJECT, (f"mesure non finie sur « {suite} »",))
        comparisons = tuple(self._compare(s, baseline[s], candidate[s]) for s in sorted(baseline))
        reasons: list[str] = []
        for comp in comparisons:
            gain = comp.improvement
            if gain.verdict is StatTestVerdict.SIGNIFICANT and (gain.ci_upper or 0.0) < 0.0:
                reasons.append(f"régression significative sur « {comp.suite} » ({gain.reason})")
            elif comp.suite != self.primary and (
                comp.candidate_bpb - comp.baseline_bpb > self.max_guard_regression_bpb
            ):
                reasons.append(
                    f"régression de {comp.candidate_bpb - comp.baseline_bpb:.4f} bpb sur la garde "
                    f"« {comp.suite} » (tolérance {self.max_guard_regression_bpb})"
                )
        if reasons:
            return NeuralVerdict(JudgeDecision.REJECT, tuple(reasons), comparisons)
        primary = next(c for c in comparisons if c.suite == self.primary)
        gain = primary.improvement
        if gain.verdict is StatTestVerdict.SIGNIFICANT and (gain.ci_lower or 0.0) > 0.0:
            return NeuralVerdict(
                JudgeDecision.ACCEPT,
                (f"amélioration significative sur « {self.primary} » : {gain.reason}",),
                comparisons,
            )
        return NeuralVerdict(
            JudgeDecision.INCONCLUSIVE,
            (f"aucune amélioration démontrée sur « {self.primary} » : {gain.reason}",),
            comparisons,
        )


def evaluate_suites(
    model: ApexLM, tokenizer: ByteBPETokenizer, suites: Sequence[Benchmark]
) -> dict[str, EvalReport]:
    return {b.name: evaluate(model, tokenizer, b) for b in suites}

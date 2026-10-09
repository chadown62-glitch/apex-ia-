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

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import torch
import torch.nn.functional as F

from apex.brain.model import model_fingerprint
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


#: Métrique historique des suites de texte (bits par octet, plus bas = mieux).
BPB = "bits_per_byte"


@dataclass(frozen=True)
class DocScore:
    """Score d'UN élément d'une suite. Pour les suites de texte (`BPB`) :
    bits par octet, précision de jeton, octets, jetons. Pour une autre
    métrique (capacité), `bits_per_byte` porte la VALEUR de la métrique pour
    cet élément (toujours « plus bas = mieux ») — voir `EvalReport.metric`."""

    doc_id: str
    bits_per_byte: float
    token_accuracy: float
    n_bytes: int
    n_tokens: int

    @property
    def value(self) -> float:
        return self.bits_per_byte


@dataclass(frozen=True)
class EvalReport:
    benchmark: str
    benchmark_fingerprint: str
    model_fingerprint: str
    scores: tuple[DocScore, ...]
    metric: str = BPB

    @property
    def value(self) -> float:
        """Valeur agrégée de la métrique (plus bas = mieux) : moyenne pondérée
        par les octets pour le BPB, moyenne simple par élément sinon (une
        pondération par octets n'a aucun sens pour un score de tâche)."""
        if self.metric == BPB:
            total = sum(s.n_bytes for s in self.scores)
            return sum(s.bits_per_byte * s.n_bytes for s in self.scores) / total if total else math.nan
        return sum(s.value for s in self.scores) / len(self.scores) if self.scores else math.nan

    @property
    def bits_per_byte(self) -> float:
        """BPB du benchmark entier ; NaN si la suite n'est pas une suite de texte."""
        return self.value if self.metric == BPB else math.nan

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
            str(data.get("metric", BPB)),
        )

    def summary(self) -> dict[str, object]:
        out: dict[str, object] = {
            "benchmark": self.benchmark,
            "benchmark_fingerprint": self.benchmark_fingerprint,
            "model_fingerprint": self.model_fingerprint,
            "documents": len(self.scores),
            "metric": self.metric,
            "value": round(self.value, 5),
        }
        if self.metric == BPB:
            out["bits_per_byte"] = round(self.bits_per_byte, 5)
            out["token_accuracy"] = round(self.token_accuracy, 5)
        else:
            out["solved"] = round(self.token_accuracy, 5)  # fraction pondérée des tâches résolues
        return out


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
    """`baseline_bpb`/`candidate_bpb` : valeur agrégée de la métrique de la
    suite (`metric`) — noms historiques conservés pour les journaux existants ;
    `to_dict` expose aussi les clés génériques `baseline_value`/`candidate_value`."""

    suite: str
    baseline_bpb: float
    candidate_bpb: float
    baseline_accuracy: float
    candidate_accuracy: float
    improvement: StatTestResult
    metric: str = BPB

    def to_dict(self) -> dict[str, object]:
        return {
            "suite": self.suite,
            "metric": self.metric,
            "baseline_value": round(self.baseline_bpb, 5),
            "candidate_value": round(self.candidate_bpb, 5),
            "gain_ci": [self.improvement.ci_lower, self.improvement.ci_upper],
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

    def __init__(
        self,
        *,
        primary: str,
        max_guard_regression_bpb: float = 0.01,
        seed: int = 0,
        tolerances: dict[str, float] | None = None,
    ) -> None:
        """`tolerances` : tolérance de régression des suites de GARDE par
        métrique (valeur agrégée) ; le BPB garde `max_guard_regression_bpb`."""
        self.primary = primary
        self.max_guard_regression_bpb = max_guard_regression_bpb
        self.tolerances = {**(tolerances or {}), BPB: max_guard_regression_bpb}
        self.stat = StatTest(n_bootstrap=2000, confidence_level=0.95, seed=seed)

    def tolerance(self, metric: str) -> float:
        if metric not in self.tolerances:
            raise ValueError(f"aucune tolérance de garde déclarée pour la métrique « {metric} »")
        return self.tolerances[metric]

    def _compare(self, suite: str, baseline: EvalReport, candidate: EvalReport) -> SuiteComparison:
        base = {s.doc_id: s for s in baseline.scores}
        cand = {s.doc_id: s for s in candidate.scores}
        ids = sorted(base)
        # Gain = valeur de référence - valeur candidate : positif si le candidat est meilleur.
        result = self.stat.compare_paired([cand[i].value for i in ids], [base[i].value for i in ids])
        return SuiteComparison(
            suite,
            baseline.value,
            candidate.value,
            baseline.token_accuracy,
            candidate.token_accuracy,
            result,
            baseline.metric,
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
            if baseline[suite].metric != candidate[suite].metric:
                return NeuralVerdict(JudgeDecision.REJECT, (f"métriques différentes sur « {suite} »",))
            if not all(math.isfinite(s.value) for s in candidate[suite].scores):
                return NeuralVerdict(JudgeDecision.REJECT, (f"mesure non finie sur « {suite} »",))
        comparisons = tuple(self._compare(s, baseline[s], candidate[s]) for s in sorted(baseline))
        reasons: list[str] = []
        for comp in comparisons:
            gain = comp.improvement
            if gain.verdict is StatTestVerdict.SIGNIFICANT and (gain.ci_upper or 0.0) < 0.0:
                reasons.append(f"régression significative sur « {comp.suite} » ({gain.reason})")
            elif comp.suite != self.primary and (
                comp.candidate_bpb - comp.baseline_bpb > self.tolerance(comp.metric)
            ):
                reasons.append(
                    f"régression de {comp.candidate_bpb - comp.baseline_bpb:.4f} ({comp.metric}) sur la "
                    f"garde « {comp.suite} » (tolérance {self.tolerance(comp.metric)})"
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

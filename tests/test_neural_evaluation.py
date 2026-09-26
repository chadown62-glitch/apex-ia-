"""Évaluation (bits par octet) et Judge d'acceptation d'une version du
modèle APEX : un benchmark modifié, une régression sur une garde ou une
amélioration indistinguable du bruit ne sont jamais acceptés."""

from __future__ import annotations

import math

import pytest

torch = pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.neural.config import ModelConfig, OptimizerSpec, TrainingConfig  # noqa: E402
from apex.neural.evaluation import (  # noqa: E402
    Benchmark,
    DocScore,
    EvalReport,
    JudgeDecision,
    NeuralJudge,
    evaluate,
    model_fingerprint,
)
from apex.neural.model import ApexLM  # noqa: E402
from apex.neural.tokenizer import ByteBPETokenizer  # noqa: E402
from apex.neural.training import TokenBatcher, TrainingEngine  # noqa: E402

CORPUS = [
    f"The function number {i} returns the sum of its arguments. It raises ValueError on empty input."
    for i in range(40)
]


@pytest.fixture(scope="module")
def tokenizer() -> ByteBPETokenizer:
    return ByteBPETokenizer.train(CORPUS, 300)


def _model(tokenizer: ByteBPETokenizer, seed: int = 0) -> ApexLM:
    torch.manual_seed(seed)
    return ApexLM(
        ModelConfig(
            vocab_size=tokenizer.vocab_size,
            hidden_size=32,
            num_layers=1,
            num_heads=2,
            intermediate_size=64,
            context_length=32,
        )
    )


def _bench(name: str = "heldout") -> Benchmark:
    return Benchmark(name, tuple((f"d{i}", t) for i, t in enumerate(CORPUS[30:])))


def test_bits_per_byte_matches_a_manual_computation(tokenizer: ByteBPETokenizer) -> None:
    model = _model(tokenizer)
    text = CORPUS[0]
    report = evaluate(model, tokenizer, Benchmark("one", (("x", text),)))
    ids = tokenizer.encode(text, add_bos=True, add_eos=True)
    nll, n_bytes = 0.0, 0
    with torch.no_grad():
        for start in range(0, len(ids) - 1, 32):
            chunk = ids[start : start + 33]
            logits, _ = model(torch.tensor([chunk[:-1]]))
            nll += float(
                torch.nn.functional.cross_entropy(logits[0], torch.tensor(chunk[1:]), reduction="sum")
            )
            n_bytes += sum(len(tokenizer.token_bytes(t)) for t in chunk[1:])
    assert report.scores[0].bits_per_byte == pytest.approx(nll / math.log(2) / n_bytes, rel=1e-5)
    assert report.scores[0].n_bytes == len(text.encode())  # <eos> compte 0 octet, <bos> n'est pas prédit


def test_evaluation_leaves_weights_and_mode_untouched(tokenizer: ByteBPETokenizer) -> None:
    model = _model(tokenizer)
    model.train()
    before = model_fingerprint(model)
    evaluate(model, tokenizer, _bench())
    assert model.training and model_fingerprint(model) == before


def test_training_lowers_heldout_bits_per_byte_and_the_judge_accepts(tokenizer: ByteBPETokenizer) -> None:
    model = _model(tokenizer)
    bench = _bench()
    v0 = evaluate(model, tokenizer, bench)
    tokens = [t for text in CORPUS[:30] for t in tokenizer.encode(text, add_bos=True, add_eos=True)]
    engine = TrainingEngine(
        model, TrainingConfig(max_steps=80, batch_size=8, optimizer=OptimizerSpec(lr=1e-2))
    )
    engine.fit(TokenBatcher(tokens, window=32, batch_size=8, seed=0), 80)
    v1 = evaluate(model, tokenizer, bench)
    assert v1.model_fingerprint != v0.model_fingerprint
    assert v1.bits_per_byte < 0.7 * v0.bits_per_byte
    verdict = NeuralJudge(primary="heldout").judge({"heldout": v0}, {"heldout": v1})
    assert verdict.decision is JudgeDecision.ACCEPT, verdict.reasons
    # Et dans l'autre sens, V0 est une régression.
    assert (
        NeuralJudge(primary="heldout").judge({"heldout": v1}, {"heldout": v0}).decision
        is JudgeDecision.REJECT
    )


# Judge sur des rapports construits : chaque règle isolée ----------------------


def report(bpb: list[float], name: str = "heldout", fingerprint: str = "sha256:b") -> EvalReport:
    return EvalReport(
        name, fingerprint, "sha256:m", tuple(DocScore(f"d{i}", v, 0.5, 100, 30) for i, v in enumerate(bpb))
    )


BASE = [2.0, 2.1, 1.9, 2.2, 2.0, 2.05, 1.95, 2.1, 2.0, 1.9]
BETTER = [v - 0.3 for v in BASE]
WORSE = [v + 0.3 for v in BASE]
NOISE = [v + (0.01 if i % 2 else -0.01) for i, v in enumerate(BASE)]


def test_judge_rules() -> None:
    judge = NeuralJudge(primary="heldout")
    assert (
        judge.judge({"heldout": report(BASE)}, {"heldout": report(BETTER)}).decision is JudgeDecision.ACCEPT
    )
    assert (
        judge.judge({"heldout": report(BASE)}, {"heldout": report(WORSE)}).decision is JudgeDecision.REJECT
    )
    noisy = judge.judge({"heldout": report(BASE)}, {"heldout": report(NOISE)})
    assert noisy.decision is JudgeDecision.INCONCLUSIVE


def test_changed_benchmark_cannot_hide_a_regression() -> None:
    judge = NeuralJudge(primary="heldout")
    verdict = judge.judge(
        {"heldout": report(BASE)}, {"heldout": report(BETTER, fingerprint="sha256:other")}
    )
    assert verdict.decision is JudgeDecision.REJECT and "modifié" in verdict.reasons[0]
    # Même empreinte annoncée mais documents différents : refusé aussi.
    fewer = EvalReport("heldout", "sha256:b", "sha256:m", report(BETTER).scores[:-2])
    assert judge.judge({"heldout": report(BASE)}, {"heldout": fewer}).decision is JudgeDecision.REJECT
    assert Benchmark("h", (("a", "x"), ("b", "y"))).fingerprint != Benchmark("h", (("a", "x"),)).fingerprint


def test_guard_regression_is_not_masked_by_primary_gain() -> None:
    judge = NeuralJudge(primary="heldout", max_guard_regression_bpb=0.01)
    baseline = {"heldout": report(BASE), "guard": report(BASE, "guard")}
    small_loss = [v + 0.02 for v in BASE]  # +0.02 bpb partout : au-delà de la tolérance de garde
    candidate = {"heldout": report([v - 1.0 for v in BASE]), "guard": report(small_loss, "guard")}
    verdict = judge.judge(baseline, candidate)
    assert verdict.decision is JudgeDecision.REJECT
    assert any("guard" in r for r in verdict.reasons)


def test_structural_refusals() -> None:
    judge = NeuralJudge(primary="heldout")
    assert judge.judge({"other": report(BASE)}, {"other": report(BETTER)}).decision is JudgeDecision.REJECT
    assert (
        judge.judge({"heldout": report(BASE)}, {"heldout": report(BETTER), "extra": report(BASE)}).decision
        is JudgeDecision.REJECT
    )
    nan = report([*BETTER[:-1], math.nan])
    assert judge.judge({"heldout": report(BASE)}, {"heldout": nan}).decision is JudgeDecision.REJECT
    few = judge.judge({"heldout": report(BASE[:3])}, {"heldout": report(BETTER[:3])})
    assert few.decision is JudgeDecision.INCONCLUSIVE  # trop peu de paires : jamais ACCEPT

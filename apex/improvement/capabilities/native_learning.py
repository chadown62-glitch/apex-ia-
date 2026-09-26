"""Quatrième capacité du Continuous Improvement Engine : NATIVE LEARNING.

Question mesurée : la chaîne d'apprentissage du modèle APEX (dataset ->
entraînement -> checkpoint -> évaluation -> Judge) apprend-elle RÉELLEMENT et
refuse-t-elle ce qui fausserait la preuve d'apprentissage ?

Chaque scénario exécute le vrai code (`apex.neural.*`) sur un petit modèle
réel (CPU, graines fixées). Une métrique vaut 1.0 si le comportement attendu
est observé. Sans PyTorch (extra `neural`), toutes les métriques sont
BLOCKED — jamais une valeur inventée."""

from __future__ import annotations

import hashlib
import inspect
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from apex.improvement.judge import ExternalJudge, FunctionProbe, ProbeOutcome
from apex.improvement.model import BenchmarkSnapshot, CapabilitySpec, CycleKind, MetricSpec
from apex.metalearner.judge_model import JudgeDimension, JudgeSeverity

CAPABILITY_ID = "native_learning"

_SUBJECTS = (
    "the river",
    "a star",
    "the bread",
    "an engine",
    "the forest",
    "a glacier",
    "the violin",
    "a harbor",
)
_VERBS = ("grows", "moves", "changes", "shines", "turns", "rests", "falls", "rises", "sings", "waits")
_OBJECTS = (
    "near the town",
    "in the morning",
    "after the rain",
    "under the bridge",
    "with the wind",
    "before the night",
    "across the valley",
    "along the coast",
    "beside the wall",
    "over the hills",
)


def _doc(i: int) -> str:
    """Phrases aléatoires (graine = i) sur un vocabulaire restreint : une
    structure apprenable, mais aucun long passage partagé entre documents."""
    import random

    rng = random.Random(i)  # noqa: S311 - corpus déterministe, pas de cryptographie
    sentences = [
        f"{rng.choice(_SUBJECTS).capitalize()} {rng.choice(_VERBS)} {rng.choice(_OBJECTS)}."
        for _ in range(12)
    ]
    return " ".join(sentences)


def _candidates(n: int = 30) -> list:  # type: ignore[type-arg]
    from apex.knowledge.model import sha256_text
    from apex.neural.dataset import TrainingCandidate

    return [
        TrainingCandidate(
            f"c{i}", _doc(i), f"src-{i}", sha256_text(_doc(i)), "compatible", "high_value", "ci"
        )
        for i in range(n)
    ]


def _setup(seed: int = 0) -> tuple:  # type: ignore[type-arg]
    import torch

    from apex.neural.config import ModelConfig, OptimizerSpec, TrainingConfig
    from apex.neural.dataset import Split, build_dataset
    from apex.neural.evaluation import Benchmark
    from apex.neural.model import ApexLM
    from apex.neural.tokenizer import ByteBPETokenizer
    from apex.neural.training import TokenBatcher, TrainingEngine

    dataset = build_dataset(_candidates(), validation_ratio=0.1, test_ratio=0.2, max_overlap=0.5)
    tokenizer = ByteBPETokenizer.train(dataset.texts(Split.TRAIN), 320)
    tokens = [
        t for text in dataset.texts(Split.TRAIN) for t in tokenizer.encode(text, add_bos=True, add_eos=True)
    ]
    torch.manual_seed(seed)
    model = ApexLM(
        ModelConfig(
            vocab_size=tokenizer.vocab_size,
            hidden_size=32,
            num_layers=1,
            num_heads=2,
            intermediate_size=64,
            context_length=32,
        )
    )
    engine = TrainingEngine(
        model, TrainingConfig(max_steps=60, batch_size=8, seed=seed, optimizer=OptimizerSpec(lr=1e-2))
    )
    batcher = TokenBatcher(tokens, window=32, batch_size=8, seed=seed)
    heldout = Benchmark.from_dataset("heldout", dataset, Split.TEST)
    return dataset, tokenizer, tokens, engine, batcher, heldout


def _backprop_updates_weights() -> tuple[bool, str]:
    import torch

    _, _, _, engine, batcher, _ = _setup()
    before = [p.detach().clone() for p in engine.model.parameters()]
    first = engine.train_step(batcher).loss
    for _ in range(40):
        last = engine.train_step(batcher).loss
    changed = sum(not torch.equal(a, b) for a, b in zip(before, engine.model.parameters(), strict=True))
    return changed == len(
        before
    ) and last < 0.8 * first, f"{changed}/{len(before)} tenseurs modifiés, loss {first:.3f} -> {last:.3f}"


def _step_without_backward_refused() -> tuple[bool, str]:
    _, _, _, engine, _, _ = _setup()
    try:
        engine.optimizer_step()
    except RuntimeError as exc:
        return True, f"refusé : {exc}"
    return False, "optimizer.step() accepté sans gradient"


def _resume_is_exact() -> tuple[bool, str]:
    import torch

    from apex.neural.checkpoint import load_checkpoint, save_checkpoint
    from apex.neural.training import TokenBatcher

    dataset, tokenizer, tokens, engine, batcher, _ = _setup()
    engine.fit(batcher, 10)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "c.pt"
        save_checkpoint(
            path,
            engine,
            tokenizer=tokenizer,
            batcher=batcher,
            dataset_fingerprint=dataset.fingerprint,
            dataset_provenance=dataset.provenance(),
            dataset_tokens=len(tokens),
        )
        engine.fit(batcher, 10)
        fresh = TokenBatcher(tokens, window=32, batch_size=8, seed=123)
        resumed, _, _ = load_checkpoint(path, batcher=fresh)
        resumed.fit(fresh, 10)
    same = all(
        torch.equal(a, b)
        for a, b in zip(engine.model.parameters(), resumed.model.parameters(), strict=True)
    )
    return same, "reprise identique à l'exécution continue" if same else "poids divergents après reprise"


def _resume_refuses_other_dataset() -> tuple[bool, str]:
    from apex.neural import checkpoint as ckpt

    if "dataset_fingerprint" not in inspect.signature(ckpt.load_checkpoint).parameters:
        return (
            False,
            "load_checkpoint ne vérifie pas le dataset attendu : une reprise sur d'autres données passe",
        )
    dataset, tokenizer, tokens, engine, batcher, _ = _setup()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "c.pt"
        ckpt.save_checkpoint(
            path,
            engine,
            tokenizer=tokenizer,
            batcher=batcher,
            dataset_fingerprint=dataset.fingerprint,
            dataset_provenance=dataset.provenance(),
            dataset_tokens=len(tokens),
        )
        try:
            load: Any = ckpt.load_checkpoint  # contrat vérifié par signature ci-dessus
            load(path, dataset_fingerprint="sha256:" + "0" * 64)
        except ValueError as exc:
            load(path, dataset_fingerprint=dataset.fingerprint)
            return True, f"refusé : {exc}"
    return False, "reprise acceptée sur un autre dataset"


def _heldout_gain_accepted() -> tuple[bool, str]:
    from apex.neural.evaluation import JudgeDecision, NeuralJudge, evaluate

    _, tokenizer, _, engine, batcher, heldout = _setup()
    v0 = evaluate(engine.model, tokenizer, heldout)
    engine.fit(batcher, 60)
    v1 = evaluate(engine.model, tokenizer, heldout)
    verdict = NeuralJudge(primary="heldout").judge({"heldout": v0}, {"heldout": v1})
    return (
        verdict.decision is JudgeDecision.ACCEPT,
        f"{v0.bits_per_byte:.3f} -> {v1.bits_per_byte:.3f} bpb : {verdict.decision.value}",
    )


def _corrupted_training_rejected() -> tuple[bool, str]:
    import torch

    from apex.neural.evaluation import JudgeDecision, NeuralJudge, evaluate
    from apex.neural.training import TokenBatcher

    _, tokenizer, tokens, engine, batcher, heldout = _setup()
    engine.fit(batcher, 60)
    v1 = evaluate(engine.model, tokenizer, heldout)
    shuffled = torch.tensor(tokens)[torch.randperm(len(tokens), generator=torch.Generator().manual_seed(1))]
    engine.fit(TokenBatcher(shuffled.tolist(), window=32, batch_size=8, seed=1), 60)
    v2 = evaluate(engine.model, tokenizer, heldout)
    verdict = NeuralJudge(primary="heldout").judge({"heldout": v1}, {"heldout": v2})
    return (
        verdict.decision is JudgeDecision.REJECT,
        f"{v1.bits_per_byte:.3f} -> {v2.bits_per_byte:.3f} bpb : {verdict.decision.value}",
    )


def _benchmark_swap_refused() -> tuple[bool, str]:
    from apex.neural.evaluation import Benchmark, JudgeDecision, NeuralJudge, evaluate

    _, tokenizer, _, engine, _, heldout = _setup()
    v0 = evaluate(engine.model, tokenizer, heldout)
    lighter = Benchmark("heldout", heldout.documents[1:])
    v0_light = evaluate(engine.model, tokenizer, lighter)
    verdict = NeuralJudge(primary="heldout").judge({"heldout": v0}, {"heldout": v0_light})
    return verdict.decision is JudgeDecision.REJECT, verdict.reasons[0]


def _duplicated_benchmark_documents_refused() -> tuple[bool, str]:
    from apex.neural.evaluation import Benchmark

    base = (("d1", _doc(1)), ("d2", _doc(2)))
    for name, docs in (
        ("identifiant répété", (*base, ("d1", _doc(1)))),
        ("texte répété", (*base, ("d3", _doc(1)))),
    ):
        try:
            Benchmark("heldout", docs)
        except ValueError:
            continue
        return (
            False,
            f"benchmark accepté avec un {name} : l'échantillon apparié est gonflé artificiellement",
        )
    return True, "identifiants et textes répétés refusés"


def _dataset_zero_width_duplicate_dropped() -> tuple[bool, str]:
    from dataclasses import replace

    from apex.knowledge.model import sha256_text
    from apex.neural.dataset import build_dataset

    items = _candidates()
    text = " ".join(w[:2] + chr(0x200B) + w[2:] if len(w) > 4 else w for w in items[0].text.split(" "))
    copy = replace(
        items[0], candidate_id="copy", source_id="mirror", text=text, content_hash=sha256_text(text)
    )
    dataset = build_dataset([*items, copy], validation_ratio=0.1, test_ratio=0.2, max_overlap=1.0)
    ok = "copy" in dataset.dropped_duplicates
    return ok, "copie maquillée écartée" if ok else "copie maquillée comptée comme une donnée nouvelle"


def _leakage_blocked() -> tuple[bool, str]:
    from dataclasses import replace

    from apex.knowledge.model import sha256_text
    from apex.neural.dataset import LeakageError, build_dataset

    items = _candidates()
    mirrors = [
        replace(
            c,
            candidate_id=f"m{i}",
            source_id=f"mirror-{i}",
            text=c.text + f" variant {i}",
            content_hash=sha256_text(c.text + f" variant {i}"),
        )
        for i, c in enumerate(items)
    ]
    try:
        build_dataset([*items, *mirrors], validation_ratio=0.1, test_ratio=0.2)
    except LeakageError as exc:
        return True, f"refusé : {str(exc)[:80]}"
    return False, "dataset construit malgré un test recopié dans le train"


def _unconfirmed_experience_refused() -> tuple[bool, str]:
    from apex.knowledge.experience import ExperienceRejectedError, confirmed_success_candidate
    from apex.metalearner.experience import Experience, SuccessEvidence
    from apex.metalearner.failure_taxonomy import FailureCategory
    from apex.oracles.model import OracleVerdict

    code = "def f():\n    return 1\n"
    experience = Experience(
        id="e",
        task_id="t",
        eval_run_id="r",
        candidate_id="c",
        strategy_id="s",
        generation_number=0,
        generation_config={},
        oracle_verdict=OracleVerdict.PASS,
        fitness=1.0,
        failure_category=FailureCategory.SUCCESS,
        created_by="ci",
        solution_fingerprint=hashlib.sha256(code.encode()).hexdigest(),
        success_evidence=SuccessEvidence.OBSERVED,
    )
    try:
        confirmed_success_candidate(
            experience, task_prompt="p", solution_text=code, license_status="compatible"
        )
    except ExperienceRejectedError as exc:
        return True, f"refusé : {exc}"
    return False, "succès seulement observé transformé en donnée d'entraînement"


def _guard_regression_rejected() -> tuple[bool, str]:
    from apex.neural.evaluation import DocScore, EvalReport, JudgeDecision, NeuralJudge

    def report(name: str, values: list[float]) -> EvalReport:
        return EvalReport(
            name,
            "sha256:b",
            "sha256:m",
            tuple(DocScore(f"d{i}", v, 0.5, 100, 30) for i, v in enumerate(values)),
        )

    base = [2.0 + 0.01 * i for i in range(10)]
    verdict = NeuralJudge(primary="heldout").judge(
        {"heldout": report("heldout", base), "guard": report("guard", base)},
        {
            "heldout": report("heldout", [v - 1.0 for v in base]),
            "guard": report("guard", [v + 0.05 for v in base]),
        },
    )
    return verdict.decision is JudgeDecision.REJECT, verdict.decision.value


def _nan_candidate_rejected() -> tuple[bool, str]:
    from apex.neural.evaluation import DocScore, EvalReport, JudgeDecision, NeuralJudge

    scores = tuple(DocScore(f"d{i}", 2.0, 0.5, 100, 30) for i in range(10))
    nan = (*scores[:-1], DocScore("d9", float("nan"), 0.5, 100, 30))
    verdict = NeuralJudge(primary="heldout").judge(
        {"heldout": EvalReport("heldout", "sha256:b", "m", scores)},
        {"heldout": EvalReport("heldout", "sha256:b", "m", nan)},
    )
    return verdict.decision is JudgeDecision.REJECT, verdict.decision.value


SCENARIOS: dict[str, tuple[Callable[[], tuple[bool, str]], str]] = {
    "backprop_updates_weights": (
        _backprop_updates_weights,
        "l'entraînement modifie tous les poids et réduit la loss",
    ),
    "step_without_backward_refused": (
        _step_without_backward_refused,
        "optimizer.step() sans gradient refusé",
    ),
    "resume_is_exact": (_resume_is_exact, "reprise depuis checkpoint = exécution continue"),
    "resume_refuses_other_dataset": (
        _resume_refuses_other_dataset,
        "reprendre un checkpoint sur un autre dataset est refusé",
    ),
    "heldout_gain_accepted": (
        _heldout_gain_accepted,
        "un gain réel sur le held-out est accepté par le Judge",
    ),
    "corrupted_training_rejected": (_corrupted_training_rejected, "un entraînement corrompu est rejeté"),
    "benchmark_swap_refused": (_benchmark_swap_refused, "un benchmark allégé est refusé"),
    "duplicated_benchmark_documents_refused": (
        _duplicated_benchmark_documents_refused,
        "un benchmark aux documents répétés est refusé",
    ),
    "dataset_zero_width_duplicate_dropped": (
        _dataset_zero_width_duplicate_dropped,
        "une copie maquillée n'augmente pas le dataset",
    ),
    "leakage_blocked": (_leakage_blocked, "un test recopié dans le train bloque la construction"),
    "unconfirmed_experience_refused": (
        _unconfirmed_experience_refused,
        "un succès non confirmé n'entre pas dans l'entraînement",
    ),
    "guard_regression_rejected": (_guard_regression_rejected, "une régression de garde n'est pas masquée"),
    "nan_candidate_rejected": (_nan_candidate_rejected, "une mesure non finie est rejetée"),
}

SPEC = CapabilitySpec(
    capability_id=CAPABILITY_ID,
    name="Native Learning",
    version="1",
    description=(
        "La chaîne d'apprentissage du modèle APEX apprend réellement (backprop, gain sur held-out) "
        "et refuse ce qui fausserait la preuve (fuite, doublons, benchmark modifié, reprise sur "
        "d'autres données, succès non confirmés)."
    ),
    components=(
        "apex.neural.dataset.build_dataset",
        "apex.neural.training.TrainingEngine",
        "apex.neural.checkpoint",
        "apex.neural.evaluation.NeuralJudge",
        "apex.knowledge.experience",
    ),
    dependencies=("PyTorch (extra « neural »)",),
    preconditions=("PyTorch importable",),
    tests=(
        "tests/test_neural_training.py",
        "tests/test_neural_evaluation.py",
        "tests/test_neural_dataset.py",
    ),
    metrics=tuple(MetricSpec(name, description, True) for name, (_, description) in SCENARIOS.items()),
    success_criteria=("toutes les métriques à 1.0",),
    risks=(
        "petit modèle et petit corpus synthétique : prouve la mécanique, pas la qualité d'un grand modèle",
    ),
    maturity="research",
)


def run_scenario(name: str) -> tuple[float, str]:
    fn, _ = SCENARIOS[name]
    try:
        ok, observed = fn()
    except Exception as exc:
        return 0.0, f"exception {exc.__class__.__name__}: {exc}"
    return (1.0 if ok else 0.0), observed


def _torch_available() -> tuple[bool, str]:
    try:
        import torch
    except ImportError:
        return False, "PyTorch absent (extra « neural » non installé)"
    return True, f"torch {torch.__version__}"


class NativeLearningAdapter:
    spec = SPEC

    def benchmark_fingerprint(self) -> str:
        source = repr(sorted(SCENARIOS)) + "".join(_doc(i) for i in range(30))
        return "scenarios=" + hashlib.sha256(source.encode()).hexdigest()[:16]

    def measure(self, version: str) -> BenchmarkSnapshot:
        available, detail = _torch_available()
        environment = {
            "torch": detail,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "device": "cpu",
        }
        if not available:
            return BenchmarkSnapshot(
                capability_id=CAPABILITY_ID,
                version=version,
                samples={},
                blocked={m.name: detail for m in SPEC.metrics},
                notes=(detail,),
                environment=environment,
            )
        samples: dict[str, tuple[float, ...]] = {}
        notes: list[str] = []
        for metric in SPEC.metrics:
            value, observed = run_scenario(metric.name)
            samples[metric.name] = (value,)
            notes.append(f"{metric.name}: {observed}")
        return BenchmarkSnapshot(
            capability_id=CAPABILITY_ID,
            version=version,
            samples=samples,
            notes=tuple(notes),
            environment=environment,
        )


def _scenario_probe(name: str) -> Callable[[], ProbeOutcome]:
    def run() -> ProbeOutcome:
        available, detail = _torch_available()
        if not available:
            return ProbeOutcome(refuted=None, observed=detail, hypothesis=SCENARIOS[name][1])
        value, observed = run_scenario(name)
        return ProbeOutcome(
            refuted=value < 1.0,
            observed=observed,
            hypothesis=SCENARIOS[name][1],
            severity=JudgeSeverity.HIGH,
        )

    return run


def build_judge(judge_id: str = "ci-judge:native-learning") -> ExternalJudge:
    return ExternalJudge(
        judge_id,
        tuple(
            FunctionProbe(
                probe_id=name,
                dimension=JudgeDimension.CORRECTNESS,
                kinds=frozenset(CycleKind),
                description=f"échec : {description}",
                remediation="corriger le composant de la chaîne d'apprentissage concerné",
                regression_test="tests/test_neural_evaluation.py",
                fn=_scenario_probe(name),
            )
            for name, (_, description) in SCENARIOS.items()
        ),
    )

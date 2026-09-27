"""P3 — capacité PROGRAM TRACE : générateur, vérificateur de traces, benchmark (vérité terrain
exécutée dans le sandbox, fraîcheur canonique), évaluation d'un modèle RÉEL et intégration au
monde enfant (même cerveau que `code_repair`)."""

from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

import torch

from apex.capabilities.program_trace import benchmark as bench_module
from apex.capabilities.program_trace.benchmark import (
    BenchmarkBuildError,
    ProgramTraceBenchmark,
    build_benchmark,
    document_grams,
    suite_name,
)
from apex.capabilities.program_trace.capability import (
    TASKS_DOMAIN,
    TASKS_PREFIX,
    score_output,
)
from apex.capabilities.program_trace.faithfulness import (
    Intervention,
    interventions,
    measure_faithfulness,
    recompute,
)
from apex.capabilities.program_trace.generator import (
    Problem,
    canonical_form,
    generate,
    interpret,
    parse_trace,
)
from apex.capabilities.registry import get_capability, sandbox_provider
from apex.neural.config import ModelConfig, OptimizerSpec, SchedulerSpec, TrainingConfig
from apex.neural.dataset import Split
from apex.neural.model import ApexLM
from apex.neural.tokenizer import ByteBPETokenizer
from apex.neural.training import TokenBatcher, TrainingEngine

try:
    sandbox_provider()
    SANDBOX = True
except Exception:  # pragma: no cover - dépend de l'hôte
    SANDBOX = False
needs_sandbox = pytest.mark.skipif(not SANDBOX, reason="aucun sandbox NAMESPACED sur cet hôte")

SMALL = {"train": 16, "validation": 16, "heldout": 16, "deeper": 16}


@pytest.fixture(scope="module")
def small() -> ProgramTraceBenchmark:
    if not SANDBOX:
        pytest.skip("aucun sandbox NAMESPACED sur cet hôte")
    return build_benchmark(sandbox_provider(), pool_size=2000, sizes=SMALL)


# -- générateur et vérificateur ------------------------------------------------------


def test_generated_problems_match_an_independent_interpreter() -> None:
    rng = random.Random(3)
    for depth in (1, 2, 3, 4, 6):
        for _ in range(200):
            problem = generate(depth, rng)
            assert problem.depth == depth
            assert interpret(problem.program) == problem
    assert interpret("a=3;b=a+4;c=b-a").values == (3, 7, 4)


@pytest.mark.parametrize(
    "program",
    ["a=3", "a=3;b=c+1", "a=3;b=a*2", "a=3;a=a+1", "a=3;b=a+1;c=a+1", "a=12;b=a+1", "a=3;b=a+__import__"],
)
def test_the_interpreter_refuses_anything_outside_the_grammar(program: str) -> None:
    with pytest.raises(ValueError):
        interpret(program)


def test_renaming_does_not_make_a_problem_new() -> None:
    assert canonical_form("a=3;b=a+4;c=b-a") == canonical_form("x=3;q=x+4;m=q-x")
    assert canonical_form("a=3;b=a+4;c=b-a") != canonical_form("a=3;b=a+4;c=b-4")


def test_trace_parsing_is_strict() -> None:
    assert parse_trace("a3,b7,c4=4") == (["a", "b", "c"], [3, 7, 4], 4)
    assert parse_trace("3,-7,4=4") == (["", "", ""], [3, -7, 4], 4)
    for junk in ("a3,b7,c4=4a=3", "a3,b7,c4=", "a3,,c4=4", "a3 b7=7", "", "=4", "a3,b7,c4=4\n"):
        assert parse_trace(junk) is None, junk


def test_scoring_checks_every_value_and_ignores_only_the_names() -> None:
    problem = interpret("a=3;b=a+4;c=b-a")
    assert score_output(problem, "a3,b7,c4=4")["exact"]
    wrong_names = score_output(problem, "x3,y7,z4=4")
    assert wrong_names["exact"] and not wrong_names["names_ok"]  # calcul juste, recopie fausse
    lucky_final = score_output(problem, "a3,b8,c4=4")  # réponse juste, étape fausse
    assert not lucky_final["exact"] and lucky_final["final_ok"] and lucky_final["steps_ok"] == 2
    assert not score_output(problem, "a3,b7=4")["exact"]  # étape manquante
    assert not score_output(problem, "a3,b7,c4,d4=4")["exact"]  # étape en trop
    assert not score_output(problem, "a3,b7,c4=4 ok")["exact"]  # texte après la réponse


# -- benchmark -------------------------------------------------------------------


@needs_sandbox
def test_benchmark_is_fresh_by_construction(small: ProgramTraceBenchmark) -> None:
    pool_canonical = {p.canonical for p in small.pool}
    evaluated = small.eval_problems()
    assert evaluated and all(p.canonical not in pool_canonical for p in evaluated)
    protected = set().union(*(document_grams(p) for p in evaluated))
    assert all(not (document_grams(p) & protected) for p in small.pool)
    assert set(small.suites[suite_name("train")].problems) <= set(small.pool)
    assert {p.depth for p in small.suites[suite_name("deeper")].problems} == {4}
    assert max(p.depth for p in small.pool) == 3  # la profondeur 4 n'est jamais entraînée
    assert small.report["sandbox_verified"] == len(small.pool) + len(evaluated)


@needs_sandbox
def test_ground_truth_comes_from_real_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un générateur qui se trompe (valeur finale décalée) est démasqué par l'exécution sandbox."""
    real = bench_module.generate

    def off_by_one(depth: int, rng: random.Random) -> Problem:
        p = real(depth, rng)
        return replace(p, values=(*p.values[:-1], p.values[-1] + 1))

    monkeypatch.setattr(bench_module, "generate", off_by_one)
    with pytest.raises(BenchmarkBuildError, match="exécution réelle"):
        build_benchmark(sandbox_provider(), pool_size=50, sizes=SMALL)


@needs_sandbox
def test_frozen_benchmark_is_reverified_on_load(small: ProgramTraceBenchmark) -> None:
    payload = small.to_dict()
    assert ProgramTraceBenchmark.from_dict(payload).fingerprint == small.fingerprint
    tampered = {**payload, "fingerprint": "sha256:" + "0" * 64}
    with pytest.raises(BenchmarkBuildError, match="empreinte"):
        ProgramTraceBenchmark.from_dict(tampered)
    # Valeur fausse ET empreintes recalculées par le faussaire : l'évaluateur hôte la refuse.
    heldout = payload["suites"][suite_name("heldout")]
    forged_problem = {**heldout["problems"][0], "values": [0, 0, 0, 0]}
    forged_suite = {k: v for k, v in heldout.items() if k != "fingerprint"}
    forged_suite["problems"] = [forged_problem, *heldout["problems"][1:]]
    forged = {k: v for k, v in payload.items() if k != "fingerprint"}
    forged["suites"] = {**payload["suites"], suite_name("heldout"): forged_suite}
    with pytest.raises(BenchmarkBuildError, match="vérité terrain"):
        ProgramTraceBenchmark.from_dict(forged)


@needs_sandbox
def test_an_evaluation_problem_seen_in_training_is_refused(small: ProgramTraceBenchmark) -> None:
    leaked = small.suites[suite_name("heldout")].problems[0]
    renamed = interpret(
        leaked.program.translate(str.maketrans("abcdefghjkmnpqrstuvwxyz", "zyxwvutsrqpnmkjhgfedcba"))
    )
    assert renamed.canonical == leaked.canonical
    with pytest.raises(BenchmarkBuildError, match="présents à l'entraînement"):
        replace(small, pool=(*small.pool, renamed)).verify()


# -- évaluation d'un modèle réel -------------------------------------------------


def _config(tokenizer: ByteBPETokenizer) -> ModelConfig:
    return ModelConfig(
        vocab_size=tokenizer.vocab_size,
        hidden_size=64,
        num_layers=2,
        num_heads=4,
        intermediate_size=128,
        context_length=128,
    )


@pytest.fixture(scope="module")
def memorised(small: ProgramTraceBenchmark) -> tuple[ApexLM, ByteBPETokenizer]:
    """Modèle RÉEL entraîné sur les SEULS documents de la suite train (40 répétitions)."""
    torch.manual_seed(0)
    tokenizer = ByteBPETokenizer.train([p.document for p in small.pool], 300)
    stream: list[int] = []
    for _ in range(40):
        for p in small.suites[suite_name("train")].problems:
            stream += tokenizer.encode(p.document, add_bos=True, add_eos=True)
    model = ApexLM(_config(tokenizer))
    training = TrainingConfig(
        max_steps=300,
        batch_size=16,
        seed=0,
        optimizer=OptimizerSpec(lr=3e-3, weight_decay=0.0),
        scheduler=SchedulerSpec(name="cosine", warmup_steps=20, min_lr_ratio=0.1),
    )
    TrainingEngine(model, training, pad_id=tokenizer.pad_id).fit(
        TokenBatcher(stream, window=64, batch_size=16, seed=0), steps=300
    )
    return model, tokenizer


@needs_sandbox
def test_a_real_model_that_memorised_the_train_suite_is_scored_as_such(
    small: ProgramTraceBenchmark, memorised: tuple[ApexLM, ByteBPETokenizer]
) -> None:
    """Le modèle restitue les problèmes vus (erreur basse) sans rien résoudre de neuf (held-out,
    validation, deeper) : la métrique sépare mémorisation et généralisation. Un modèle non
    entraîné échoue partout."""
    model, tokenizer = memorised
    capability = get_capability("program_trace")
    untrained = capability.evaluate(
        ApexLM(_config(tokenizer)), tokenizer, small, provider=sandbox_provider()
    )
    assert all(r.value == 1.0 for r in untrained.reports.values())
    result = capability.evaluate(model, tokenizer, small, provider=sandbox_provider())
    assert set(result.reports) == set(capability.contract.suites)
    assert result.reports[suite_name("train")].value <= 0.5
    for split in ("validation", "heldout", "deeper"):
        assert result.reports[suite_name(split)].value >= 0.9
    solved = [d for d in result.details[suite_name("train")] if d["solved"]]
    assert solved and all(d["well_formed"] and d["final_ok"] for d in solved)


# -- fidélité par intervention -------------------------------------------------------


def test_recomputation_propagates_an_intervention_through_the_program() -> None:
    problem = interpret("a=3;b=a+4;c=b-a;d=c+2")
    assert recompute(problem, 0, 5) == [5, 9, 4, 6]  # a change : b, c, d suivent (c lit a et b)
    assert recompute(problem, 1, 0) == [3, 0, -3, -1]
    assert recompute(problem, 3, 9) == [*problem.values[:3], 9]
    case = Intervention(problem, 1, 0)
    assert case.prefix == "a3,b0,"
    assert case.followed == [-3, -1] and case.ignored == [4, 6]
    chosen = interventions([problem] * 20, random.Random(1))
    assert chosen and all(c.step < problem.depth and c.followed != c.ignored for c in chosen)


@needs_sandbox
def test_a_memoriser_is_not_counted_as_a_faithful_reasoner(
    small: ProgramTraceBenchmark, memorised: tuple[ApexLM, ByteBPETokenizer]
) -> None:
    """Un modèle qui RÉCITE des traces apprises par cœur ne recalcule pas depuis une valeur
    imposée : l'intervention le montre, même sur les problèmes qu'il « résout »."""
    model, tokenizer = memorised
    report = measure_faithfulness(model, tokenizer, small.suites[suite_name("train")].problems)
    assert report["solved"] >= 8 and report["interventions"] == report["solved"]
    assert report["followed"] + report["ignored"] + report["other"] == report["interventions"]
    assert report["followed_rate"] < 0.5


# -- intégration au monde : même cerveau que code_repair -------------------------------


@needs_sandbox
def test_child_world_adds_program_trace_next_to_code_repair(small: ProgramTraceBenchmark) -> None:
    from apex.neural.loop.world import World
    from tests import capability_support as support

    parent = support.world()
    spec = replace(
        parent.spec,
        parent=parent.fingerprint,
        domains={**parent.spec.domains, TASKS_DOMAIN: TASKS_PREFIX},
        train_only_domains=(*parent.spec.train_only_domains, TASKS_DOMAIN),
        capabilities=(*parent.spec.capabilities, "program_trace"),
    )
    child = World.build(spec, [], capability_data={"program_trace": small}, parent=parent)
    assert set(child.capabilities) == {"code_repair", "program_trace"}
    assert child.tokenizer.fingerprint() == parent.tokenizer.fingerprint()
    train_texts = {c.text for c in child.dataset.splits[Split.TRAIN]}
    assert len(child.train_candidates(TASKS_DOMAIN)) > 0.95 * len(small.pool)
    assert not {p.document for p in small.eval_problems()} & train_texts
    assert all(
        not c.source_id.startswith(TASKS_PREFIX)
        for split in (Split.VALIDATION, Split.TEST)
        for c in child.dataset.splits[split]
    )
    assert suite_name("heldout") in child.acceptance_suites
    assert child.capability_of_suite(suite_name("deeper")) == "program_trace"


@needs_sandbox
def test_the_loop_judges_a_world_holding_both_capabilities(
    small: ProgramTraceBenchmark, tmp_path: Path
) -> None:
    """Régression (P3, run réel) : le Judge lisait les détails de tâche au format de `code_repair`
    seul et plantait sur une seconde capacité. Boucle réelle, deux capacités, un entraînement
    jugé (rejeu et réplique compris)."""
    from apex.neural.loop.controller import LoopController, LoopLimits
    from apex.neural.loop.strategy import StrategyPolicy
    from apex.neural.loop.world import World
    from tests import capability_support as support

    spec = replace(
        support.SPEC,
        domains={**support.SPEC.domains, TASKS_DOMAIN: TASKS_PREFIX},
        train_only_domains=(*support.SPEC.train_only_domains, TASKS_DOMAIN),
        capabilities=("code_repair", "program_trace"),
    )
    world = World.build(
        spec,
        support.candidates(),
        capability_data={"code_repair": support.benchmark(), "program_trace": small},
    )
    ctrl = LoopController(
        tmp_path / "loop",
        world,
        limits=LoopLimits(
            max_cycles=1,
            max_experiments_per_cycle=1,
            max_total_experiments=1,
            max_steps_per_experiment=60,
            max_tokens_per_experiment=20_000,
            checkpoint_every=30,
            experiment_timeout_s=300,
        ),
        policy=StrategyPolicy(finetune_steps=40, finetune_tokens=8_000, max_steps=60),
        bootstrap=support.recipe(steps=60, mix={"code": 1.0, TASKS_DOMAIN: 1.0}),
        log=lambda _m: None,
    )
    summary = ctrl.run()
    assert summary["unfinished"] == []
    record = next(iter(ctrl.journal.experiments().values()))
    judged = (record.payload("judged") or {})["result"]
    changes = judged["task_changes"]
    for split in ("train", "validation", "heldout", "deeper"):
        assert changes[suite_name(split)]["tasks"] == SMALL[split]
    assert changes["cap:code_repair:heldout"]["tasks"] > 0

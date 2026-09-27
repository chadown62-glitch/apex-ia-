"""Capacité DEPENDENCY ORDER : instructions mélangées, trace dans l'ordre d'exécution. Résolveur
hôte indépendant, notation (l'ordre compte), benchmark (vérité terrain sandbox, fraîcheur,
exclusions croisées avec `program_trace`) et monde enfant portant les deux capacités."""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.capabilities.dependency_order import benchmark as order_module
from apex.capabilities.dependency_order.benchmark import (
    DependencyOrderBenchmark,
    OrderProblem,
    build_benchmark,
    resolve,
    shuffled,
    suite_name,
)
from apex.capabilities.dependency_order.capability import TASKS_DOMAIN, TASKS_PREFIX, score_output
from apex.capabilities.program_trace import benchmark as trace_module
from apex.capabilities.program_trace.benchmark import (
    BenchmarkBuildError,
    ProgramTraceBenchmark,
    document_grams,
)
from apex.capabilities.program_trace.generator import Problem, generate, interpret
from apex.capabilities.registry import get_capability, sandbox_provider
from apex.neural.dataset import Split

try:
    sandbox_provider()
    SANDBOX = True
except Exception:  # pragma: no cover - dépend de l'hôte
    SANDBOX = False
needs_sandbox = pytest.mark.skipif(not SANDBOX, reason="aucun sandbox NAMESPACED sur cet hôte")

SMALL = {"train": 12, "validation": 12, "heldout": 12}


@pytest.fixture(scope="module")
def trace() -> ProgramTraceBenchmark:
    if not SANDBOX:
        pytest.skip("aucun sandbox NAMESPACED sur cet hôte")
    return trace_module.build_benchmark(
        sandbox_provider(),
        pool_size=1500,
        sizes={"train": 12, "validation": 12, "heldout": 12, "deeper": 12},
    )


@pytest.fixture(scope="module")
def small(trace: ProgramTraceBenchmark) -> DependencyOrderBenchmark:
    return build_benchmark(
        sandbox_provider(),
        seen={p.canonical for p in trace.pool},
        evaluated={p.canonical for p in trace.eval_problems()},
        seen_grams=frozenset(g for p in trace.pool for g in document_grams(p)),
        pool_size=800,
        sizes=SMALL,
    )


def test_the_host_resolver_recovers_the_execution_order_from_the_shuffled_text() -> None:
    rng = random.Random(5)
    for depth in (1, 2, 3, 5):
        for _ in range(200):
            problem = shuffled(generate(depth, rng), rng)
            assert list(problem.order) != sorted(problem.order)  # jamais présenté dans l'ordre
            assert resolve(problem.shown) == problem.base
    assert resolve("c=b-a;a=3;b=a+4").values == (3, 7, 4)


@pytest.mark.parametrize(
    "shown",
    [
        "a=3;b=4;c=b+1",  # deux racines
        "b=a+4;c=b-1",  # aucune racine
        "a=3;b=a+4;c=a+1",  # deux instructions partent de « a » : ordre ambigu
        "a=3;b=a+4;d=c+1",  # « d » hors de la chaîne
        "a=3;a=a+1",  # réaffectation
    ],
)
def test_the_resolver_refuses_anything_that_is_not_a_single_dependency_chain(shown: str) -> None:
    with pytest.raises(ValueError):
        resolve(shown)


def test_reading_left_to_right_is_scored_as_a_failure() -> None:
    problem = OrderProblem(interpret("a=3;b=a+4;c=b-a"), (2, 0, 1))  # « c=b-a;a=3;b=a+4 »
    assert problem.prompt == "c=b-a;a=3;b=a+4?c:"
    assert score_output(problem, "a3,b7,c4=4")["exact"]
    textual = score_output(problem, "c4,a3,b7=4")  # ordre de présentation, bonnes valeurs
    assert not textual["exact"] and textual["textual_order"] and textual["final_ok"]
    renamed = score_output(problem, "x3,y7,z4=4")  # valeurs justes, ordre illisible
    assert not renamed["exact"] and not renamed["order_ok"]
    assert not score_output(problem, "a3,b7,c4=4 ")["exact"]


@needs_sandbox
def test_benchmark_is_fresh_and_never_overlaps_program_trace(
    small: DependencyOrderBenchmark, trace: ProgramTraceBenchmark
) -> None:
    evaluated = [p.canonical for p in small.eval_problems()]
    assert len(set(evaluated)) == len(evaluated)
    assert not set(evaluated) & {p.canonical for p in small.pool}
    # Évalué ici : jamais entraîné par program_trace, même ordonné ou renommé.
    assert not set(evaluated) & {p.canonical for p in trace.pool}
    # Même contrôle que le monde : aucune séquence de 12 jetons de code d'un problème évalué ici
    # (formes mélangée et ordonnée) dans un document d'entraînement de program_trace.
    trained = {g for p in trace.pool for g in document_grams(p)}
    assert not any((document_grams(p) | document_grams(p.base)) & trained for p in small.eval_problems())
    # Entraîné ici : jamais la solution d'un problème évalué par program_trace.
    assert not {p.canonical for p in small.pool} & {p.canonical for p in trace.eval_problems()}
    assert small.report["sandbox_verified"] == len(small.pool) + len(evaluated)


@needs_sandbox
def test_problems_evaluated_by_program_trace_are_never_drawn() -> None:
    """Toutes les formes de profondeur 1 déclarées évaluées par program_trace : aucune n'entre au
    réservoir ni à l'évaluation, quelle que soit la chance du tirage."""
    depth_one = {
        interpret(f"a={x};b=a{op}{y}").canonical for x in range(10) for y in range(10) for op in "+-"
    }
    bench = build_benchmark(sandbox_provider(), evaluated=depth_one, pool_size=150, sizes=SMALL)
    assert bench.report["pool_depths"][1] == 0 and bench.report["rejected"]["excluded"] > 0
    assert not depth_one & {p.canonical for p in (*bench.pool, *bench.eval_problems())}


@needs_sandbox
def test_forbidden_overlaps_are_refused_on_verification(
    small: DependencyOrderBenchmark, trace: ProgramTraceBenchmark
) -> None:
    trace_eval = trace.eval_problems()[0]
    with pytest.raises(BenchmarkBuildError, match="évalué par program_trace"):
        replace(small, pool=(*small.pool, shuffled(trace_eval, random.Random(0)))).verify()
    trained = small.eval_problems()[0].canonical
    with pytest.raises(BenchmarkBuildError, match="déjà entraîné"):
        replace(small, seen=(*small.seen, trained)).verify()
    leaked = small.suites[suite_name("heldout")].problems[0]
    with pytest.raises(BenchmarkBuildError, match="présents à l'entraînement"):
        replace(small, pool=(*small.pool, leaked)).verify()
    in_order = replace(leaked, order=tuple(sorted(leaked.order)))
    with pytest.raises(BenchmarkBuildError, match="ordre d'exécution"):
        replace(small, pool=(*small.pool, in_order)).verify()


@needs_sandbox
def test_ground_truth_comes_from_real_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    real = order_module.generate

    def off_by_one(depth: int, rng: random.Random) -> Problem:
        p = real(depth, rng)
        return replace(p, values=(*p.values[:-1], p.values[-1] + 1))

    monkeypatch.setattr(order_module, "generate", off_by_one)
    with pytest.raises(BenchmarkBuildError, match="exécution réelle"):
        build_benchmark(sandbox_provider(), pool_size=50, sizes=SMALL)


@needs_sandbox
def test_frozen_benchmark_is_reverified_on_load(small: DependencyOrderBenchmark) -> None:
    payload = small.to_dict()
    assert DependencyOrderBenchmark.from_dict(payload).fingerprint == small.fingerprint
    with pytest.raises(BenchmarkBuildError, match="empreinte"):
        DependencyOrderBenchmark.from_dict({**payload, "fingerprint": "sha256:" + "0" * 64})
    heldout = payload["suites"][suite_name("heldout")]
    forged_suite = {k: v for k, v in heldout.items() if k != "fingerprint"}
    forged_suite["problems"] = [
        {**heldout["problems"][0], "values": [0, 0, 0, 0]},
        *heldout["problems"][1:],
    ]
    forged = {k: v for k, v in payload.items() if k != "fingerprint"}
    forged["suites"] = {**payload["suites"], suite_name("heldout"): forged_suite}
    with pytest.raises(BenchmarkBuildError, match="vérité terrain"):
        DependencyOrderBenchmark.from_dict(forged)


@needs_sandbox
def test_child_world_holds_both_reasoning_capabilities_and_protects_them(
    small: DependencyOrderBenchmark, trace: ProgramTraceBenchmark
) -> None:
    from apex.capabilities.program_trace.capability import TASKS_DOMAIN as TRACE_DOMAIN
    from apex.capabilities.program_trace.capability import TASKS_PREFIX as TRACE_PREFIX
    from apex.neural.loop.world import World
    from tests import capability_support as support

    parent = support.world()
    spec = replace(
        parent.spec,
        parent=parent.fingerprint,
        domains={**parent.spec.domains, TRACE_DOMAIN: TRACE_PREFIX, TASKS_DOMAIN: TASKS_PREFIX},
        train_only_domains=(*parent.spec.train_only_domains, TRACE_DOMAIN, TASKS_DOMAIN),
        capabilities=(*parent.spec.capabilities, "program_trace", "dependency_order"),
    )
    child = World.build(
        spec, [], capability_data={"program_trace": trace, "dependency_order": small}, parent=parent
    )
    assert set(child.capabilities) == {"code_repair", "program_trace", "dependency_order"}
    train_texts = {c.text for c in child.dataset.splits[Split.TRAIN]}
    assert child.train_candidates(TASKS_DOMAIN)
    for p in small.eval_problems():
        assert p.document not in train_texts
        assert not any(p.shown in t or p.base.program in t for t in train_texts)
    assert child.protected_groups["dependency_order"][0] == suite_name("heldout")
    assert get_capability("dependency_order").contract.metric == "order_error"


@needs_sandbox
@pytest.mark.parametrize("form", ["ordered", "shuffled"])
def test_an_outside_document_quoting_an_evaluated_program_is_decontaminated(
    small: DependencyOrderBenchmark, trace: ProgramTraceBenchmark, form: str
) -> None:
    """La forme ORDONNÉE d'un problème évalué porte la même trace que sa réponse : un document
    extérieur qui la cite (comme la forme mélangée) est retiré de l'entraînement."""
    import hashlib

    from apex.capabilities.program_trace.capability import TASKS_DOMAIN as TRACE_DOMAIN
    from apex.capabilities.program_trace.capability import TASKS_PREFIX as TRACE_PREFIX
    from apex.neural.dataset import TrainingCandidate
    from apex.neural.loop.world import World
    from tests import capability_support as support

    problem = small.suites[suite_name("heldout")].problems[0]
    program = (problem.base.program if form == "ordered" else problem.shown).replace(";", "; ")
    text = f"Qu'obtient-on avec le programme suivant : {program} ? Réponse attendue plus bas.\n"
    leak = TrainingCandidate(
        candidate_id="leak#0",
        text=text,
        source_id=support.SPEC.domains["code"] + "zz_quotes_an_eval_program.py",
        content_hash="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        license_status="free",
        quality="high_value",
        origin="test:adversarial",
    )
    spec = replace(
        support.SPEC,
        domains={**support.SPEC.domains, TRACE_DOMAIN: TRACE_PREFIX, TASKS_DOMAIN: TASKS_PREFIX},
        train_only_domains=(*support.SPEC.train_only_domains, TRACE_DOMAIN, TASKS_DOMAIN),
        capabilities=("code_repair", "program_trace", "dependency_order"),
    )
    world = World.build(
        spec,
        (*support.candidates(), leak),
        capability_data={
            "code_repair": support.benchmark(),
            "program_trace": trace,
            "dependency_order": small,
        },
    )
    assert leak.candidate_id in world.capability_decontaminated
    assert all(c.candidate_id != leak.candidate_id for s in Split for c in world.dataset.splits[s])


@needs_sandbox
def test_evaluation_problems_sharing_code_with_program_trace_training_are_redrawn() -> None:
    """Contrôle du monde appliqué à la construction : une séquence de 12 jetons de code partagée
    avec un document d'entraînement de program_trace (même préfixe, mêmes noms) refuse le problème.
    Les séquences sont celles d'une première construction : la seconde doit tout retirer."""
    first = build_benchmark(sandbox_provider(), pool_size=100, sizes=SMALL)
    grams = frozenset(g for p in first.eval_problems() for g in document_grams(p.base))
    second = build_benchmark(sandbox_provider(), seen_grams=grams, pool_size=100, sizes=SMALL)
    assert second.report["rejected"]["ngram"] >= len(first.eval_problems())
    assert not any((document_grams(p) | document_grams(p.base)) & grams for p in second.eval_problems())

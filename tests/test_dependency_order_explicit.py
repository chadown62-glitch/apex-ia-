"""DEPENDENCY ORDER à liaison explicite (P3.7) : seule la RÉPONSE diffère de la capacité
implicite ; même générateur, même entrée ; benchmark gelé, vérifié, refusé s'il est forgé."""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.capabilities.dependency_order.benchmark import (
    DependencyOrderBenchmark,
    OrderProblem,
    build_benchmark,
    shuffled,
    suite_name,
)
from apex.capabilities.dependency_order.explicit import NAME, TASKS_DOMAIN, TASKS_PREFIX, score_explicit
from apex.capabilities.program_trace import benchmark as trace_module
from apex.capabilities.program_trace.benchmark import ProgramTraceBenchmark, document_grams
from apex.capabilities.program_trace.generator import generate
from apex.capabilities.registry import get_capability, sandbox_provider
from apex.neural.dataset import Split

try:
    sandbox_provider()
    SANDBOX = True
except Exception:  # pragma: no cover - dépend de l'hôte
    SANDBOX = False
needs_sandbox = pytest.mark.skipif(not SANDBOX, reason="aucun sandbox NAMESPACED sur cet hôte")

SMALL = {"train": 12, "validation": 12, "heldout": 12}


def _problem(seed: int = 3) -> tuple[OrderProblem, OrderProblem]:
    base = generate(3, random.Random(seed))
    implicit = shuffled(base, random.Random(seed))
    return implicit, replace(implicit, explicit=True)


def test_only_the_answer_differs_from_the_implicit_capability() -> None:
    implicit, explicit = _problem()
    assert explicit.prompt == implicit.prompt and explicit.shown == implicit.shown
    assert explicit.canonical == implicit.canonical
    steps = implicit.base.program.split(";")
    assert explicit.answer == ",".join(
        f"{s}>{v}" for s, v in zip(steps, implicit.base.values, strict=True)
    ) + (f"={implicit.base.values[-1]}")
    # Le format implicite n'écrit pas la clé : les empreintes des benchmarks existants sont inchangées.
    assert "explicit" not in implicit.to_dict() and explicit.to_dict()["explicit"] is True
    assert OrderProblem.from_dict(explicit.to_dict()) == explicit
    assert OrderProblem.from_dict(implicit.to_dict()) == implicit


def test_explicit_scoring_checks_order_copied_statements_and_values() -> None:
    _, problem = _problem()
    assert score_explicit(problem, problem.answer)["exact"]
    wrong_value = problem.answer.replace(
        f">{problem.base.values[1]},", f">{problem.base.values[1] + 1},", 1
    )
    assert not score_explicit(problem, wrong_value)["exact"]
    steps = problem.base.program.split(";")
    wrong_copy = problem.answer.replace(steps[1], steps[1][:-1] + ("9" if steps[1][-1] != "9" else "8"), 1)
    assert not score_explicit(problem, wrong_copy)["exact"]
    assert not score_explicit(problem, problem.base.answer)[
        "well_formed"
    ]  # format implicite : illisible ici
    textual = ",".join(f"{s}>0" for s in problem.statements) + "=0"
    assert (
        score_explicit(problem, textual)["textual_order"] and not score_explicit(problem, textual)["exact"]
    )


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
        pool_size=600,
        sizes=SMALL,
        seed="apex-dependency-order-explicit",
        capability=NAME,
        explicit=True,
    )


@needs_sandbox
def test_explicit_benchmark_is_frozen_named_and_refused_by_the_implicit_capability(
    small: DependencyOrderBenchmark,
) -> None:
    assert set(small.suites) == {suite_name(s, NAME) for s in ("train", "validation", "heldout")}
    assert all(p.explicit for p in small.pool) and all(p.explicit for p in small.eval_problems())
    payload = get_capability(NAME).dump(small)
    assert get_capability(NAME).load(payload).fingerprint == small.fingerprint
    with pytest.raises(ValueError, match=r"format de réponse|autre capacité"):
        get_capability("dependency_order").load(payload)
    # Benchmark forgé : un problème ramené au format implicite (réponse différente) est refusé.
    forged = dict(payload)
    heldout = dict(payload["suites"][suite_name("heldout", NAME)])
    heldout["problems"] = [
        {k: v for k, v in heldout["problems"][0].items() if k != "explicit"},
        *heldout["problems"][1:],
    ]
    heldout.pop("fingerprint", None)
    forged["suites"] = {**payload["suites"], suite_name("heldout", NAME): heldout}
    forged.pop("fingerprint", None)
    with pytest.raises(ValueError):
        get_capability(NAME).load(forged)


@needs_sandbox
def test_child_world_trains_on_explicit_documents_and_protects_the_new_suites(
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
        capabilities=(*parent.spec.capabilities, "program_trace", NAME),
    )
    child = World.build(spec, [], capability_data={"program_trace": trace, NAME: small}, parent=parent)
    docs = child.train_candidates(TASKS_DOMAIN)
    assert docs and all(">" in c.text.split("?", 1)[1] for c in docs)  # réponses explicites
    train_texts = {c.text for c in child.dataset.splits[Split.TRAIN]}
    for p in small.eval_problems():
        assert p.document not in train_texts
        assert not any(p.shown in t or p.base.program in t for t in train_texts)
    assert child.protected_groups[NAME][0] == suite_name("heldout", NAME)
    assert child.capabilities[NAME][0].contract.memorization_suite == suite_name("train", NAME)

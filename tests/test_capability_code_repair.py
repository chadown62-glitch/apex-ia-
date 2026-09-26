"""Capacité CODE REPAIR (Priority 2, cycle 1) : mutation, exécution sandbox
réelle, benchmark protégé, solveur, intégration au monde gelé et au Judge de
la boucle autonome. Aucun mock : sandbox, entraînement et mesures réels."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.capabilities.code_repair.benchmark import (
    BenchmarkBuildError,
    CodeRepairBenchmark,
    RepairSuite,
    assign_splits,
    similarity,
    suite_name,
)
from apex.capabilities.code_repair.corpus import ALL
from apex.capabilities.code_repair.fresh import FRESH, fresh_contamination
from apex.capabilities.code_repair.harness import _MAX_BATCH_CHARS, judge_run, run_sources
from apex.capabilities.code_repair.mutation import candidates, single_edits
from apex.capabilities.code_repair.solver import METRIC, rank_candidates, solve_suite, suite_report
from apex.capabilities.contract import classify_learning
from apex.capabilities.registry import get_capability, sandbox_provider
from apex.knowledge.filters import code_quality, normalize_code
from apex.knowledge.pipeline import code_chunks
from apex.neural.dataset import Split
from apex.neural.evaluation import BPB, DocScore, EvalReport, NeuralJudge
from apex.neural.loop.execute import initial_model, measure_with_details
from apex.neural.loop.plan import Budget, ExperimentPlan, LoopHypothesis, Weakness
from apex.neural.loop.world import PRIMARY, World, WorldIntegrityError, WorldSpec
from tests import capability_support as support

try:
    sandbox_provider()
    SANDBOX = True
except Exception:  # pragma: no cover - dépend de l'hôte
    SANDBOX = False
needs_sandbox = pytest.mark.skipif(not SANDBOX, reason="aucun sandbox NAMESPACED sur cet hôte")


# Knowledge Agent : le code arrive intact au modèle ------------------------------------------


def test_code_normalization_keeps_indentation_and_chunks_are_lossless() -> None:
    source = "def f(x):\r\n    if x:\t\r\n        return 1   \n\n\n\n    return 2\n"
    normalized = normalize_code(source)
    assert normalized == "def f(x):\n    if x:\n        return 1\n\n    return 2"
    text = "\n".join(f"def g{i}(a):\n    total = a + {i}\n    return total\n" for i in range(40))
    chunks = code_chunks(text, 30)
    assert len(chunks) > 1
    assert [line for c in chunks for line in c.split("\n") if line.strip()] == [
        line for line in text.split("\n") if line.strip()
    ]
    assert all(c.startswith("def ") for c in chunks)  # coupure aux définitions de premier niveau


def test_code_quality_is_judged_on_syntax_not_prose() -> None:
    assert code_quality("def f(:\n    pass\n" * 3).score == 0.0
    valid = "\n".join(f"def f{i}(a):\n    return a + {i}" for i in range(4))
    assert code_quality(valid).score == 1.0


# Mutation ----------------------------------------------------------------------------------------


def test_every_operator_is_invertible_so_the_exact_fix_is_enumerable() -> None:
    fn = next(f for f in ALL if f.fid == "binary_search")
    for edit in single_edits(fn.source)[:60]:
        buggy = edit.apply(fn.source)
        if buggy == fn.source:
            continue
        texts = [t for t, _ in candidates(buggy)]
        assert len(texts) == len(set(texts))
        assert all(t != buggy for t in texts)
    buggy = fn.source.replace("lo <= hi", "lo < hi")
    listing = candidates(buggy)
    assert fn.source in [t for t, _ in listing]
    assert listing == candidates(buggy)  # déterministe


# Exécution sandbox réelle ------------------------------------------------------------------------


@needs_sandbox
def test_harness_distinguishes_pass_wrong_output_exception_and_timeout() -> None:
    fn = next(f for f in ALL if f.fid == "binary_search")
    inputs = [repr(t) for t in fn.tests]
    sources = [
        fn.source,
        fn.source.replace("lo <= hi", "lo < hi"),
        fn.source.replace("items[mid] == target", "items[mid + 99] == target"),
        fn.source.replace("lo = mid + 1", "lo = mid"),
        "def other(x):\n    return x\n",
    ]
    runs = run_sources(sandbox_provider(), name="binary_search", sources=sources, inputs=inputs)
    expected = runs[0].outputs
    assert expected is not None
    outcomes = [judge_run(r, expected) for r in runs]
    assert outcomes[0].passed and outcomes[0].tests_passed == len(inputs)
    assert not outcomes[1].passed and outcomes[1].first_failure.startswith("wrong_output")  # type: ignore[union-attr]
    assert outcomes[2].first_failure.startswith("exception:IndexError")  # type: ignore[union-attr]
    assert outcomes[3].status == "timeout" and not outcomes[3].passed
    assert outcomes[4].status == "load_error" and outcomes[4].tests_passed == 0


@needs_sandbox
def test_large_payloads_are_batched_under_the_kernel_argument_limit() -> None:
    fn = next(f for f in ALL if f.fid == "max_index")
    many = [t for t, _ in candidates(fn.source.replace("> values[best]", ">= values[best]"))] * 20
    assert sum(map(len, many)) > 2 * _MAX_BATCH_CHARS  # plusieurs lancements nécessaires
    runs = run_sources(
        sandbox_provider(), name="max_index", sources=many, inputs=[repr(t) for t in fn.tests]
    )
    assert len(runs) == len(many) and all(r.status in ("ok", "timeout", "crash") for r in runs)


# Benchmark ---------------------------------------------------------------------------------------


def test_split_assignment_keeps_families_apart_and_clusters_together() -> None:
    assignment = assign_splits(ALL, "apex-code-repair", 0.6)
    by = {f.fid: f for f in ALL}
    assert {s for fid, s in assignment.items() if by[fid].category == "security"} == {"transfer"}
    assert {s for fid, s in assignment.items() if by[fid].category == "parsing"} == {"heldout"}
    assert assignment["binary_search"] == assignment["lower_bound"]  # grappe de quasi-doublons
    for a in ALL:
        for b in ALL:
            if assignment[a.fid] != assignment[b.fid]:
                assert similarity(a.source, b.source) <= 0.6


@needs_sandbox
def test_benchmark_is_protected_frozen_and_hides_the_answers() -> None:
    bench = support.benchmark()
    assert set(bench.suites) == {suite_name(s) for s in ("train", "validation", "heldout", "transfer")}
    fids = [set(s.functions) for s in bench.suites.values()]
    assert sum(len(f) for f in fids) == len(set().union(*fids))  # une fonction = une seule suite
    for suite in bench.suites.values():
        for inst in suite.instances:
            view = inst.view()
            assert not hasattr(view, "expected") and not hasattr(view, "injected")
            assert inst.buggy != bench.references[inst.fid]["source"]
    restored = CodeRepairBenchmark.from_dict(json.loads(json.dumps(bench.to_dict())))
    assert restored.fingerprint == bench.fingerprint
    tampered = json.loads(json.dumps(bench.to_dict()))
    first = next(iter(tampered["suites"]))
    tampered["suites"][first]["instances"][0]["expected"][0] = "'forged'"
    with pytest.raises(BenchmarkBuildError):
        CodeRepairBenchmark.from_dict(tampered)


# Solveur -----------------------------------------------------------------------------------------


@needs_sandbox
def test_solver_scores_only_what_was_executed() -> None:
    world = support.world()
    model = initial_model(world, 0)
    suite = support.benchmark().suites[suite_name("heldout")]
    results = solve_suite(model, world.tokenizer, suite, sandbox_provider(), attempts=3)
    assert len(results) == len(suite.instances)
    for r in results:
        assert 1 <= len(r.attempts) <= 3 and all(a.outcome.executed for a in r.attempts)
        if r.solved:
            assert r.attempts[r.first_pass_rank - 1].outcome.passed  # type: ignore[operator]
            assert not any(a.outcome.passed for a in r.attempts[: r.first_pass_rank - 1])  # type: ignore[operator]
            assert r.cost == (r.first_pass_rank - 1) / 3  # type: ignore[operator]
        else:
            assert r.cost == 1.0 and not any(a.outcome.passed for a in r.attempts)
    report = suite_report(suite, results, model)
    assert report.metric == METRIC and len(report.scores) == len(suite.functions)  # un score par fonction
    assert math.isclose(report.value, sum(s.value for s in report.scores) / len(report.scores))
    again = solve_suite(model, world.tokenizer, suite, sandbox_provider(), attempts=3)
    assert [r.to_dict() for r in again] == [r.to_dict() for r in results]  # reproductible


def test_ranking_is_deterministic_and_uses_the_model() -> None:
    world = support.world()
    view = support.benchmark().suites[suite_name("train")].instances[0].view()
    a = rank_candidates(initial_model(world, 0), world.tokenizer, view)
    b = rank_candidates(initial_model(world, 0), world.tokenizer, view)
    c = rank_candidates(initial_model(world, 1), world.tokenizer, view)
    assert [x.source for x in a] == [x.source for x in b]
    assert [x.score for x in a] != [x.score for x in c]  # d'autres poids donnent d'autres scores


# Monde et Judge ----------------------------------------------------------------------------------


@needs_sandbox
def test_world_freezes_the_capability_and_decontaminates_training(tmp_path: Path) -> None:
    world = support.world()
    bench = support.benchmark()
    assert world.suite_names >= set(bench.suites)
    assert world.acceptance_suites == {PRIMARY, suite_name("heldout")}
    assert world.metric_tolerances == {METRIC: 0.05}
    assert (
        world.suite_domain(suite_name("transfer")) == "code"
        and world.suite_domain("heldout:stdlib") == "stdlib"
    )
    tasks = [
        c for s in Split for c in world.dataset.splits[s] if world.domain_of(c.source_id) == "code_tasks"
    ]
    assert tasks and all(c in world.dataset.splits[Split.TRAIN] for c in tasks)  # train seulement
    train_fids = {fid for fid, r in bench.references.items() if r["split"] == "train"}
    assert {c.source_id.rsplit("/", 1)[1] for c in tasks} <= train_fids  # jamais une tâche d'évaluation
    # Un document d'entraînement qui contient une solution d'évaluation est retiré.
    eval_source = next(r["source"] for r in bench.references.values() if r["split"] == "heldout")
    leaky = replace(
        support.candidates()[0],
        candidate_id="leak#0",
        text="# copie\n" + eval_source,
        content_hash="sha256:"
        + __import__("hashlib").sha256(("# copie\n" + eval_source).encode()).hexdigest(),
    )
    leaky_world = World.build(
        support.SPEC, (*support.candidates(), leaky), capability_data={"code_repair": bench}
    )
    assert "leak#0" in leaky_world.capability_decontaminated
    assert all(c.candidate_id != "leak#0" for s in Split for c in leaky_world.dataset.splits[s])
    path = tmp_path / "world.json"
    world.save(path)
    assert World.load(path).fingerprint == world.fingerprint
    data = json.loads(path.read_text())
    suite = next(iter(data["capabilities"]["code_repair"]["suites"].values()))
    suite["instances"][0]["expected"][0] = "'forged'"
    path.write_text(json.dumps(data))
    with pytest.raises(WorldIntegrityError):
        World.load(path)


def test_text_only_worlds_keep_their_fingerprint() -> None:
    spec = WorldSpec(domains={"a": "x:"})
    assert "capabilities" not in spec.identity() and "train_only_domains" not in spec.identity()


@needs_sandbox
def test_measure_includes_capability_suites_measured_by_execution() -> None:
    world = support.world()
    reports, details = measure_with_details(initial_model(world, 0), world)
    for name in support.benchmark().suites:
        assert reports[name].metric == METRIC and name in details
        assert all(t["attempts"] for t in details[name])  # chaque tâche : au moins une exécution réelle
    assert reports[PRIMARY].metric == BPB


def test_judge_tolerances_are_per_metric() -> None:
    def report(name: str, metric: str, values: list[float]) -> EvalReport:
        return EvalReport(
            name, "fp", "m", tuple(DocScore(str(i), v, 0.0, 1, 1) for i, v in enumerate(values)), metric
        )

    base = {"heldout": report("heldout", BPB, [3.0] * 10), "cap": report("cap", METRIC, [0.5] * 10)}
    # Régressions NON significatives (une ou deux fonctions sur dix) : seule la tolérance décide.
    slight = {**base, "cap": report("cap", METRIC, [0.9] + [0.5] * 9)}  # +0.04 < 0.05
    verdict = NeuralJudge(primary="heldout", tolerances={METRIC: 0.05}).judge(base, slight)
    assert "cap" not in " ".join(verdict.reasons)
    beyond = {**base, "cap": report("cap", METRIC, [0.9, 0.9] + [0.5] * 8)}  # +0.08 > 0.05
    verdict = NeuralJudge(primary="heldout", tolerances={METRIC: 0.05}).judge(base, beyond)
    assert verdict.decision.value == "reject" and "tolérance 0.05" in " ".join(verdict.reasons)
    # Une régression constante est SIGNIFICATIVE : rejetée quelle que soit la tolérance.
    constant = {**base, "cap": report("cap", METRIC, [0.54] * 10)}
    verdict = NeuralJudge(primary="heldout", tolerances={METRIC: 0.05}).judge(base, constant)
    assert verdict.decision.value == "reject" and "significative" in " ".join(verdict.reasons)
    much_worse = beyond
    with pytest.raises(ValueError, match="aucune tolérance"):
        NeuralJudge(primary="heldout").judge(base, much_worse)


def test_judge_primary_is_the_preregistered_acceptance_target_only() -> None:
    from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
    from apex.neural.loop.judge import IndependentJudge

    world = support.world()
    judge = IndependentJudge(world, LocalFilesystemArtifactStore(root="/tmp/unused-apex-store"))

    def plan(target: str) -> ExperimentPlan:
        weakness = Weakness(target, "code", 0.9, 0.25, 0.65, 5, "mesure")
        hyp = LoopHypothesis(
            weakness, "c", "m", target, "decrease", tuple(sorted(world.suite_names)), "f", "s"
        )
        return ExperimentPlan(
            world.fingerprint,
            {"name": "init", "artifact": None},
            None,
            support.recipe(),
            hyp,
            Budget(100, 20_000, 60.0),
        )

    assert judge.primary_for(plan(suite_name("heldout"))) == suite_name("heldout")
    assert judge.primary_for(plan(suite_name("train"))) == PRIMARY  # jamais une suite non déclarée
    assert judge.primary_for(plan("heldout:code")) == PRIMARY  # tranche : comportement P1 inchangé
    assert judge.primary_for(None) == PRIMARY


def test_capability_registry_rejects_unknown_capabilities() -> None:
    assert get_capability("code_repair").contract.acceptance_suite == suite_name("heldout")
    with pytest.raises(KeyError):
        get_capability("reasoning")


def test_repair_suite_fingerprint_changes_with_any_instance() -> None:
    bench = support.benchmark()
    suite = bench.suites[suite_name("validation")]
    altered = RepairSuite(
        suite.name, suite.split, (replace(suite.instances[0], inputs=("(1,)",)), *suite.instances[1:])
    )
    assert altered.fingerprint != suite.fingerprint


@needs_sandbox
def test_every_kept_bug_is_really_detected_by_the_tests() -> None:
    bench = support.benchmark()
    for suite in bench.suites.values():
        for inst in suite.instances:
            (run,) = run_sources(
                sandbox_provider(), name=inst.name, sources=[inst.buggy], inputs=list(inst.inputs)
            )
            assert not judge_run(run, inst.expected).passed, inst.instance_id


def test_ranking_rule_is_the_mean_per_token_nll() -> None:
    import torch
    import torch.nn.functional as F

    world = support.world()
    model = initial_model(world, 0)
    view = support.benchmark().suites[suite_name("validation")].instances[0].view()
    top = rank_candidates(model, world.tokenizer, view)[0]
    ids = world.tokenizer.encode(top.source, add_bos=True, add_eos=True)
    with torch.no_grad():
        logits, _ = model(torch.tensor([ids[:-1]]))
        mean = float(F.cross_entropy(logits[0].float(), torch.tensor(ids[1:])))
    assert math.isclose(top.score, mean, rel_tol=1e-4)


@needs_sandbox
def test_world_refuses_a_context_too_short_for_the_benchmark() -> None:
    short = replace(support.SPEC, context_length=48)
    with pytest.raises(WorldIntegrityError, match="dépasse le contexte"):
        World.build(short, support.candidates(), capability_data={"code_repair": support.benchmark()})


def test_code_reaches_training_candidates_intact() -> None:
    from apex.knowledge.acquisition import FileAcquirer, LocalDirectoryDiscovery
    from apex.knowledge.export import to_training_candidates
    from apex.knowledge.model import SourceKind
    from apex.knowledge.pipeline import AgentConfig, KnowledgeAgent

    refs = LocalDirectoryDiscovery(
        support.STDLIB, ("heapq.py",), SourceKind.REPOSITORY, "PSF-2.0"
    ).discover()
    items = KnowledgeAgent([FileAcquirer()], AgentConfig(chunk_words=80)).run(refs).items
    chunks = to_training_candidates(items, chunk_words=80)
    assert len(chunks) > 1 and any(c.text.startswith("def ") for c in chunks)
    assert any("\n    " in c.text for c in chunks)  # l'indentation survit à l'export
    original = normalize_code((support.STDLIB / "heapq.py").read_text())
    assert [line for c in chunks for line in c.text.split("\n") if line.strip()] == [
        line for line in original.split("\n") if line.strip()
    ]


# Mémorisation / généralisation / transfert : établis par les comparaisons du Judge ----------


def _comparison(suite: str, low: float, high: float, verdict: str = "significant") -> dict:
    return {
        "suite": suite,
        "bpb_gain_ci": [low, high],
        "verdict": verdict,
        "baseline_bpb": 0.9,
        "candidate_bpb": 0.8,
    }


def test_learning_is_classified_from_significant_paired_gains_only() -> None:
    contract = get_capability("code_repair").contract
    held, train, transfer = (suite_name(s) for s in ("heldout", "train", "transfer"))
    assert contract.memorization_suite == train and contract.transfer_suites == (transfer,)

    memo = classify_learning(
        contract, [_comparison(train, 0.1, 0.3), _comparison(held, -0.05, 0.02, "no_difference")]
    )
    assert memo["kinds"] == ["MEMORIZATION"] and memo["evidence"][0].startswith(train)

    general = classify_learning(contract, [_comparison(train, 0.1, 0.3), _comparison(held, 0.02, 0.1)])
    assert general["kinds"] == ["GENERALIZATION"]  # un gain held-out n'est pas de la mémorisation

    both = classify_learning(contract, [_comparison(held, 0.02, 0.1), _comparison(transfer, 0.01, 0.2)])
    assert both["kinds"] == ["GENERALIZATION", "TRANSFER"]

    # un intervalle qui contient zéro n'est jamais un apprentissage, même avec un verdict mal formé
    noise = classify_learning(contract, [_comparison(held, -0.01, 0.1), _comparison(train, 0.0, 0.2)])
    assert noise["kinds"] == ["NONE"] and noise["evidence"] == []

    worse = classify_learning(contract, [_comparison(held, -0.2, -0.05)])
    assert worse["kinds"] == ["NONE"] and worse["regressions"] == [held]


def test_fresh_tasks_are_checked_against_everything_the_world_contains() -> None:
    fresh = FRESH[0]
    assert {f.fid for f in FRESH}.isdisjoint({f.fid for f in ALL})  # jamais dans le benchmark gelé
    leaked = fresh_contamination(["# prose\n", "x = 1\n" + fresh.source], [f.source for f in ALL])
    assert leaked["shared_12gram_with_world"] == [fresh.fid]
    clean = fresh_contamination(["def unrelated(a):\n    return a\n"], [f.source for f in ALL])
    assert clean["shared_12gram_with_world"] == []
    assert clean["max_similarity_to_corpus"][0] <= 0.6  # pas une variante d'une fonction du corpus

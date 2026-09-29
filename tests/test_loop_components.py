"""Composants de la boucle autonome, un par un : monde gelé, mémoire,
hypothèses et plans, registre (retour arrière), analyse d'échec, stratégie."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.neural.dataset import Split
from apex.neural.evaluation import DocScore, EvalReport
from apex.neural.loop.failure import FailureCause, analyze_exception, analyze_judgement
from apex.neural.loop.journal import ExperimentJournal, JournalCorruptedError
from apex.neural.loop.judge import JudgeResult
from apex.neural.loop.observe import Observation, find_weaknesses
from apex.neural.loop.plan import (
    Budget,
    ExperimentPlan,
    InvalidPlanError,
    LoopHypothesis,
    NonFalsifiableHypothesisError,
    Recipe,
    Weakness,
)
from apex.neural.loop.strategy import StrategyDecision, StrategyPolicy
from apex.neural.loop.world import World, WorldIntegrityError
from apex.neural.registry import ModelRegistry, Proposal
from tests import loop_support as support

# Monde gelé -----------------------------------------------------------------------


def test_world_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    world = support.world()
    path = tmp_path / "world.json"
    fingerprint = world.save(path)
    assert World.load(path, expected_fingerprint=fingerprint).fingerprint == fingerprint
    data = json.loads(path.read_text())
    data["splits"]["test"][0]["text"] += " altered"
    path.write_text(json.dumps(data))
    with pytest.raises(WorldIntegrityError, match="altéré"):
        World.load(path)
    world.save(path)
    with pytest.raises(WorldIntegrityError, match="différent du monde attendu"):
        World.load(path, expected_fingerprint="sha256:" + "1" * 64)


def test_world_benchmarks_are_frozen_slices_of_the_heldout() -> None:
    world = support.world()
    heldout = {d for d, _ in world.benchmarks["heldout"].documents}
    slices = [{d for d, _ in world.benchmarks[f"heldout:{dom}"].documents} for dom in support.DOMAINS]
    assert set().union(*slices) == heldout and not slices[0] & slices[1]
    test_ids = {c.candidate_id for c in world.dataset.splits[Split.TEST]}
    assert heldout == test_ids


def test_token_stream_follows_the_mix_and_uses_only_training_documents() -> None:
    world = support.world()
    train_ids = {c.candidate_id for c in world.dataset.splits[Split.TRAIN]}
    stream, used = world.token_stream({"apex_docs": 1.0}, 5_000, seed=1)
    assert len(stream) >= 5_000 and set(used) <= train_ids
    assert {
        world.domain_of(c.source_id) for c in world.dataset.splits[Split.TRAIN] if c.candidate_id in used
    } == {"apex_docs"}
    _, mixed = world.token_stream({"apex_docs": 0.5, "stdlib": 0.5}, 5_000, seed=1)
    domains = {
        world.domain_of(c.source_id) for c in world.dataset.splits[Split.TRAIN] if c.candidate_id in mixed
    }
    assert domains == {"apex_docs", "stdlib"}
    assert world.token_stream({"stdlib": 1.0}, 3_000, seed=2) == world.token_stream(
        {"stdlib": 1.0}, 3_000, seed=2
    )
    with pytest.raises(ValueError, match="inconnus"):
        world.token_stream({"other": 1.0}, 100, seed=0)
    with pytest.raises(ValueError, match="vide"):
        world.token_stream({"stdlib": 0.0}, 100, seed=0)


# Mémoire ---------------------------------------------------------------------------


def test_journal_is_append_only_and_tamper_evident(tmp_path: Path) -> None:
    journal = ExperimentJournal(tmp_path / "j.jsonl")
    journal.experiment("e1", "planned", plan={"fingerprint": "sha256:a", "hypothesis": {"strategy": "s"}})
    journal.experiment("e1", "started")
    journal.experiment("e2", "planned", plan={"fingerprint": "sha256:b"})
    journal.experiment("e1", "promotion", promoted=False)
    records = journal.experiments()
    assert records["e1"].finished and not records["e2"].finished
    assert [r.experiment_id for r in journal.unfinished()] == ["e2"]
    assert [r.experiment_id for r in journal.by_plan("sha256:a")] == ["e1"]
    lines = journal.path.read_text().splitlines()
    tampered = json.loads(lines[1])
    tampered["event"] = "promotion"
    journal.path.write_text("\n".join([lines[0], json.dumps(tampered), *lines[2:]]) + "\n")
    with pytest.raises(JournalCorruptedError, match="entrée 1"):
        journal.entries()
    journal.path.write_text("\n".join([lines[0], *lines[2:]]) + "\n")  # suppression d'une ligne
    with pytest.raises(JournalCorruptedError):
        journal.entries()


# Hypothèses et plans ----------------------------------------------------------------

BENCHES = {"heldout", "validation", "heldout:stdlib", "heldout:apex_docs"}
WEAK = Weakness("heldout:apex_docs", "apex_docs", 5.2, 4.1, 1.1, 40, "mesure")


def hypothesis(**over: object) -> LoopHypothesis:
    base = LoopHypothesis(
        WEAK,
        "entraîner sur apex_docs réduit son BPB",
        "mix apex_docs",
        "heldout:apex_docs",
        "decrease",
        tuple(sorted(BENCHES)),
        "réfutée si pas de gain significatif",
        "target_only",
    )
    return replace(base, **over)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"target_benchmark": "nowhere"}, "inexistant"),
        ({"direction": "better"}, "non mesurable"),
        ({"modification": " "}, "modification"),
        ({"weakness": replace(WEAK, documents=2)}, "preuve insuffisante"),
        ({"protected": ()}, "protégés"),
        ({"falsification": ""}, "réfutation"),
        ({"weakness": replace(WEAK, benchmark="elsewhere")}, "benchmark inconnu"),
    ],
)
def test_vague_or_unfalsifiable_hypotheses_are_refused(override: dict[str, object], message: str) -> None:
    with pytest.raises(NonFalsifiableHypothesisError, match=message):
        hypothesis(**override).validate(BENCHES)


REF = Recipe(init="champion", mix={"stdlib": 1.0}, steps=100, lr=1e-3, tokens=10_000)


def plan(recipe: Recipe, baseline_recipe: Recipe | None = REF, **over: object) -> ExperimentPlan:
    return ExperimentPlan(
        "sha256:w",
        {"name": "c", "artifact": "sha256:" + "a" * 64},
        baseline_recipe,
        recipe,
        hypothesis(),
        Budget(200, 20_000, 60.0),
        **over,  # type: ignore[arg-type]
    )


def test_plan_validation_budget_modifications_and_identity() -> None:
    doms = set(support.DOMAINS)
    good = plan(replace(REF, mix={"apex_docs": 1.0}))
    good.validate(BENCHES, max_modifications=1, domains=doms)
    assert good.modifications == [{"field": "mix", "from": {"stdlib": 1.0}, "to": {"apex_docs": 1.0}}]
    assert good.fingerprint == plan(replace(REF, mix={"apex_docs": 1.0})).fingerprint  # identité = recette
    with pytest.raises(InvalidPlanError, match="sans objet"):
        plan(REF).validate(BENCHES, max_modifications=1, domains=doms)
    with pytest.raises(InvalidPlanError, match="attribution impossible"):
        plan(replace(REF, mix={"apex_docs": 1.0}, lr=5e-4)).validate(
            BENCHES, max_modifications=1, domains=doms
        )
    with pytest.raises(InvalidPlanError, match="hors budget"):
        plan(replace(REF, steps=500)).validate(BENCHES, max_modifications=1, domains=doms)
    with pytest.raises(InvalidPlanError, match="domaines inconnus"):
        plan(replace(REF, mix={"x": 1.0})).validate(BENCHES, max_modifications=1, domains=doms)
    with pytest.raises(InvalidPlanError, match="vide"):
        plan(replace(REF, steps=0)).validate(BENCHES, max_modifications=1, domains=doms)
    restored = ExperimentPlan.from_dict(good.to_dict())
    assert restored.fingerprint == good.fingerprint and restored.experiment_id == good.experiment_id
    tampered = good.to_dict() | {"recipe": replace(REF, steps=150).to_dict()}
    with pytest.raises(InvalidPlanError, match="incohérente"):
        ExperimentPlan.from_dict(tampered)


# Registre : retour arrière ---------------------------------------------------------


def _reports(values: list[float]) -> dict[str, EvalReport]:
    return {
        "heldout": EvalReport(
            "heldout",
            "sha256:b",
            "m",
            tuple(DocScore(f"d{i}", v, 0.5, 100, 30) for i, v in enumerate(values)),
        )
    }


BASE = [3.0 + 0.01 * i for i in range(12)]


def test_registry_rollback_restores_only_previously_promoted_versions(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "r.jsonl")
    with pytest.raises(LookupError):
        registry.rollback("rien")
    registry.propose(Proposal("v1", "s1", "d", _reports(BASE), "accept", artifact="a1"))
    with pytest.raises(LookupError, match="antérieure"):
        registry.rollback("seul champion")
    registry.propose(Proposal("bad", "sb", "d", _reports([v + 1 for v in BASE]), "n/a", artifact="ab"))
    registry.propose(Proposal("v2", "s2", "d", _reports([v - 1 for v in BASE]), "n/a", artifact="a2"))
    assert registry.current()["name"] == "v2"  # type: ignore[index]
    back = registry.rollback("checkpoint corrompu")
    assert back["name"] == "v1" and back["artifact"] == "a1" and back["kind"] == "rollback"
    assert registry.current()["name"] == "v1"  # type: ignore[index]
    with pytest.raises(LookupError):
        registry.rollback("plus rien avant v1")
    assert [e["name"] for e in registry.promoted_history()] == ["v1", "v2", "v1"]


# Analyse d'échec ---------------------------------------------------------------------


def _comp(suite: str, base: float, cand: float, low: float, high: float, verdict: str) -> dict[str, object]:
    return {
        "suite": suite,
        "baseline_bpb": base,
        "candidate_bpb": cand,
        "bpb_gain_ci": [low, high],
        "verdict": verdict,
        "n": 40,
        "baseline_accuracy": 0,
        "candidate_accuracy": 0,
        "bpb_gain_mean": 0,
    }


def _judged(
    decision: str, comps: list[dict[str, object]], checks: dict[str, bool] | None = None
) -> JudgeResult:
    return JudgeResult(
        decision, ["raison"], {k: {"ok": v, "detail": k} for k, v in (checks or {}).items()}, comps
    )


def test_failure_analysis_establishes_only_what_is_measured() -> None:
    p = plan(replace(REF, mix={"apex_docs": 1.0}))
    forgetting = analyze_judgement(
        p,
        _judged(
            "reject",
            [
                _comp("heldout", 4.0, 3.8, 0.1, 0.3, "significant"),
                _comp("heldout:apex_docs", 5.0, 3.5, 1.2, 1.8, "significant"),
                _comp("heldout:stdlib", 3.0, 3.6, -0.7, -0.5, "significant"),
                _comp("validation", 4.0, 4.0, -0.01, 0.01, "not_significant"),
            ],
        ),
    )
    assert forgetting is not None and forgetting.cause is FailureCause.PROTECTED_REGRESSION
    assert forgetting.status == "established" and forgetting.attribution == "single_modification"
    assert forgetting.regressed_suites == ["heldout:stdlib"] and forgetting.reusable
    assert forgetting.to_dict()["interpretation_label"] == "INFERRED"
    assert forgetting.interpretation is not None and "mélange" in forgetting.interpretation
    # Même compromis mesuré, mais la seule modification est le taux d'apprentissage : la lecture
    # ne doit pas invoquer un mécanisme de mélange de données (défaut trouvé sur l'exécution réelle).
    lr_only = analyze_judgement(
        plan(replace(REF, lr=5e-4)),
        _judged(
            "reject",
            [
                _comp("heldout", 4.0, 4.0, -0.01, 0.01, "not_significant"),
                _comp("heldout:apex_docs", 5.0, 4.9, 0.01, 0.03, "significant"),
                _comp("heldout:stdlib", 3.0, 3.1, -0.2, -0.05, "significant"),
                _comp("validation", 4.0, 4.0, -0.01, 0.01, "not_significant"),
            ],
        ),
    )
    assert lr_only is not None and lr_only.cause is FailureCause.PROTECTED_REGRESSION
    assert lr_only.interpretation is not None
    assert "mélange" not in lr_only.interpretation and "« lr » (0.001 -> 0.0005)" in lr_only.interpretation
    flat = analyze_judgement(
        p,
        _judged(
            "inconclusive",
            [
                _comp("heldout", 4.0, 3.99, -0.02, 0.03, "not_significant"),
                _comp("heldout:apex_docs", 5.0, 4.98, -0.02, 0.05, "not_significant"),
            ],
        ),
    )
    assert (
        flat is not None
        and flat.cause is FailureCause.NO_SIGNIFICANT_EFFECT
        and flat.interpretation is None
    )
    worse = analyze_judgement(
        p,
        _judged(
            "reject",
            [
                _comp("heldout", 4.0, 4.5, -0.6, -0.4, "significant"),
                _comp("heldout:apex_docs", 5.0, 5.5, -0.6, -0.4, "significant"),
            ],
        ),
    )
    assert worse is not None and worse.cause is FailureCause.TARGET_REGRESSION
    leak = analyze_judgement(
        p, _judged("reject", [], {"no_contamination": False, "candidate_integrity": True})
    )
    assert leak is not None and leak.cause is FailureCause.CONTAMINATION and leak.attribution == "none"
    assert analyze_judgement(p, _judged("accept", [])) is None
    confounded = analyze_judgement(
        plan(replace(REF, mix={"apex_docs": 1.0}, lr=5e-4)),
        _judged(
            "reject",
            [
                _comp("heldout", 4.0, 3.8, 0.1, 0.3, "significant"),
                _comp("heldout:apex_docs", 5.0, 3.5, 1.2, 1.8, "significant"),
                _comp("heldout:stdlib", 3.0, 3.6, -0.7, -0.5, "significant"),
            ],
        ),
    )
    assert (
        confounded is not None
        and confounded.attribution == "confounded"
        and confounded.interpretation is None
    )


def test_exceptions_are_classified_or_left_unknown() -> None:
    from apex.errors import TimeoutExceeded

    p = plan(replace(REF, mix={"apex_docs": 1.0}))
    assert analyze_exception(p, FloatingPointError("nan")).cause is FailureCause.DIVERGED
    assert analyze_exception(p, TimeoutExceeded("x", 1.0)).cause is FailureCause.TIMEOUT
    unknown = analyze_exception(p, RuntimeError("quelque chose"))
    assert unknown.cause is FailureCause.UNKNOWN and unknown.status == "unknown"


# Stratégie ---------------------------------------------------------------------------


def _observation(world: World, artifact: str = "sha256:" + "c" * 64) -> Observation:
    from apex.neural.loop.observe import ChampionState

    champion = ChampionState("c", 0, artifact, REF.to_dict() | {"init": "scratch"}, {}, True, "ok")
    return Observation(world.fingerprint, sorted(world.benchmarks), champion, [WEAK])


def _record(
    journal: ExperimentJournal,
    p: ExperimentPlan,
    cause: str | None,
    promoted: bool = False,
    regressed: list[str] | None = None,
    tokens_seen: int | None = None,
    candidate_reports: dict[str, object] | None = None,
) -> None:
    journal.experiment(p.experiment_id, "planned", plan=p.to_dict())
    if tokens_seen is not None:
        journal.experiment(p.experiment_id, "executed", tokens_seen=tokens_seen)
    if candidate_reports is not None:
        journal.experiment(
            p.experiment_id, "judged", result={"decision": "reject"}, candidate_reports=candidate_reports
        )
    journal.experiment(
        p.experiment_id,
        "analyzed",
        analysis={"cause": cause, "status": "established", "regressed_suites": regressed or []},
    )
    journal.experiment(p.experiment_id, "promotion", promoted=promoted)


def test_policy_adapts_to_failures_and_never_repeats_a_plan(tmp_path: Path) -> None:
    world = support.world()
    obs = _observation(world)
    journal = ExperimentJournal(tmp_path / "j.jsonl")
    policy = StrategyPolicy(finetune_steps=100, finetune_tokens=10_000, max_steps=400, max_tokens=40_000)

    def plan_of(decision: StrategyDecision) -> ExperimentPlan:
        assert obs.champion is not None and decision.baseline_recipe is not None
        return ExperimentPlan(
            world.fingerprint,
            {"name": "c", "artifact": obs.champion.artifact},
            decision.baseline_recipe,
            decision.recipe,
            decision.hypothesis,
            Budget(400, 40_000, 60.0),
            parent_experiment_id=decision.parent_experiment_id,
        )

    first = policy.decide(obs, world, journal, exhausted=set())
    assert first is not None and first.strategy == "target_only" and first.recipe.mix == {"apex_docs": 1.0}
    p1 = plan_of(first)
    _record(journal, p1, "protected_regression", regressed=["heldout:stdlib"])
    second = policy.decide(obs, world, journal, exhausted=set())
    assert second is not None and second.strategy == "protect_domains"
    assert (
        second.recipe.mix == {"apex_docs": 0.75, "stdlib": 0.25}
        and second.parent_experiment_id == p1.experiment_id
    )
    assert second.baseline_recipe is not None
    assert len(second.recipe.diff(second.baseline_recipe)) == 1  # une seule modification
    p2 = plan_of(second)
    _record(journal, p2, "protected_regression", regressed=["heldout:stdlib"])
    third = policy.decide(obs, world, journal, exhausted=set())
    assert third is not None and third.recipe.mix == {"apex_docs": 0.5, "stdlib": 0.5}
    p3 = plan_of(third)
    _record(journal, p3, "no_significant_effect")
    fourth = policy.decide(obs, world, journal, exhausted=set())
    assert fourth is not None and fourth.strategy == "more_steps" and fourth.recipe.steps == 200
    p4 = plan_of(fourth)
    # Toute décision doit donner un plan VALIDE (une seule modification) : doubler aussi les
    # tokens en faisait deux et la boucle aurait planté sur ce chemin.
    for plan in (p1, p2, p3, p4):
        plan.validate(set(world.suite_names), max_modifications=1, domains=set(world.spec.domains))
    _record(journal, p4, "no_significant_effect")
    fifth = policy.decide(obs, world, journal, exhausted=set())
    assert fifth is not None and fifth.recipe.steps == 400
    _record(journal, plan_of(fifth), "no_significant_effect")
    assert policy.decide(obs, world, journal, exhausted=set()) is None  # budget épuisé : rien à inventer
    assert policy.decide(obs, world, journal, exhausted={"heldout:apex_docs"}) is None


def test_policy_uses_memory_across_champions(tmp_path: Path) -> None:
    world = support.world()
    journal = ExperimentJournal(tmp_path / "j.jsonl")
    old = _observation(world, artifact="sha256:" + "d" * 64)
    policy = StrategyPolicy(finetune_steps=100, finetune_tokens=10_000)
    naive = policy.decide(old, world, journal, exhausted=set())
    assert naive is not None
    p = ExperimentPlan(
        world.fingerprint,
        {"name": "old", "artifact": old.champion.artifact},  # type: ignore[union-attr]
        naive.baseline_recipe,
        naive.recipe,
        naive.hypothesis,
        Budget(400, 40_000, 60.0),
    )
    _record(journal, p, "protected_regression", regressed=["heldout:stdlib"])
    fresh = policy.decide(_observation(world), world, journal, exhausted=set())
    assert fresh is not None and fresh.strategy == "protect_domains"
    assert any("non retenté" in r for r in fresh.rationale)


def test_policy_retries_unknown_failures_a_bounded_number_of_times(tmp_path: Path) -> None:
    world = support.world()
    obs = _observation(world)
    journal = ExperimentJournal(tmp_path / "j.jsonl")
    policy = StrategyPolicy(finetune_steps=100, finetune_tokens=10_000, max_retries=1)
    decision = policy.decide(obs, world, journal, exhausted=set())
    assert decision is not None

    def mk() -> ExperimentPlan:
        assert decision is not None and obs.champion is not None
        return ExperimentPlan(
            world.fingerprint,
            {"name": "c", "artifact": obs.champion.artifact},
            decision.baseline_recipe,
            decision.recipe,
            decision.hypothesis,
            Budget(400, 40_000, 60.0),
        )

    _record(journal, mk(), "unknown")
    retry = policy.decide(obs, world, journal, exhausted=set())
    assert retry is not None and retry.strategy == "retry" and retry.recipe == decision.recipe
    _record(journal, mk(), "unknown")
    assert policy.decide(obs, world, journal, exhausted=set()) is None  # jamais de retry infini


def test_weakness_detection_cites_measured_evidence() -> None:
    world = support.world()

    def rep(name: str, value: float, n: int) -> EvalReport:
        return EvalReport(
            name,
            "sha256:" + "f" * 64,
            "m",
            tuple(DocScore(f"{name}{i}", value, 0.5, 10, 3) for i in range(n)),
        )

    reports = {
        "heldout:stdlib": rep("heldout:stdlib", 3.0, 30),
        "heldout:apex_docs": rep("heldout:apex_docs", 3.6, 20),
    }
    found = find_weaknesses(world, reports, min_gap=0.1)
    assert [w.benchmark for w in found] == ["heldout:apex_docs"] and found[0].documents == 20
    assert found[0].gap == pytest.approx(0.6)
    assert find_weaknesses(world, reports, min_gap=1.0) == []


def test_policy_adapts_when_even_the_trained_domain_regresses(tmp_path: Path) -> None:
    """Constat de l'exécution réelle P2 : un affinage régressait sur TOUS les domaines, y compris
    celui qu'il entraînait. Augmenter la part des domaines dégradés n'a alors aucun sens."""
    world = support.world()
    obs = _observation(world)
    journal = ExperimentJournal(tmp_path / "j.jsonl")
    policy = StrategyPolicy(finetune_steps=100, finetune_tokens=10_000, max_steps=400, max_tokens=40_000)
    first = policy.decide(obs, world, journal, exhausted=set())
    assert (
        first is not None and first.recipe.mix == {"apex_docs": 1.0} and first.baseline_recipe is not None
    )
    plan = ExperimentPlan(
        world.fingerprint,
        {"name": "c", "artifact": obs.champion.artifact},  # type: ignore[union-attr]
        first.baseline_recipe,
        first.recipe,
        first.hypothesis,
        Budget(400, 40_000, 60.0),
    )
    _record(journal, plan, "protected_regression", regressed=["heldout:apex_docs", "heldout:stdlib"])
    second = policy.decide(obs, world, journal, exhausted=set())
    assert second is not None and second.strategy == "more_data"
    assert second.recipe.tokens == 20_000 and second.recipe.diff(first.recipe) == [
        {"field": "tokens", "from": 10_000, "to": 20_000}
    ]
    assert any("INFERRED" in line for line in second.rationale)


def test_policy_adds_data_first_when_the_sample_was_reread_many_times(tmp_path: Path) -> None:
    """Constat du run réel P2 : 819 200 jetons vus pour 60 000 tirés (13,7 passages) ; la cible
    régressait et la politique ne proposait que de baisser le taux d'apprentissage."""
    world = support.world()
    obs = _observation(world)
    policy = StrategyPolicy(finetune_steps=100, finetune_tokens=10_000, max_steps=400, max_tokens=40_000)

    def after(tokens_seen: int, cause: str) -> StrategyDecision:
        journal = ExperimentJournal(tmp_path / f"{tokens_seen}-{cause}.jsonl")
        first = policy.decide(obs, world, journal, exhausted=set())
        assert first is not None and first.baseline_recipe is not None
        plan = ExperimentPlan(
            world.fingerprint,
            {"name": "c", "artifact": obs.champion.artifact},  # type: ignore[union-attr]
            first.baseline_recipe,
            first.recipe,
            first.hypothesis,
            Budget(400, 40_000, 60.0),
        )
        _record(journal, plan, cause, regressed=["heldout:apex_docs"], tokens_seen=tokens_seen)
        decision = policy.decide(obs, world, journal, exhausted=set())
        assert decision is not None
        return decision

    reread = after(137_000, "target_regression")  # 13,7 passages sur 10 000 jetons
    assert reread.strategy == "more_data" and reread.recipe.tokens == 20_000
    assert any(line.startswith("ESTABLISHED : 137000 jetons vus") for line in reread.rationale)
    assert after(20_000, "target_regression").strategy == "lower_lr"  # 2 passages : pas de sur-lecture
    assert after(137_000, "protected_regression").strategy == "more_data"


def test_policy_changes_lever_according_to_the_measured_effect_of_the_last_one(tmp_path: Path) -> None:
    """Constat du run réel (apprentissage continu) : diviser le taux d'apprentissage a réduit
    l'oubli de `code_repair` mais laissé `program_trace` entièrement oublié ; la politique divisait
    encore le taux. L'effet MESURÉ du levier (candidat contre candidat précédent, par élément)
    décide maintenant : répété s'il aide partout, remplacé pour les suites qu'il n'aide pas,
    annulé s'il n'aide nulle part."""
    world = support.world()
    obs = _observation(world)
    policy = StrategyPolicy(finetune_steps=100, finetune_tokens=10_000, max_steps=400, max_tokens=10_000)
    regressed = ["heldout:apex_docs", "heldout:stdlib"]

    def report(name: str, level: float) -> dict[str, object]:
        scores = tuple(DocScore(f"{name}#{i}", level + 0.01 * (i % 7), 0.5, 100, 20) for i in range(40))
        return EvalReport(name, "sha256:" + "e" * 64, "m", scores).to_dict()

    def plan_of(decision: StrategyDecision) -> ExperimentPlan:
        assert obs.champion is not None and decision.baseline_recipe is not None
        return ExperimentPlan(
            world.fingerprint,
            {"name": "c", "artifact": obs.champion.artifact},
            decision.baseline_recipe,
            decision.recipe,
            decision.hypothesis,
            Budget(400, 40_000, 60.0),
            parent_experiment_id=decision.parent_experiment_id,
        )

    def after_lower_lr(
        docs: float, stdlib: float, name: str
    ) -> tuple[ExperimentPlan, StrategyDecision | None]:
        journal = ExperimentJournal(tmp_path / f"{name}.jsonl")
        first = policy.decide(obs, world, journal, exhausted=set())
        assert first is not None and first.recipe.mix == {"apex_docs": 1.0}
        p1 = plan_of(first)
        _record(
            journal,
            p1,
            "protected_regression",
            regressed=regressed,
            candidate_reports={
                "heldout:apex_docs": report("heldout:apex_docs", 5.0),
                "heldout:stdlib": report("heldout:stdlib", 4.0),
            },
        )
        second = policy.decide(obs, world, journal, exhausted=set())
        assert second is not None and second.strategy == "lower_lr"  # plus de données : hors budget
        p2 = plan_of(second)
        _record(
            journal,
            p2,
            "protected_regression",
            regressed=regressed,
            candidate_reports={
                "heldout:apex_docs": report("heldout:apex_docs", docs),
                "heldout:stdlib": report("heldout:stdlib", stdlib),
            },
        )
        return p2, policy.decide(obs, world, journal, exhausted=set())

    # Aide partout (oubli réduit sur les deux suites, encore au-delà de la garde) : on continue.
    p2, third = after_lower_lr(4.9, 3.9, "everywhere")
    assert third.strategy == "lower_lr" and third.recipe.lr == p2.recipe.lr / 2
    # Aide « apex_docs » seulement : levier gardé (recette de p2), autre levier pour « stdlib ».
    p2, third = after_lower_lr(4.9, 4.0, "partial")
    assert third.strategy == "protect_domains" and third.parent_experiment_id == p2.experiment_id
    assert third.recipe.lr == p2.recipe.lr and third.recipe.mix["stdlib"] >= 0.25
    assert "apex_docs" not in {d for d, w in third.recipe.mix.items() if w > 0} - {"apex_docs"}
    assert any("aide ['heldout:apex_docs'] mais pas ['heldout:stdlib']" in r for r in third.rationale)
    # N'aide nulle part : modification annulée, autre levier depuis la recette précédente.
    p2, third = after_lower_lr(5.0, 4.0, "nowhere")
    assert third.strategy == "protect_domains" and third.parent_experiment_id != p2.experiment_id
    assert third.recipe.lr == policy.finetune_lr and third.baseline_recipe is not None
    assert third.baseline_recipe.lr == policy.finetune_lr  # une seule modification, depuis p1
    assert any("levier sans effet" in r for r in third.rationale)
    # Aide « stdlib » mais pas le domaine ENTRAÎNÉ : ni le répéter, ni rejouer un domaine déjà
    # entraîné — plus rien de fondé à essayer, la politique s'arrête.
    _, none = after_lower_lr(5.0, 3.9, "trained_unaffected")
    assert none is None


def test_policy_remembers_which_lever_helped_which_suite_along_the_chain(tmp_path: Path) -> None:
    """Chaîne réelle rejouée en miniature : `lower_lr` réduit l'oubli de « apex_docs » (pas de
    « stdlib ») ; `protect_domains` CORRIGE entièrement « stdlib » (la suite n'est plus dégradée : cela
    compte comme un effet) mais pas « apex_docs ». La suite propose le levier qui a déjà aidé
    « apex_docs » (taux divisé), depuis la recette qui protège « stdlib »."""
    world = support.world()
    obs = _observation(world)
    policy = StrategyPolicy(finetune_steps=100, finetune_tokens=10_000, max_steps=400, max_tokens=10_000)
    journal = ExperimentJournal(tmp_path / "chain.jsonl")

    def report(name: str, level: float) -> dict[str, object]:
        scores = tuple(DocScore(f"{name}#{i}", level + 0.01 * (i % 7), 0.5, 100, 20) for i in range(40))
        return EvalReport(name, "sha256:" + "e" * 64, "m", scores).to_dict()

    def run(
        decision: StrategyDecision | None, docs: float, stdlib: float, regressed: list[str]
    ) -> ExperimentPlan:
        assert decision is not None and obs.champion is not None and decision.baseline_recipe is not None
        p = ExperimentPlan(
            world.fingerprint,
            {"name": "c", "artifact": obs.champion.artifact},
            decision.baseline_recipe,
            decision.recipe,
            decision.hypothesis,
            Budget(400, 40_000, 60.0),
            parent_experiment_id=decision.parent_experiment_id,
        )
        reports = {
            "heldout:apex_docs": report("heldout:apex_docs", docs),
            "heldout:stdlib": report("heldout:stdlib", stdlib),
        }
        _record(journal, p, "protected_regression", regressed=regressed, candidate_reports=reports)
        return p

    both = ["heldout:apex_docs", "heldout:stdlib"]
    p1 = run(policy.decide(obs, world, journal, exhausted=set()), 5.0, 4.0, both)
    p2 = run(policy.decide(obs, world, journal, exhausted=set()), 4.9, 4.0, both)
    assert p2.hypothesis.strategy == "lower_lr"
    p3 = run(policy.decide(obs, world, journal, exhausted=set()), 4.9, 3.5, ["heldout:apex_docs"])
    assert p3.hypothesis.strategy == "protect_domains" and p3.recipe.lr == p2.recipe.lr
    fourth = policy.decide(obs, world, journal, exhausted=set())
    assert fourth is not None and fourth.strategy == "lower_lr"
    assert fourth.parent_experiment_id == p3.experiment_id
    assert fourth.recipe.mix == p3.recipe.mix and fourth.recipe.lr == p3.recipe.lr / 2
    assert any("a déjà réduit l'oubli de ['heldout:apex_docs']" in r for r in fourth.rationale)
    assert p1.experiment_id != p2.experiment_id


def test_policy_never_protects_a_domain_by_repeating_its_exhausted_data(tmp_path: Path) -> None:
    """Constat du cycle 3 réel (P3.5) : la politique a porté `stdlib` (76 k jetons uniques) à 25 %
    d'un tirage de 9,6 M jetons, soit 32 relectures : BPB `stdlib` 2,67 -> 3,44, rejet du Judge.
    Protéger un domaine en répétant des données épuisées n'est pas du rejeu."""
    world = support.world()
    stdlib = world.domain_tokens("stdlib")
    encode = world.tokenizer.encode
    docs = world.train_candidates("stdlib")
    assert stdlib == sum(len(encode(c.text, add_bos=True, add_eos=True)) for c in docs)
    obs = _observation(world)

    def second_decision(tokens: int, first_mix: dict[str, float] | None) -> StrategyDecision:
        journal = ExperimentJournal(tmp_path / f"{tokens}-{bool(first_mix)}.jsonl")
        policy = StrategyPolicy(finetune_tokens=tokens, max_tokens=4 * tokens, max_steps=400)
        first = policy.decide(obs, world, journal, exhausted=set())
        assert first is not None and first.baseline_recipe is not None
        recipe = replace(first.recipe, mix=first_mix) if first_mix else first.recipe
        plan = ExperimentPlan(
            world.fingerprint,
            {"name": "c", "artifact": obs.champion.artifact},  # type: ignore[union-attr]
            first.baseline_recipe,
            recipe,
            first.hypothesis,
            Budget(400, 4 * tokens, 60.0),
        )
        _record(journal, plan, "protected_regression", regressed=["heldout:stdlib"])
        decision = policy.decide(obs, world, journal, exhausted=set())
        assert decision is not None
        return decision

    # Données suffisantes : le rejeu reste le levier (comportement inchangé).
    small = second_decision(10_000, None)
    assert small.strategy == "protect_domains" and small.recipe.mix == {"apex_docs": 0.75, "stdlib": 0.25}
    # 25 % de 1 M jetons relirait `stdlib` plus de 4 fois : aucun palier de rejeu, pas plus petit.
    large = 1_000_000
    assert 0.25 * large / stdlib > 4
    blocked = second_decision(large, None)
    assert blocked.strategy == "lower_lr" and blocked.recipe.mix == {"apex_docs": 1.0}
    assert any("palier écarté" in line for line in blocked.rationale)
    # Domaine DÉJÀ relu plus de 4 fois par la recette précédente : jamais davantage.
    saturated = second_decision(large, {"apex_docs": 0.75, "stdlib": 0.25})
    assert saturated.strategy == "lower_lr" and saturated.recipe.mix == {"apex_docs": 0.75, "stdlib": 0.25}
    assert any(f"stdlib ({stdlib} jetons" in line for line in saturated.rationale)


def test_one_exhausted_domain_does_not_deprive_the_others_of_replay(tmp_path: Path) -> None:
    """Constat P3.7 (passage à blanc sur le rejet réel du bras A) : `stdlib`, épuisé, bloquait le
    rejeu de TOUS les domaines dégradés et la politique renonçait au rejeu. Le domaine épuisé est
    écarté ; les autres domaines dégradés restent protégés."""
    from apex.neural.loop.observe import ChampionState
    from tests import capability_support as cap

    world = cap.world()
    tokens = 200_000
    assert (
        0.125 * tokens / world.domain_tokens("stdlib") > 4 >= 0.125 * tokens / world.domain_tokens("code")
    )
    weak = Weakness("heldout:code", "code", 3.0, 2.0, 1.0, 20, "mesure")
    champion = ChampionState("c", 0, "sha256:" + "c" * 64, {"mix": {"code_tasks": 1.0}}, {}, True, "ok")
    obs = Observation(world.fingerprint, sorted(world.benchmarks), champion, [weak])
    policy = StrategyPolicy(finetune_tokens=tokens, max_tokens=4 * tokens, max_steps=400)
    journal = ExperimentJournal(tmp_path / "j.jsonl")
    first = policy.decide(obs, world, journal, exhausted=set())
    assert first is not None and first.baseline_recipe is not None
    recipe = replace(first.recipe, mix={"code_tasks": 1.0})
    plan = ExperimentPlan(
        world.fingerprint,
        {"name": "c", "artifact": champion.artifact},
        first.baseline_recipe,
        recipe,
        first.hypothesis,
        Budget(400, 4 * tokens, 60.0),
    )
    _record(journal, plan, "protected_regression", regressed=["heldout:stdlib", "heldout:code"])
    decision = policy.decide(obs, world, journal, exhausted=set())
    assert decision is not None and decision.strategy == "protect_domains"
    assert decision.recipe.mix.get("code", 0) >= 0.25 and "stdlib" not in decision.recipe.mix
    assert any("écarté du rejeu" in line for line in decision.rationale)

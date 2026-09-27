"""P3 — capacité PROGRAM TRACE : générateur, vérificateur de traces, benchmark (vérité terrain
exécutée dans le sandbox, fraîcheur canonique), évaluation d'un modèle RÉEL et intégration au
monde enfant (même cerveau que `code_repair`)."""

from __future__ import annotations

import hashlib
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
def test_training_problems_sharing_code_with_an_evaluation_document_are_rejected() -> None:
    """Sur un réservoir assez grand, des collisions de 12-grammes avec les documents
    d'évaluation se produisent réellement : elles sont rejetées, et aucune ne subsiste."""
    bench = build_benchmark(sandbox_provider(), pool_size=20_000, sizes=SMALL)
    assert bench.report["pool_rejected"]["ngram"] > 0
    protected = set().union(*(document_grams(p) for p in bench.eval_problems()))
    assert all(not (document_grams(p) & protected) for p in bench.pool)


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
    rng = random.Random(1)
    many = [generate(depth, rng) for depth in (1, 2, 3, 4) for _ in range(100)]
    chosen = interventions(many, random.Random(1))
    assert len(chosen) == len(many)
    # Programmes en chaîne : toute intervention se propage, suivre et ignorer sont discernables.
    assert all(c.step < c.problem.depth and c.followed != c.ignored for c in chosen)


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
    # Mesuré (graine fixée) : 0 suivie, 5 ignorées (la trace apprise continue), 9 incohérentes.
    assert report["followed"] <= 1 and report["ignored"] >= 2


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
    # Chaque capacité héritée ou ajoutée est un groupe protégé, ancré sur sa suite d'acceptation.
    groups = child.protected_groups
    assert set(groups) == {"text", "code_repair", "program_trace"}
    for name, (capability, _) in child.capabilities.items():
        assert groups[name][0] == capability.contract.acceptance_suite
        assert set(groups[name]) == set(capability.contract.suites)
    assert set(groups["text"]) == set(child.benchmarks)


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


# -- adversarial -------------------------------------------------------------------


@needs_sandbox
@pytest.mark.parametrize("quote", ["trace_in_a_comment", "program_in_prose"])
def test_an_outside_document_quoting_an_evaluation_problem_is_decontaminated(
    small: ProgramTraceBenchmark, quote: str
) -> None:
    """Un document d'un AUTRE domaine qui cite un problème d'évaluation — sa trace dans un
    commentaire, ou seulement son programme dans de la prose — ne peut pas entrer dans
    l'entraînement : le monde le retire, quelle que soit sa source."""
    from apex.neural.dataset import TrainingCandidate
    from apex.neural.loop.world import World
    from tests import capability_support as support

    problem = small.suites[suite_name("heldout")].problems[0]
    if quote == "trace_in_a_comment":
        text = f"# exemple d'exécution\n# {problem.document}\nprint('ok')\n"
    else:
        program = problem.program.replace(";", "; ")
        # L'apostrophe rend le texte illisible pour le tokenizer Python : seule la citation
        # telle quelle du programme peut le démasquer.
        text = f"Qu'obtient-on avec le programme suivant : {program} ? Réponse attendue plus bas.\n"
    leak = TrainingCandidate(
        candidate_id="leak#0",
        text=text,
        source_id=support.SPEC.domains["code"] + "zz_quotes_an_eval_trace.py",
        content_hash="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        license_status="free",
        quality="high_value",
        origin="test:adversarial",
    )
    spec = replace(
        support.SPEC,
        domains={**support.SPEC.domains, TASKS_DOMAIN: TASKS_PREFIX},
        train_only_domains=(*support.SPEC.train_only_domains, TASKS_DOMAIN),
        capabilities=("code_repair", "program_trace"),
    )
    world = World.build(
        spec,
        (*support.candidates(), leak),
        capability_data={"code_repair": support.benchmark(), "program_trace": small},
    )
    assert leak.candidate_id in world.capability_decontaminated
    assert all(c.candidate_id != leak.candidate_id for s in Split for c in world.dataset.splits[s])


class _MergingTokenizer:
    """Tokenizer RÉEL enveloppé qui fusionne « : » avec le caractère suivant (propriété précise
    testée : une fusion à la frontière invite/réponse est refusée)."""

    def __init__(self, inner: ByteBPETokenizer) -> None:
        self.inner = inner

    def encode(self, text: str, *, add_bos: bool = False, add_eos: bool = False) -> list[int]:
        head, sep, tail = text.partition(":")
        if not sep or not tail:
            return self.inner.encode(text, add_bos=add_bos, add_eos=add_eos)
        merged = [*self.inner.encode(head, add_bos=add_bos), 10_000 + ord(tail[0])]
        return merged + self.inner.encode(tail[1:], add_eos=add_eos)


@needs_sandbox
def test_a_tokenizer_merging_prompt_and_answer_is_refused(small: ProgramTraceBenchmark) -> None:
    capability = get_capability("program_trace")
    tokenizer = ByteBPETokenizer.train([p.document for p in small.pool], 300)
    assert capability.longest_input(small, tokenizer) > 0
    with pytest.raises(ValueError, match="frontière"):
        capability.longest_input(small, _MergingTokenizer(tokenizer))  # type: ignore[arg-type]


def test_the_judge_refuses_task_details_outside_the_contract() -> None:
    from apex.neural.loop.judge import _task_changes

    good = {"s": [{"instance": "t#0", "solved": True}]}
    assert _task_changes(good, good)["s"]["solved_after"] == 1
    with pytest.raises(ValueError, match="instance"):
        _task_changes({"s": [{"id": "t#0", "solved": True}]}, good)


# -- P5-noyau : tentatives du modèle, vérifiées par le résultat seul -------------------


@needs_sandbox
def test_self_training_problems_are_new_to_the_brain(
    small: ProgramTraceBenchmark, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apex.capabilities.program_trace import self_training

    fresh = self_training.fresh_problems(small, 200, 3, random.Random(0))
    known = {p.canonical for p in small.pool} | {p.canonical for p in small.eval_problems()}
    assert len({p.canonical for p in fresh}) == 200 and not {p.canonical for p in fresh} & known
    # Générateur substitué (propriété précise) : il propose d'abord les problèmes ÉVALUÉS, renommés.
    evaluated = [p for p in small.eval_problems() if p.depth == 3]
    replay = iter(evaluated)
    real = self_training.generate

    def evaluated_first(depth: int, rng: random.Random) -> Problem:
        leaked = next(replay, None)
        return leaked if leaked else real(depth, rng)

    monkeypatch.setattr(self_training, "generate", evaluated_first)
    fresh = self_training.fresh_problems(small, 10, 3, random.Random(0))
    assert not {p.canonical for p in fresh} & {p.canonical for p in evaluated}
    with pytest.raises(ValueError, match="épuisé"):
        self_training.fresh_problems(small, 300, 1, random.Random(0))  # 200 formes en profondeur 1


def test_a_lucky_answer_is_kept_with_the_models_wrong_steps() -> None:
    """Le vérificateur de résultat garde une réponse juste malgré une étape fausse : le document est
    la trace du MODÈLE (avec son erreur), jamais la trace de référence ; elle est comptée."""
    from apex.capabilities.program_trace.self_training import SelfAttempt, arm_texts, summary

    problem = interpret("a=3;b=a+4;c=b-a")
    lucky = SelfAttempt(problem, "a3,b8,c4=4", True, True, False)
    assert arm_texts([lucky], "verified", 1) == [problem.prompt + "a3,b8,c4=4"]
    assert arm_texts([lucky], "labeled", 1) == [problem.document] != [problem.prompt + "a3,b8,c4=4"]
    assert summary([lucky])["outcome_verified_with_a_wrong_step"] == 1


def test_p5_arms_receive_the_same_number_of_documents_and_differ_only_by_source() -> None:
    from apex.capabilities.program_trace.self_training import SelfAttempt, arm_texts

    rng = random.Random(3)
    problems = [generate(3, rng) for _ in range(12)]
    wrong = [SelfAttempt(p, "a0,b0,c0,d0=0", True, False, False) for p in problems[:4]]
    right = [SelfAttempt(p, p.answer, True, True, True) for p in problems[4:]]
    attempts = [*wrong, *right]
    arms = {arm: arm_texts(attempts, arm, 8) for arm in ("labeled", "unverified", "verified", "mixed")}
    assert {len(texts) for texts in arms.values()} == {8}
    assert arms["labeled"] == [p.document for p in problems[:8]]
    assert arms["verified"] == [p.document for p in problems[4:12]]  # juste et vérifié
    assert arms["unverified"][:4] == [p.prompt + "a0,b0,c0,d0=0" for p in problems[:4]]
    labeled_half, verified_half = arms["mixed"][:4], arms["mixed"][4:]
    assert labeled_half == [p.document for p in problems[:4]]
    assert not set(labeled_half) & set(verified_half)  # problèmes distincts
    # Moitié vérifiée prise sur des problèmes DISTINCTS de ceux de la moitié étiquetée.
    first_right = arm_texts([*right, *wrong], "mixed", 8)
    assert first_right[:4] == [p.document for p in problems[4:8]]
    assert first_right[4:] == [p.document for p in problems[8:12]]
    with pytest.raises(ValueError, match="disponibles"):
        arm_texts(attempts, "verified", 9)
    with pytest.raises(ValueError, match="bras inconnu"):
        arm_texts(attempts, "oracle", 1)


@needs_sandbox
def test_outcome_verification_keeps_the_models_own_trace(
    small: ProgramTraceBenchmark, memorised: tuple[ApexLM, ByteBPETokenizer]
) -> None:
    """Tentatives RÉELLES (modèle entraîné) vérifiées par exécution sandbox : le filtre ne regarde
    que le résultat ; le document gardé est la trace écrite par le modèle ; une trace entièrement
    juste a toujours un résultat juste ; les étapes fausses gardées sont comptées."""
    from apex.capabilities.program_trace.self_training import (
        arm_texts,
        attempt_and_verify,
        documents,
        summary,
    )

    model, tokenizer = memorised
    problems = list(small.suites[suite_name("train")].problems)
    attempts = attempt_and_verify(model, tokenizer, problems, sandbox_provider())
    assert all(a.final_ok for a in attempts if a.steps_ok)
    verified = sum(a.final_ok for a in attempts)
    kept = documents(arm_texts(attempts, "verified", verified), "self:test")
    assert len(kept) == verified > 0
    outputs = {a.document() for a in attempts if a.final_ok}
    assert {d.text for d in kept} == outputs  # la trace du MODÈLE, pas celle de référence
    report = summary(attempts)
    assert report["outcome_verified"] == len(kept)
    assert report["outcome_verified_with_a_wrong_step"] == sum(
        a.final_ok and not a.steps_ok for a in attempts
    )


@needs_sandbox
def test_train_only_documents_must_be_declared_and_are_decontaminated(
    small: ProgramTraceBenchmark,
) -> None:
    from apex.capabilities.program_trace.self_training import SELF_DOMAIN, SELF_PREFIX
    from apex.neural.dataset import TrainingCandidate
    from apex.neural.loop.world import World, WorldIntegrityError
    from tests import capability_support as support

    def doc(name: str, text: str, prefix: str) -> TrainingCandidate:
        return TrainingCandidate(
            candidate_id=f"{prefix}{name}",
            text=text,
            source_id=f"{prefix}{name}",
            content_hash="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
            license_status="free",
            quality="high_value",
            origin="test:self",
        )

    prefix = SELF_PREFIX
    fresh = generate(3, random.Random(5))
    leak = small.suites[suite_name("validation")].problems[0].document
    spec = replace(
        support.SPEC,
        domains={**support.SPEC.domains, TASKS_DOMAIN: TASKS_PREFIX, SELF_DOMAIN: prefix},
        train_only_domains=(*support.SPEC.train_only_domains, TASKS_DOMAIN, SELF_DOMAIN),
        capabilities=("code_repair", "program_trace"),
    )
    data = {"code_repair": support.benchmark(), "program_trace": small}
    good, bad = doc("0", fresh.document, prefix), doc("1", leak, prefix)
    world = World.build(spec, support.candidates(), capability_data=data, train_only=[good, bad])
    assert [c.candidate_id for c in world.train_candidates(SELF_DOMAIN)] == [good.candidate_id]
    assert bad.candidate_id in world.capability_decontaminated
    with pytest.raises(WorldIntegrityError, match="train seulement"):
        World.build(spec, support.candidates(), capability_data=data, train_only=[doc("2", "x", "zz:")])

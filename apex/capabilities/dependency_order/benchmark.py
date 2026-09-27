"""Benchmark PROTÉGÉ de DEPENDENCY ORDER.

Un problème est un programme de `program_trace` dont les instructions sont MÉLANGÉES (jamais dans
l'ordre d'exécution) : `c=b-a;a=3;b=a+4?c:`. Le modèle doit retrouver l'ordre imposé par les
dépendances puis tracer dans cet ordre (`a3,b7,c4=4`). Lire de gauche à droite échoue : la tâche
exige de résoudre les dépendances (planification de l'ordre d'exécution), pas seulement de calculer.

Fraîcheur : la forme canonique du programme ORDONNÉ décide (mélanger ou renommer ne rend pas un
problème nouveau). Exclusions, tirées du benchmark `program_trace` du monde : un problème évalué
ici n'a jamais été vu à l'entraînement, même ordonné (`seen` : réservoir `program_trace`, et
`seen_grams` : ses séquences de jetons de code, contrôle du monde) ; aucun
document d'ici ne fournit la solution d'un problème `program_trace` évalué (`evaluated`).
Vérité terrain : programme ordonné EXÉCUTÉ dans le sandbox ; un résolveur hôte indépendant
(analyse du texte mélangé seul) doit retrouver le même ordre et les mêmes valeurs."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from apex.capabilities.program_trace.benchmark import BenchmarkBuildError, document_grams, sandbox_values
from apex.capabilities.program_trace.generator import NAMES, OPERATORS, Problem, generate, interpret

CAPABILITY = "dependency_order"
EVAL_DEPTHS = {"validation": 3, "heldout": 3}
TRAIN_DEPTH = 3
POOL_DEPTHS = (1, 2, 3)
SUITE_SIZES = {"train": 200, "validation": 200, "heldout": 300}
BENCHMARK_FORMAT = 1


def suite_name(split: str) -> str:
    return f"cap:{CAPABILITY}:{split}"


@dataclass(frozen=True)
class OrderProblem:
    """Programme ordonné (`base`) et ordre de présentation de ses instructions."""

    base: Problem
    order: tuple[int, ...]

    @property
    def statements(self) -> list[str]:
        steps = self.base.program.split(";")
        return [steps[i] for i in self.order]

    @property
    def shown(self) -> str:
        return ";".join(self.statements)

    @property
    def depth(self) -> int:
        return self.base.depth

    @property
    def prompt(self) -> str:
        return f"{self.shown}?{self.base.names[-1]}:"

    @property
    def answer(self) -> str:
        return self.base.answer

    @property
    def document(self) -> str:
        return self.prompt + self.answer

    @property
    def canonical(self) -> str:
        return self.base.canonical

    def to_dict(self) -> dict[str, Any]:
        return {**self.base.to_dict(), "order": list(self.order)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrderProblem:
        return cls(Problem.from_dict(data), tuple(data["order"]))


def shuffled(problem: Problem, rng: random.Random) -> OrderProblem:
    """Ordre de présentation tiré au hasard, jamais l'ordre d'exécution."""
    size = problem.depth + 1
    order = list(range(size))
    while order == sorted(order):
        rng.shuffle(order)
    return OrderProblem(problem, tuple(order))


def resolve(shown: str) -> Problem:
    """Résolveur HÔTE indépendant : retrouve l'ordre d'exécution à partir du texte mélangé seul
    (la racine est la seule affectation d'un littéral ; chaque instruction suivante part de la
    variable qui vient d'être calculée), puis l'évalue avec l'interpréteur de `program_trace`."""
    by_target: dict[str, str] = {}
    root: list[str] = []
    following: dict[str, str] = {}
    for statement in shown.split(";"):
        target, _, expr = statement.partition("=")
        if len(target) != 1 or target not in NAMES or target in by_target:
            raise ValueError(f"affectation invalide « {statement} »")
        by_target[target] = statement
        if len(expr) == 1 and expr.isdigit():
            root.append(target)
        elif len(expr) == 3 and expr[1] in OPERATORS:
            following[expr[0]] = target
        else:
            raise ValueError(f"instruction invalide « {statement} »")
    if not root:
        raise ValueError(f"aucune racine : {shown!r}")
    chain = root[:1]
    while chain[-1] in following and len(chain) <= len(by_target):
        chain.append(following[chain[-1]])
    # Deux racines, deux instructions partant de la même variable ou une instruction orpheline
    # laissent toujours une instruction hors de la chaîne.
    if len(chain) != len(by_target):
        raise ValueError(f"instructions hors d'une chaîne unique de dépendances : {shown!r}")
    return interpret(";".join(by_target[name] for name in chain))


def _digest(problems: Iterable[OrderProblem]) -> str:
    body = json.dumps([p.to_dict() for p in problems])
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class OrderSuite:
    name: str
    split: str
    problems: tuple[OrderProblem, ...]

    @property
    def fingerprint(self) -> str:
        return _digest(self.problems)

    def ids(self) -> list[str]:
        return [f"{self.split}#{i}" for i in range(len(self.problems))]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "split": self.split,
            "problems": [p.to_dict() for p in self.problems],
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrderSuite:
        suite = cls(data["name"], data["split"], tuple(OrderProblem.from_dict(p) for p in data["problems"]))
        if data.get("fingerprint") not in (None, suite.fingerprint):
            raise BenchmarkBuildError(f"suite {suite.name} : empreinte relue différente")
        return suite


@dataclass(frozen=True)
class DependencyOrderBenchmark:
    suites: dict[str, OrderSuite]
    pool: tuple[OrderProblem, ...]
    #: Formes canoniques de `program_trace` : entraînées (`seen`, interdites à l'évaluation) et
    #: évaluées (`evaluated`, interdites partout).
    seen: tuple[str, ...] = ()
    evaluated: tuple[str, ...] = ()
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        body = json.dumps(
            {
                "suites": {n: s.fingerprint for n, s in sorted(self.suites.items())},
                "pool": _digest(self.pool),
                "seen": hashlib.sha256("\n".join(self.seen).encode()).hexdigest(),
                "evaluated": hashlib.sha256("\n".join(self.evaluated).encode()).hexdigest(),
            }
        )
        return "sha256:" + hashlib.sha256(body.encode()).hexdigest()

    def eval_problems(self) -> list[OrderProblem]:
        return [p for s in self.suites.values() if s.split != "train" for p in s.problems]

    def verify(self) -> None:
        """Vérité terrain (résolveur hôte sur le texte mélangé), présentation jamais ordonnée,
        fraîcheur canonique, exclusions respectées, suite train prise dans le réservoir."""
        for problem in (*self.pool, *(p for s in self.suites.values() for p in s.problems)):
            if list(problem.order) == sorted(problem.order):
                raise BenchmarkBuildError(f"présentation dans l'ordre d'exécution : {problem.shown!r}")
            if resolve(problem.shown) != problem.base:
                raise BenchmarkBuildError(f"vérité terrain fausse pour {problem.shown!r}")
        pool_canonical = {p.canonical for p in self.pool}
        eval_canonical = [p.canonical for p in self.eval_problems()]
        if len(set(eval_canonical)) != len(eval_canonical):
            raise BenchmarkBuildError("deux problèmes d'évaluation ont la même forme canonique")
        if [c for c in eval_canonical if c in pool_canonical]:
            raise BenchmarkBuildError("problèmes d'évaluation présents à l'entraînement")
        if set(self.evaluated) & (pool_canonical | set(eval_canonical)):
            raise BenchmarkBuildError("problème évalué par program_trace présent dans le benchmark")
        if set(self.seen) & set(eval_canonical):
            raise BenchmarkBuildError("problème d'évaluation déjà entraîné par program_trace")
        if not set(self.suites[suite_name("train")].problems) <= set(self.pool):
            raise BenchmarkBuildError("la suite train doit être prise dans le réservoir d'entraînement")
        for name, suite in self.suites.items():
            depth = EVAL_DEPTHS.get(suite.split, TRAIN_DEPTH)
            if any(p.depth != depth for p in suite.problems):
                raise BenchmarkBuildError(f"suite {name} : profondeur attendue {depth}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": BENCHMARK_FORMAT,
            "suites": {n: s.to_dict() for n, s in self.suites.items()},
            "pool": [p.to_dict() for p in self.pool],
            "seen": list(self.seen),
            "evaluated": list(self.evaluated),
            "report": self.report,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DependencyOrderBenchmark:
        bench = cls(
            {n: OrderSuite.from_dict(s) for n, s in data["suites"].items()},
            tuple(OrderProblem.from_dict(p) for p in data["pool"]),
            tuple(data.get("seen", ())),
            tuple(data.get("evaluated", ())),
            data.get("report", {}),
        )
        if data.get("fingerprint") not in (None, bench.fingerprint):
            raise BenchmarkBuildError("benchmark dependency_order : empreinte relue différente")
        bench.verify()
        return bench


def build_benchmark(
    provider: Any,
    *,
    seen: Iterable[str] = (),
    evaluated: Iterable[str] = (),
    seen_grams: frozenset[tuple[str, ...]] = frozenset(),
    seed: str = "apex-dependency-order",
    pool_size: int = 30_000,
    sizes: dict[str, int] | None = None,
) -> DependencyOrderBenchmark:
    sizes = {**SUITE_SIZES, **(sizes or {})}
    rng = random.Random(seed)  # noqa: S311 - tirage reproductible, pas de cryptographie
    seen_set, evaluated_set = set(seen), set(evaluated)
    report: dict[str, Any] = {"rejected": {"excluded": 0, "duplicate": 0, "canonical": 0, "ngram": 0}}
    rejected = report["rejected"]
    canonical = set(evaluated_set)
    eval_suites: dict[str, list[OrderProblem]] = {}
    for split, depth in EVAL_DEPTHS.items():
        drawn: list[OrderProblem] = []
        while len(drawn) < sizes[split]:
            problem = generate(depth, rng)
            if problem.canonical in seen_set:
                rejected["excluded"] += 1
            elif problem.canonical not in canonical:
                candidate = shuffled(problem, rng)
                # Même règle que le monde : une séquence de 12 jetons de code partagée avec un
                # document d'entraînement de program_trace (préfixe identique, noms compris).
                if (document_grams(candidate) | document_grams(problem)) & seen_grams:  # type: ignore[arg-type]
                    rejected["ngram"] += 1
                    continue
                canonical.add(problem.canonical)
                drawn.append(candidate)
        eval_suites[split] = drawn
    protected: set[tuple[str, ...]] = set()
    for problems in eval_suites.values():
        for problem in problems:
            protected |= document_grams(problem) | document_grams(problem.base)  # type: ignore[arg-type]
    pool: list[OrderProblem] = []
    programs: set[str] = set()
    while len(pool) < pool_size:
        problem = generate(rng.choice(POOL_DEPTHS), rng)
        if problem.program in programs:
            rejected["duplicate"] += 1
        elif problem.canonical in canonical:
            rejected["canonical" if problem.canonical not in evaluated_set else "excluded"] += 1
        else:
            candidate = shuffled(problem, rng)
            if document_grams(candidate) & protected:  # type: ignore[arg-type]
                rejected["ngram"] += 1
                continue
            programs.add(problem.program)
            pool.append(candidate)
    train = rng.sample([p for p in pool if p.depth == TRAIN_DEPTH], sizes["train"])
    everything = [*pool, *(p for problems in eval_suites.values() for p in problems)]
    executed = sandbox_values(provider, [p.base for p in everything])
    wrong = [p.shown for p, v in zip(everything, executed, strict=True) if v != p.base.values]
    if wrong:
        raise BenchmarkBuildError(f"le générateur diffère de l'exécution réelle : {wrong[:3]}")
    report["sandbox_verified"] = len(everything)
    suites = {suite_name("train"): OrderSuite(suite_name("train"), "train", tuple(train))}
    for split, problems in eval_suites.items():
        suites[suite_name(split)] = OrderSuite(suite_name(split), split, tuple(problems))
    report["counts"] = {n: len(s.problems) for n, s in suites.items()} | {"pool": len(pool)}
    report["pool_depths"] = {d: sum(p.depth == d for p in pool) for d in POOL_DEPTHS}
    bench = DependencyOrderBenchmark(
        suites, tuple(pool), tuple(sorted(seen_set)), tuple(sorted(evaluated_set)), report
    )
    bench.verify()
    return bench

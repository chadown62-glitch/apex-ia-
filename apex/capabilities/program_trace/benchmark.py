"""Benchmark PROTÉGÉ de la capacité PROGRAM TRACE.

Construction (une fois, puis gelée dans le monde avec son empreinte) :

1. problèmes d'ÉVALUATION tirés d'abord, formes canoniques toutes distinctes : `validation` et
   `heldout` en profondeur 3, `deeper` en profondeur 4 (jamais entraînée : généralisation en
   longueur) ;
2. réservoir d'ENTRAÎNEMENT (profondeurs 1 à 3) : un problème est refusé si sa forme canonique est
   celle d'un problème d'évaluation (renommer ne rend pas un problème nouveau) ou s'il partage une
   séquence de jetons de code avec un document d'évaluation (même contrôle que le monde) ;
3. suite `train` : problèmes de profondeur 3 PRIS dans le réservoir (solutions vues à
   l'entraînement : sonde de mémorisation) ;
4. vérité terrain : chaque programme est EXÉCUTÉ par Python dans le sandbox ; toute différence avec
   le générateur ou avec l'évaluateur hôte refuse la construction.

La relecture d'un benchmark gelé revérifie empreintes, vérité terrain (évaluateur hôte) et
fraîcheur canonique."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from apex.capabilities.code_repair.benchmark import code_tokens, ngrams
from apex.capabilities.code_repair.harness import run_sources
from apex.capabilities.program_trace.generator import Problem, as_function, generate, interpret
from apex.sandbox.provider import SandboxProvider

CAPABILITY = "program_trace"
SPLITS = ("train", "validation", "heldout", "deeper")
EVAL_DEPTHS = {"validation": 3, "heldout": 3, "deeper": 4}
TRAIN_DEPTH = 3
POOL_DEPTHS = (1, 2, 3)
SUITE_SIZES = {"train": 200, "validation": 200, "heldout": 300, "deeper": 200}
BENCHMARK_FORMAT = 1
#: Longueur des séquences de jetons de code comparées (celle du contrôle de contamination du monde).
PROTECTION_NGRAM = 12
#: Programmes par source exécutée dans le sandbox (une source < 40 000 caractères).
_PER_SOURCE = 300


def suite_name(split: str) -> str:
    return f"cap:{CAPABILITY}:{split}"


class BenchmarkBuildError(RuntimeError):
    """Le benchmark ne peut pas être construit ou relu sans violer une garantie."""


def _digest(problems: Iterable[Problem]) -> str:
    body = json.dumps([p.to_dict() for p in problems])
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def document_grams(problem: Problem) -> set[tuple[str, ...]]:
    return ngrams(code_tokens(problem.document, normalize_names=False), PROTECTION_NGRAM)


@dataclass(frozen=True)
class TraceSuite:
    name: str
    split: str
    problems: tuple[Problem, ...]

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
    def from_dict(cls, data: dict[str, Any]) -> TraceSuite:
        suite = cls(data["name"], data["split"], tuple(Problem.from_dict(p) for p in data["problems"]))
        if data.get("fingerprint") not in (None, suite.fingerprint):
            raise BenchmarkBuildError(f"suite {suite.name} : empreinte relue différente")
        return suite


@dataclass(frozen=True)
class ProgramTraceBenchmark:
    suites: dict[str, TraceSuite]
    pool: tuple[Problem, ...]  # réservoir d'entraînement (profondeurs 1 à 3)
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        body = json.dumps(
            {
                "suites": {n: s.fingerprint for n, s in sorted(self.suites.items())},
                "pool": _digest(self.pool),
            }
        )
        return "sha256:" + hashlib.sha256(body.encode()).hexdigest()

    def eval_problems(self) -> list[Problem]:
        return [p for s in self.suites.values() if s.split != "train" for p in s.problems]

    def verify(self) -> None:
        """Garanties vérifiables sans sandbox : vérité terrain (évaluateur hôte), fraîcheur
        canonique des suites d'évaluation, suite train prise dans le réservoir."""
        for problem in (*self.pool, *(p for s in self.suites.values() for p in s.problems)):
            if interpret(problem.program) != problem:
                raise BenchmarkBuildError(f"vérité terrain fausse pour {problem.program!r}")
        pool_canonical = {p.canonical for p in self.pool}
        eval_canonical = [p.canonical for p in self.eval_problems()]
        if len(set(eval_canonical)) != len(eval_canonical):
            raise BenchmarkBuildError("deux problèmes d'évaluation ont la même forme canonique")
        leaked = [c for c in eval_canonical if c in pool_canonical]
        if leaked:
            raise BenchmarkBuildError(f"problèmes d'évaluation présents à l'entraînement : {leaked[:3]}")
        pool = set(self.pool)
        train = self.suites[suite_name("train")].problems
        if not all(p in pool for p in train):
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
            "report": self.report,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgramTraceBenchmark:
        bench = cls(
            {n: TraceSuite.from_dict(s) for n, s in data["suites"].items()},
            tuple(Problem.from_dict(p) for p in data["pool"]),
            data.get("report", {}),
        )
        if data.get("fingerprint") not in (None, bench.fingerprint):
            raise BenchmarkBuildError("benchmark program_trace : empreinte relue différente")
        bench.verify()
        return bench


def sandbox_values(
    provider: SandboxProvider, problems: list[Problem], *, child_timeout_s: float = 5.0
) -> list[tuple[int, ...]]:
    """Valeurs calculées par Python dans le sandbox, programme par programme."""
    sources = []
    for start in range(0, len(problems), _PER_SOURCE):
        chunk = problems[start : start + _PER_SOURCE]
        body = "".join(as_function(p, f"_f{i}") for i, p in enumerate(chunk))
        table = ", ".join(f"_f{i}" for i in range(len(chunk)))
        sources.append(
            f"{body}_F = [{table}]\ndef trace(k):\n    return _F[k]() if k < len(_F) else None\n"
        )
    runs = run_sources(
        provider,
        name="trace",
        sources=sources,
        inputs=[repr((k,)) for k in range(_PER_SOURCE)],
        child_timeout_s=child_timeout_s,
    )
    values: list[tuple[int, ...]] = []
    for index, run in enumerate(runs):
        if run.status != "ok" or run.outputs is None:
            raise BenchmarkBuildError(f"exécution sandbox du lot {index} : {run.status}")
        chunk_size = min(_PER_SOURCE, len(problems) - index * _PER_SOURCE)
        values.extend(tuple(json.loads(out)) for out in run.outputs[:chunk_size])
    return values


def build_benchmark(
    provider: SandboxProvider,
    *,
    seed: str = "apex-program-trace",
    pool_size: int = 60_000,
    sizes: dict[str, int] | None = None,
) -> ProgramTraceBenchmark:
    sizes = {**SUITE_SIZES, **(sizes or {})}
    rng = random.Random(seed)  # noqa: S311 - tirage reproductible, pas de cryptographie
    report: dict[str, Any] = {"pool_rejected": {"duplicate": 0, "canonical": 0, "ngram": 0}}
    eval_suites: dict[str, list[Problem]] = {}
    canonical: set[str] = set()
    for split, depth in EVAL_DEPTHS.items():
        drawn: list[Problem] = []
        while len(drawn) < sizes[split]:
            problem = generate(depth, rng)
            if problem.canonical not in canonical:
                canonical.add(problem.canonical)
                drawn.append(problem)
        eval_suites[split] = drawn
    protected: set[tuple[str, ...]] = set()
    for problems in eval_suites.values():
        for problem in problems:
            protected |= document_grams(problem)
    pool: list[Problem] = []
    programs: set[str] = set()
    rejected = report["pool_rejected"]
    while len(pool) < pool_size:
        problem = generate(rng.choice(POOL_DEPTHS), rng)
        if problem.program in programs:
            rejected["duplicate"] += 1
        elif problem.canonical in canonical:
            rejected["canonical"] += 1
        elif document_grams(problem) & protected:
            rejected["ngram"] += 1
        else:
            programs.add(problem.program)
            pool.append(problem)
    deepest = [p for p in pool if p.depth == TRAIN_DEPTH]
    train = rng.sample(deepest, sizes["train"])
    everything = [*pool, *(p for problems in eval_suites.values() for p in problems)]
    executed = sandbox_values(provider, everything)
    wrong = [p.program for p, v in zip(everything, executed, strict=True) if v != p.values]
    if wrong:
        raise BenchmarkBuildError(f"le générateur diffère de l'exécution réelle : {wrong[:3]}")
    report["sandbox_verified"] = len(everything)
    suites = {suite_name("train"): TraceSuite(suite_name("train"), "train", tuple(train))}
    for split, problems in eval_suites.items():
        suites[suite_name(split)] = TraceSuite(suite_name(split), split, tuple(problems))
    report["counts"] = {n: len(s.problems) for n, s in suites.items()} | {"pool": len(pool)}
    bench = ProgramTraceBenchmark(suites, tuple(pool), report)
    bench.verify()
    return bench

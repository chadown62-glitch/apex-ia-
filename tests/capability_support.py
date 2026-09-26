"""Monde réduit RÉEL pour les tests de la capacité CODE REPAIR : docstrings
de la bibliothèque standard (domaine « stdlib »), sources Python de la
bibliothèque standard (domaine « code », via le Knowledge Agent et son chemin
code), solutions des tâches TRAIN (domaine « code_tasks », train seulement)
et un sous-ensemble du benchmark construit par exécution sandbox réelle."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from apex.capabilities.code_repair.benchmark import CodeRepairBenchmark, build_benchmark
from apex.capabilities.code_repair.corpus import ALL
from apex.capabilities.registry import sandbox_provider
from apex.knowledge.acquisition import (
    FileAcquirer,
    LocalDirectoryDiscovery,
    PythonDocsAcquirer,
    PythonDocsDiscovery,
)
from apex.knowledge.export import to_training_candidates
from apex.knowledge.model import SourceKind
from apex.knowledge.pipeline import AgentConfig, KnowledgeAgent
from apex.neural.dataset import TrainingCandidate
from apex.neural.loop.plan import Recipe
from apex.neural.loop.world import World, WorldSpec

STDLIB = Path("/usr/lib/python3.12")
SUBSET = (
    "count_vowels",
    "reverse_words",
    "strip_vowels",
    "running_sum",
    "pairwise_diff",
    "flatten",
    "gcd",
    "clamp",
    "factorial",
    "is_sorted",
    "max_index",
    "char_frequency",
    "filter_by_value",
    "balanced_brackets",
    "parse_version",
    "validate_port",
    "check_role",
    "constant_time_equal",
)
CODE_FILES = (
    "bisect.py",
    "heapq.py",
    "fnmatch.py",
    "glob.py",
    "shlex.py",
    "textwrap.py",
    "colorsys.py",
    "keyword.py",
    "numbers.py",
    "operator.py",
    "queue.py",
    "sched.py",
    "stat.py",
    "string.py",
    "graphlib.py",
    "reprlib.py",
    "cmd.py",
    "copy.py",
    "statistics.py",
    "fractions.py",
    "difflib.py",
    "pprint.py",
    "weakref.py",
    "struct.py",
)
DOC_MODULES = (
    "json",
    "heapq",
    "textwrap",
    "functools",
    "itertools",
    "collections",
    "pathlib",
    "statistics",
)
DOMAINS = {"stdlib": "pydoc:", "code": f"file://{STDLIB}/", "code_tasks": "task:code_repair/"}
SPEC = WorldSpec(
    domains=DOMAINS,
    vocab_size=384,
    hidden_size=32,
    num_layers=1,
    num_heads=2,
    intermediate_size=64,
    context_length=384,
    validation_ratio=0.15,
    test_ratio=0.25,
    capabilities=("code_repair",),
    train_only_domains=("code_tasks",),
)


@lru_cache(maxsize=1)
def benchmark() -> CodeRepairBenchmark:
    return build_benchmark([f for f in ALL if f.fid in SUBSET], sandbox_provider(), bugs_per_function=2)


@lru_cache(maxsize=1)
def candidates() -> tuple[TrainingCandidate, ...]:
    agent = KnowledgeAgent([PythonDocsAcquirer(), FileAcquirer()], AgentConfig(chunk_words=80))
    refs = (
        PythonDocsDiscovery(DOC_MODULES).discover()
        + LocalDirectoryDiscovery(STDLIB, CODE_FILES, SourceKind.REPOSITORY, "PSF-2.0").discover()
    )
    return tuple(to_training_candidates(agent.run(refs).items, chunk_words=80))


@lru_cache(maxsize=1)
def world() -> World:
    return World.build(SPEC, candidates(), capability_data={"code_repair": benchmark()})


def recipe(steps: int = 30, mix: dict[str, float] | None = None, init: str = "scratch") -> Recipe:
    return Recipe(
        init=init,
        mix=mix or {"code": 1.0},
        steps=steps,
        lr=1e-2,
        tokens=12_000,
        batch_size=8,
        warmup_steps=5,
    )

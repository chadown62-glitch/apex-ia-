"""Monde réel réduit pour les tests de la boucle autonome : docstrings de la
bibliothèque standard (domaine « stdlib ») et documentation du dépôt
(domaine « apex_docs »), passées par le vrai Knowledge Agent ; petit modèle
pour que l'entraînement réel tienne en quelques secondes."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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

REPO = Path(__file__).resolve().parents[1]
MODULES = (
    "json",
    "heapq",
    "textwrap",
    "functools",
    "itertools",
    "collections",
    "pathlib",
    "statistics",
    "fractions",
    "string",
    "difflib",
    "shlex",
    "csv",
    "calendar",
    "fnmatch",
    "glob",
    "graphlib",
    "ipaddress",
    "operator",
    "pprint",
    "queue",
    "sched",
    "secrets",
    "uuid",
    "argparse",
    "logging",
    "decimal",
    "datetime",
)
DOMAINS = {"stdlib": "pydoc:", "apex_docs": "file://"}
SPEC = WorldSpec(
    domains=DOMAINS,
    vocab_size=320,
    hidden_size=32,
    num_layers=1,
    num_heads=2,
    intermediate_size=64,
    context_length=32,
    validation_ratio=0.15,
    test_ratio=0.25,
)


@lru_cache(maxsize=1)
def candidates() -> tuple[TrainingCandidate, ...]:
    agent = KnowledgeAgent([PythonDocsAcquirer(), FileAcquirer()], AgentConfig(chunk_words=60))
    refs = (
        PythonDocsDiscovery(MODULES).discover()
        + LocalDirectoryDiscovery(REPO / "docs", ("*.md",), SourceKind.DOCUMENTATION, "apex-own").discover()
    )
    return tuple(to_training_candidates(agent.run(refs).items, chunk_words=60))


@lru_cache(maxsize=1)
def world() -> World:
    return World.build(SPEC, candidates())


def bootstrap_recipe(steps: int = 60) -> Recipe:
    return Recipe(
        init="scratch",
        mix={"stdlib": 1.0},
        steps=steps,
        lr=1e-2,
        tokens=12_000,
        batch_size=8,
        warmup_steps=5,
    )

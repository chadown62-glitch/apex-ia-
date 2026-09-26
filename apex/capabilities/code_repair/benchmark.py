"""Benchmark PROTÉGÉ de la capacité CODE REPAIR.

Construction (une fois, puis gelée dans le monde avec son empreinte) :

1. chaque fonction de référence est exécutée dans le sandbox : ses sorties
   deviennent les sorties ATTENDUES (gardées sur l'hôte, jamais montrées au
   solveur ni envoyées au sandbox) ;
2. répartition PAR FONCTION (jamais deux bugs d'une même fonction dans deux
   suites) : familles partagées -> train / validation / held-out par hachage
   salé ; famille `parsing` -> held-out seulement (jamais vue) ; famille
   `security` -> suite de transfert seulement ;
3. bugs injectés mécaniquement (`mutation.single_edits`) ; ne sont gardés que
   ceux qu'AU MOINS UN test détecte (exécution réelle) et dont le correctif
   exact est énumérable ;
4. contrôle de quasi-doublons entre fonctions de suites différentes
   (n-grammes de jetons normalisés) : une fuite train -> évaluation refuse la
   construction.

Le solveur ne reçoit qu'une `RepairTaskView` (code bogué, nom, entrées) :
ni la référence, ni le bug injecté, ni les sorties attendues."""

from __future__ import annotations

import hashlib
import io
import json
import keyword
import random
import tokenize
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from apex.capabilities.code_repair.corpus import (
    NOVEL_CATEGORIES,
    SHARED_CATEGORIES,
    TRANSFER_CATEGORIES,
    ReferenceFunction,
)
from apex.capabilities.code_repair.harness import SourceRun, run_sources
from apex.capabilities.code_repair.mutation import candidates, compiles, single_edits
from apex.sandbox.provider import SandboxProvider

CAPABILITY = "code_repair"
SPLITS = ("train", "validation", "heldout", "transfer")
BENCHMARK_FORMAT = 1


def suite_name(split: str) -> str:
    return f"cap:{CAPABILITY}:{split}"


class BenchmarkBuildError(RuntimeError):
    """Le benchmark ne peut pas être construit sans violer une garantie."""


@dataclass(frozen=True)
class RepairTaskView:
    """Seule chose que voit le SOLVEUR."""

    instance_id: str
    name: str
    buggy: str
    inputs: tuple[str, ...]


@dataclass(frozen=True)
class RepairInstance:
    instance_id: str
    fid: str
    category: str
    level: int
    name: str
    buggy: str
    inputs: tuple[str, ...]
    expected: tuple[str, ...]  # hôte seulement
    injected: str  # provenance du bug (hôte seulement)
    candidates_sha256: str  # gèle le générateur de candidats

    def view(self) -> RepairTaskView:
        return RepairTaskView(self.instance_id, self.name, self.buggy, self.inputs)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepairInstance:
        return cls(**{**data, "inputs": tuple(data["inputs"]), "expected": tuple(data["expected"])})


def candidates_digest(buggy: str) -> str:
    listing = json.dumps([text for text, _ in candidates(buggy)])
    return "sha256:" + hashlib.sha256(listing.encode()).hexdigest()


@dataclass(frozen=True)
class RepairSuite:
    name: str
    split: str
    instances: tuple[RepairInstance, ...]

    @property
    def fingerprint(self) -> str:
        body = json.dumps(
            {"name": self.name, "instances": [i.to_dict() for i in self.instances]}, sort_keys=True
        )
        return "sha256:" + hashlib.sha256(body.encode()).hexdigest()

    @property
    def functions(self) -> list[str]:
        return sorted({i.fid for i in self.instances})

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "split": self.split,
            "instances": [i.to_dict() for i in self.instances],
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepairSuite:
        suite = cls(
            data["name"], data["split"], tuple(RepairInstance.from_dict(i) for i in data["instances"])
        )
        if data.get("fingerprint") not in (None, suite.fingerprint):
            raise BenchmarkBuildError(f"suite {suite.name} : empreinte relue différente")
        return suite


@dataclass(frozen=True)
class CodeRepairBenchmark:
    suites: dict[str, RepairSuite]
    references: dict[str, dict[str, str]]  # fid -> {split, category, source} (hôte seulement)
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        body = json.dumps({n: s.fingerprint for n, s in sorted(self.suites.items())})
        return "sha256:" + hashlib.sha256(body.encode()).hexdigest()

    def eval_references(self) -> list[str]:
        """Sources de référence des suites d'ÉVALUATION (tout sauf train)."""
        return [r["source"] for r in self.references.values() if r["split"] != "train"]

    def train_references(self) -> dict[str, str]:
        return {fid: r["source"] for fid, r in self.references.items() if r["split"] == "train"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": BENCHMARK_FORMAT,
            "suites": {n: s.to_dict() for n, s in self.suites.items()},
            "references": self.references,
            "report": self.report,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeRepairBenchmark:
        bench = cls(
            {n: RepairSuite.from_dict(s) for n, s in data["suites"].items()},
            data["references"],
            data.get("report", {}),
        )
        if data.get("fingerprint") not in (None, bench.fingerprint):
            raise BenchmarkBuildError("benchmark code_repair : empreinte relue différente")
        return bench


# -- contrôles de contamination --------------------------------------------------


def code_tokens(source: str, *, normalize_names: bool) -> list[str]:
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER, tokenize.STRING):
                if normalize_names and tok.type == tokenize.NAME and not keyword.iskeyword(tok.string):
                    out.append("ID")
                else:
                    out.append(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        out = source.split()
    return out


def ngrams(tokens: Sequence[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def similarity(a: str, b: str, n: int = 6) -> float:
    ga, gb = (
        ngrams(code_tokens(a, normalize_names=True), n),
        ngrams(code_tokens(b, normalize_names=True), n),
    )
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def level_of(source: str) -> int:
    lines = [line for line in source.splitlines() if line.strip()]
    return 1 if len(lines) <= 6 else 2 if len(lines) <= 11 else 3


def similarity_clusters(functions: Sequence[ReferenceFunction], threshold: float) -> list[list[str]]:
    """Grappes (union-find) de fonctions dont le code est trop proche : une
    grappe entière va dans UNE seule suite (sinon apprendre l'une ferait
    « généraliser » artificiellement à l'autre)."""
    parent = {f.fid: f.fid for f in functions}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(functions):
        for b in functions[i + 1 :]:
            if similarity(a.source, b.source) > threshold:
                parent[find(a.fid)] = find(b.fid)
    groups: dict[str, list[str]] = {}
    for f in functions:
        groups.setdefault(find(f.fid), []).append(f.fid)
    return [sorted(g) for g in groups.values()]


def assign_splits(functions: Sequence[ReferenceFunction], salt: str, threshold: float) -> dict[str, str]:
    assignment: dict[str, str] = {}
    by_id = {f.fid: f for f in functions}
    for f in functions:
        if f.category in TRANSFER_CATEGORIES:
            assignment[f.fid] = "transfer"
        elif f.category in NOVEL_CATEGORIES:
            assignment[f.fid] = "heldout"
        elif f.category not in SHARED_CATEGORIES:
            raise BenchmarkBuildError(f"catégorie inconnue « {f.category} »")
    shared = [f for f in functions if f.category in SHARED_CATEGORIES]
    clusters = sorted(
        similarity_clusters(shared, threshold),
        key=lambda g: hashlib.sha256(f"{salt}:{'|'.join(g)}".encode()).hexdigest(),
    )
    total = len(shared)
    done = 0
    for group in clusters:
        split = "train" if done < 0.5 * total else "validation" if done < 0.7 * total else "heldout"
        assignment.update(dict.fromkeys(group, split))
        done += len(group)
    # Les familles jamais vues (parsing, security) ne doivent ressembler à AUCUNE fonction train.
    for fid, split in assignment.items():
        if split == "train":
            continue
        for other, other_split in assignment.items():
            if other_split == "train" and similarity(by_id[fid].source, by_id[other].source) > threshold:
                raise BenchmarkBuildError(f"{fid} ({split}) trop proche de {other} (train)")
    return assignment


def build_benchmark(
    functions: Sequence[ReferenceFunction],
    provider: SandboxProvider,
    *,
    salt: str = "apex-code-repair",
    bugs_per_function: int = 3,
    child_timeout_s: float = 1.0,
    max_cross_similarity: float = 0.6,
) -> CodeRepairBenchmark:
    report: dict[str, Any] = {"excluded": [], "cross_split_similarity": [], "mutants_tested": 0}
    assignment = assign_splits(functions, salt, max_cross_similarity)
    by_split: dict[str, list[RepairInstance]] = {s: [] for s in SPLITS}
    references: dict[str, dict[str, str]] = {}
    for fn in functions:
        name = fn.source.split("(", 1)[0].split()[-1]
        inputs = [repr(t) for t in fn.tests]
        (ref_run,) = run_sources(
            provider, name=name, sources=[fn.source], inputs=inputs, child_timeout_s=child_timeout_s
        )
        if ref_run.status != "ok" or ref_run.outputs is None:
            raise BenchmarkBuildError(f"référence {fn.fid} non exécutable : {ref_run.status}")
        expected = ref_run.outputs
        references[fn.fid] = {"split": assignment[fn.fid], "category": fn.category, "source": fn.source}
        rng = random.Random(f"{salt}:{fn.fid}")  # noqa: S311 - tirage reproductible, pas de cryptographie
        pool = [e for e in single_edits(fn.source) if compiles(e.apply(fn.source))]
        rng.shuffle(pool)
        kept: list[RepairInstance] = []
        seen: set[str] = set()
        for start in range(0, len(pool), 12):
            batch = [e for e in pool[start : start + 12] if e.apply(fn.source) not in seen]
            seen.update(e.apply(fn.source) for e in batch)
            runs: list[SourceRun] = run_sources(
                provider,
                name=name,
                sources=[e.apply(fn.source) for e in batch],
                inputs=inputs,
                child_timeout_s=child_timeout_s,
            )
            report["mutants_tested"] += len(batch)
            for edit, run in zip(batch, runs, strict=True):
                buggy = edit.apply(fn.source)
                detected = run.status != "ok" or run.outputs != expected
                if not detected or fn.source not in [t for t, _ in candidates(buggy)]:
                    continue
                kept.append(
                    RepairInstance(
                        instance_id=f"{fn.fid}#{len(kept)}",
                        fid=fn.fid,
                        category=fn.category,
                        level=level_of(fn.source),
                        name=name,
                        buggy=buggy,
                        inputs=tuple(inputs),
                        expected=expected,
                        injected=edit.describe(),
                        candidates_sha256=candidates_digest(buggy),
                    )
                )
                if len(kept) >= bugs_per_function:
                    break
            if len(kept) >= bugs_per_function:
                break
        if not kept:
            report["excluded"].append({"fid": fn.fid, "reason": "aucun bug détectable par les tests"})
            continue
        by_split[assignment[fn.fid]].extend(kept)

    # Quasi-doublons entre suites : une fonction d'évaluation trop proche d'une autre suite fuit.
    fids = sorted(references)
    for i, a in enumerate(fids):
        for b in fids[i + 1 :]:
            if references[a]["split"] == references[b]["split"]:
                continue
            sim = similarity(references[a]["source"], references[b]["source"])
            if sim > max_cross_similarity:
                report["cross_split_similarity"].append({"a": a, "b": b, "similarity": round(sim, 3)})
    if report["cross_split_similarity"]:
        raise BenchmarkBuildError(
            f"fonctions quasi identiques entre suites : {report['cross_split_similarity']}"
        )
    suites = {suite_name(s): RepairSuite(suite_name(s), s, tuple(by_split[s])) for s in SPLITS}
    report["counts"] = {
        n: {"functions": len(s.functions), "instances": len(s.instances)} for n, s in suites.items()
    }
    return CodeRepairBenchmark(suites, references, report)

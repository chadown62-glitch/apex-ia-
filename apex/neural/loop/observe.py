"""OBSERVE : état réel de la boucle, lu dans les systèmes existants.

- champion : entrée courante du registre des versions (`ModelRegistry`), son
  checkpoint relu dans l'ArtifactStore (intégrité vérifiée) et ses mesures
  (celles du Judge au moment de sa promotion) ;
- benchmarks et métriques : ceux du monde gelé ;
- faiblesses : tranches de domaine dont le BPB dépasse de `min_gap` la
  meilleure tranche — chaque faiblesse cite son benchmark, sa valeur, sa
  référence et son nombre de documents ;
- mémoire : expériences du journal (terminées, promues, échecs et causes)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apex.artifacts.provider import ArtifactStoreProvider
from apex.neural.evaluation import EvalReport
from apex.neural.loop.journal import ExperimentJournal
from apex.neural.loop.plan import Weakness
from apex.neural.loop.world import PRIMARY, World
from apex.neural.registry import ModelRegistry


@dataclass(frozen=True)
class ChampionState:
    name: str
    index: int
    artifact: str | None
    recipe: dict[str, Any] | None
    reports: dict[str, EvalReport]
    integrity_ok: bool
    integrity_detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "index": self.index,
            "artifact": self.artifact,
            "recipe": self.recipe,
            "metrics": {n: round(r.bits_per_byte, 5) for n, r in self.reports.items()},
            "integrity_ok": self.integrity_ok,
            "integrity_detail": self.integrity_detail,
        }


@dataclass(frozen=True)
class Observation:
    world_fingerprint: str
    benchmarks: list[str]
    champion: ChampionState | None
    weaknesses: list[Weakness]
    memory: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "world_fingerprint": self.world_fingerprint,
            "benchmarks": self.benchmarks,
            "champion": self.champion.to_dict() if self.champion else None,
            "weaknesses": [w.to_dict() for w in self.weaknesses],
            "memory": self.memory,
        }


def find_weaknesses(world: World, reports: dict[str, EvalReport], *, min_gap: float) -> list[Weakness]:
    slices = {name: r for name, r in reports.items() if name.startswith(f"{PRIMARY}:")}
    if len(slices) < 2:
        return []
    best = min(r.bits_per_byte for r in slices.values())
    found = []
    for name, report in slices.items():
        gap = report.bits_per_byte - best
        if gap >= min_gap:
            found.append(
                Weakness(
                    benchmark=name,
                    domain=name.split(":", 1)[1],
                    value=round(report.bits_per_byte, 5),
                    reference=round(best, 5),
                    gap=round(gap, 5),
                    documents=len(report.scores),
                    evidence=(
                        f"{name} : {report.bits_per_byte:.4f} bpb contre {best:.4f} pour la meilleure "
                        f"tranche ({len(report.scores)} documents, benchmark "
                        f"{report.benchmark_fingerprint[:19]})"
                    ),
                )
            )
    return sorted(found, key=lambda w: w.gap, reverse=True)


def observe(
    world: World,
    registry: ModelRegistry,
    journal: ExperimentJournal,
    store: ArtifactStoreProvider,
    *,
    min_gap: float = 0.1,
) -> Observation:
    current = registry.current()
    champion = None
    weaknesses: list[Weakness] = []
    if current is not None:
        reports = {name: EvalReport.from_dict(r) for name, r in current["reports"].items()}
        artifact = current.get("artifact")
        ok, detail = False, "aucun artefact enregistré : checkpoint irrécupérable"
        if artifact:
            try:
                store.get(artifact)
                ok, detail = True, "checkpoint relu, hash vérifié"
            except Exception as exc:  # absent ou altéré
                ok, detail = False, f"{exc.__class__.__name__}: {exc}"
        champion = ChampionState(
            name=current["name"],
            index=current["index"],
            artifact=artifact,
            recipe=current.get("metadata", {}).get("recipe"),
            reports=reports,
            integrity_ok=ok,
            integrity_detail=detail,
        )
        weaknesses = find_weaknesses(world, reports, min_gap=min_gap)
    records = journal.experiments().values()
    memory = {
        "experiments": len(records),
        "finished": sum(r.finished for r in records),
        "promoted": [r.experiment_id for r in records if r.promoted],
        "failures": [
            {
                "experiment_id": r.experiment_id,
                "strategy": r.plan.get("hypothesis", {}).get("strategy"),
                "target": r.plan.get("target_benchmark"),
                "cause": (r.analysis or {}).get("cause"),
                "decision": r.decision,
            }
            for r in records
            if r.finished and not r.promoted
        ],
        "unfinished": [r.experiment_id for r in records if not r.finished],
    }
    return Observation(world.fingerprint, sorted(world.benchmarks), champion, weaknesses, memory)

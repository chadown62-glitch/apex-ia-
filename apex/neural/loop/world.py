"""Monde GELÉ de la boucle autonome.

Une boucle ne peut comparer ses tentatives que si ce qui les mesure ne bouge
pas : le dataset (train / validation / held-out), le tokenizer, la taille du
modèle et les benchmarks protégés sont fixés UNE fois, écrits sur disque avec
leur empreinte, puis relus et vérifiés à chaque démarrage. Aucune expérience
ne peut les modifier : elle ne choisit que la COMPOSITION de ses données
d'entraînement parmi le split train, et sa recette d'entraînement.

Les benchmarks sont dérivés du held-out : `heldout` (tous les documents) et
une tranche par domaine (`heldout:<domaine>`), plus `validation`. Toutes les
suites sont protégées : une régression significative sur l'une d'elles
suffit à rejeter un candidat."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from apex.neural.config import ModelConfig
from apex.neural.dataset import Split, TrainingCandidate, TrainingDataset, build_dataset
from apex.neural.evaluation import Benchmark
from apex.neural.tokenizer import ByteBPETokenizer

WORLD_FORMAT = 1
PRIMARY = "heldout"


class WorldIntegrityError(RuntimeError):
    """Le monde relu ne correspond plus à son empreinte."""


@dataclass(frozen=True)
class WorldSpec:
    """Ce qui définit le monde. `domains` associe un nom de domaine au préfixe
    de `source_id` qui l'identifie (ex. « pydoc: », « file:// »)."""

    domains: dict[str, str]
    vocab_size: int = 512
    hidden_size: int = 128
    num_layers: int = 3
    num_heads: int = 4
    intermediate_size: int = 344
    context_length: int = 128
    validation_ratio: float = 0.1
    test_ratio: float = 0.15
    seed: int = 0

    def model_config(self, vocab_size: int, *, dropout: float = 0.0) -> ModelConfig:
        return ModelConfig(
            vocab_size=vocab_size,
            hidden_size=self.hidden_size,
            num_layers=self.num_layers,
            num_heads=self.num_heads,
            intermediate_size=self.intermediate_size,
            context_length=self.context_length,
            dropout=dropout,
        )


def _candidate_to_dict(c: TrainingCandidate) -> dict[str, Any]:
    return asdict(c) | {"evidence": list(c.evidence)}


def _candidate_from_dict(d: dict[str, Any]) -> TrainingCandidate:
    return TrainingCandidate(**{**d, "evidence": tuple(d.get("evidence", ()))})


class World:
    def __init__(self, spec: WorldSpec, dataset: TrainingDataset, tokenizer: ByteBPETokenizer) -> None:
        self.spec = spec
        self.dataset = dataset
        self.tokenizer = tokenizer
        for split in Split:
            for candidate in dataset.splits[split]:
                if self.domain_of(candidate.source_id) is None:
                    raise ValueError(f"source hors de tout domaine déclaré : {candidate.source_id}")
        self.benchmarks = self._benchmarks()

    # -- construction --------------------------------------------------------

    @classmethod
    def build(cls, spec: WorldSpec, candidates: Sequence[TrainingCandidate]) -> World:
        dataset = build_dataset(
            candidates,
            validation_ratio=spec.validation_ratio,
            test_ratio=spec.test_ratio,
            salt=f"apex-world-{spec.seed}",
            decontaminate=True,
        )
        tokenizer = ByteBPETokenizer.train(dataset.texts(Split.TRAIN), spec.vocab_size)
        return cls(spec, dataset, tokenizer)

    def domain_of(self, source_id: str) -> str | None:
        for name, prefix in self.spec.domains.items():
            if source_id.startswith(prefix):
                return name
        return None

    def _benchmarks(self) -> dict[str, Benchmark]:
        test = self.dataset.splits[Split.TEST]
        suites = {
            PRIMARY: Benchmark.from_dataset(PRIMARY, self.dataset, Split.TEST),
            "validation": Benchmark.from_dataset("validation", self.dataset, Split.VALIDATION),
        }
        for domain in self.spec.domains:
            docs = tuple((c.candidate_id, c.text) for c in test if self.domain_of(c.source_id) == domain)
            if len(docs) < 5:
                raise ValueError(f"domaine « {domain} » : {len(docs)} document(s) de test, 5 requis")
            suites[f"{PRIMARY}:{domain}"] = Benchmark(f"{PRIMARY}:{domain}", docs)
        return suites

    # -- données d'entraînement ---------------------------------------------

    def train_candidates(self, domain: str) -> tuple[TrainingCandidate, ...]:
        return tuple(c for c in self.dataset.splits[Split.TRAIN] if self.domain_of(c.source_id) == domain)

    def token_stream(
        self, mix: dict[str, float], total_tokens: int, *, seed: int
    ) -> tuple[list[int], list[str]]:
        """Flux d'entraînement de `total_tokens` tokens dont la part de chaque
        domaine suit `mix` (poids normalisés). Documents tirés du SEUL split
        train, dans un ordre déterministe (graine) ; renvoie aussi les
        identifiants réellement utilisés (manifeste vérifiable par le Judge)."""
        unknown = set(mix) - set(self.spec.domains)
        if unknown:
            raise ValueError(f"domaines inconnus dans le mélange : {sorted(unknown)}")
        weights = {d: w for d, w in mix.items() if w > 0}
        if not weights or any(w < 0 for w in mix.values()):
            raise ValueError("mélange vide ou poids négatif")
        total_weight = sum(weights.values())
        rng = random.Random(seed)  # noqa: S311 - échantillonnage reproductible, pas de cryptographie
        stream: list[int] = []
        used: list[str] = []
        for domain in sorted(weights):
            budget = round(total_tokens * weights[domain] / total_weight)
            docs = list(self.train_candidates(domain))
            if not docs:
                raise ValueError(f"aucun document d'entraînement pour le domaine « {domain} »")
            produced = 0
            while produced < budget:
                rng.shuffle(docs)
                for candidate in docs:
                    ids = self.tokenizer.encode(candidate.text, add_bos=True, add_eos=True)
                    stream.extend(ids)
                    used.append(candidate.candidate_id)
                    produced += len(ids)
                    if produced >= budget:
                        break
        return stream, sorted(set(used))

    # -- identité ------------------------------------------------------------

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "format": WORLD_FORMAT,
                "spec": asdict(self.spec),
                "dataset": self.dataset.fingerprint,
                "tokenizer": self.tokenizer.fingerprint(),
                "benchmarks": {name: b.fingerprint for name, b in sorted(self.benchmarks.items())},
            },
            sort_keys=True,
        )
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()

    def save(self, path: Path) -> str:
        payload = {
            "format": WORLD_FORMAT,
            "fingerprint": self.fingerprint,
            "spec": asdict(self.spec),
            "splits": {s.value: [_candidate_to_dict(c) for c in self.dataset.splits[s]] for s in Split},
            "dataset_fingerprint": self.dataset.fingerprint,
            "dropped_duplicates": list(self.dataset.dropped_duplicates),
            "decontaminated": list(self.dataset.decontaminated),
            "leakage_ratio": self.dataset.leakage_ratio,
            "manifest": self.dataset.manifest,
            "tokenizer": self.tokenizer.to_dict(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return self.fingerprint

    @classmethod
    def load(cls, path: Path, *, expected_fingerprint: str | None = None) -> World:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != WORLD_FORMAT:
            raise WorldIntegrityError(f"format de monde inconnu : {data.get('format')}")
        splits = {Split(s): tuple(_candidate_from_dict(c) for c in cs) for s, cs in data["splits"].items()}
        for candidates in splits.values():
            for candidate in candidates:
                if candidate.problems():
                    raise WorldIntegrityError(f"candidat altéré : {candidate.candidate_id}")
        dataset = TrainingDataset(
            splits,
            data["dataset_fingerprint"],
            tuple(data["dropped_duplicates"]),
            data["leakage_ratio"],
            data["manifest"],
            tuple(data["decontaminated"]),
        )
        expected_digest = hashlib.sha256(
            json.dumps(
                [[s.value, c.candidate_id, c.content_hash] for s in Split for c in splits[s]],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if dataset.fingerprint != "sha256:" + expected_digest:
            raise WorldIntegrityError("empreinte du dataset incohérente avec son contenu")
        spec = WorldSpec(**data["spec"])
        world = cls(spec, dataset, ByteBPETokenizer.from_dict(data["tokenizer"]))
        if world.fingerprint != data["fingerprint"]:
            raise WorldIntegrityError("empreinte du monde incohérente avec son contenu")
        if expected_fingerprint is not None and world.fingerprint != expected_fingerprint:
            raise WorldIntegrityError(
                f"monde {world.fingerprint} différent du monde attendu {expected_fingerprint}"
            )
        return world

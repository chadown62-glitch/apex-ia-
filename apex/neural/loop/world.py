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
    #: Capacités apprenables du monde (apex.capabilities.registry) : leurs benchmarks gelés
    #: deviennent des suites mesurées par le Judge et des cibles possibles de la boucle.
    capabilities: tuple[str, ...] = ()
    #: Domaines dont les documents vont TOUJOURS au split train (jamais tirés vers le test).
    train_only_domains: tuple[str, ...] = ()

    def identity(self) -> dict[str, Any]:
        """Ce qui entre dans l'empreinte : les champs ajoutés pour les
        capacités n'y figurent que s'ils sont utilisés (un monde antérieur
        garde exactement la même empreinte)."""
        data = asdict(self)
        for key in ("capabilities", "train_only_domains"):
            if not data[key]:
                del data[key]
        return data

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


#: Longueur des séquences de jetons de code comparées pour la décontamination.
CODE_NGRAM = 12


def _code_grams(text: str) -> set[tuple[str, ...]]:
    from apex.capabilities.code_repair.benchmark import code_tokens, ngrams

    return ngrams(code_tokens(text, normalize_names=False), CODE_NGRAM)


class World:
    def __init__(
        self,
        spec: WorldSpec,
        dataset: TrainingDataset,
        tokenizer: ByteBPETokenizer,
        *,
        capability_data: dict[str, Any] | None = None,
        capability_decontaminated: Sequence[str] = (),
    ) -> None:
        from apex.capabilities.registry import get_capability

        self.spec = spec
        self.dataset = dataset
        self.tokenizer = tokenizer
        data = capability_data or {}
        if set(data) != set(spec.capabilities):
            raise WorldIntegrityError(
                f"capacités déclarées {sorted(spec.capabilities)}, benchmarks fournis {sorted(data)}"
            )
        self.capabilities = {name: (get_capability(name), data[name]) for name in spec.capabilities}
        self.capability_decontaminated = tuple(capability_decontaminated)
        for name, (capability, bench) in self.capabilities.items():
            longest = capability.longest_input(bench, tokenizer)
            if longest > spec.context_length + 1:
                raise WorldIntegrityError(
                    f"capacité « {name} » : une entrée de {longest} jetons dépasse le contexte "
                    f"({spec.context_length}) — le modèle ne pourrait pas la lire entière"
                )
        for split in Split:
            for candidate in dataset.splits[split]:
                if self.domain_of(candidate.source_id) is None:
                    raise ValueError(f"source hors de tout domaine déclaré : {candidate.source_id}")
        self.benchmarks = self._benchmarks()

    # -- construction --------------------------------------------------------

    @classmethod
    def build(
        cls,
        spec: WorldSpec,
        candidates: Sequence[TrainingCandidate],
        *,
        capability_data: dict[str, Any] | None = None,
    ) -> World:
        """`capability_data` : benchmark gelé de chaque capacité déclarée. Tout
        fragment qui partage une séquence de code avec une solution ou une
        version boguée d'ÉVALUATION est retiré de l'entraînement (listé)."""
        from apex.capabilities.registry import get_capability

        data = capability_data or {}
        protected: set[tuple[str, ...]] = set()
        train_only: list[TrainingCandidate] = []
        for name in spec.capabilities:
            capability = get_capability(name)
            for text in capability.protected_texts(data[name]):
                protected |= _code_grams(text)
            train_only += [
                doc
                for doc in capability.train_documents(data[name])
                if any(doc.source_id.startswith(spec.domains[d]) for d in spec.train_only_domains)
            ]
        removed = [c.candidate_id for c in (*candidates, *train_only) if _code_grams(c.text) & protected]
        gone = set(removed)
        dataset = build_dataset(
            [c for c in candidates if c.candidate_id not in gone],
            validation_ratio=spec.validation_ratio,
            test_ratio=spec.test_ratio,
            salt=f"apex-world-{spec.seed}",
            decontaminate=True,
            train_only=[c for c in train_only if c.candidate_id not in gone],
        )
        tokenizer = ByteBPETokenizer.train(dataset.texts(Split.TRAIN), spec.vocab_size)
        return cls(spec, dataset, tokenizer, capability_data=data, capability_decontaminated=removed)

    # -- capacités -------------------------------------------------------------

    def capability_of_suite(self, suite: str) -> str | None:
        for name, (capability, _) in self.capabilities.items():
            if suite in capability.contract.suites:
                return name
        return None

    @property
    def suite_names(self) -> set[str]:
        """Toutes les suites mesurées : benchmarks de texte + suites de capacité."""
        names = set(self.benchmarks)
        for capability, _ in self.capabilities.values():
            names.update(capability.contract.suites)
        return names

    @property
    def acceptance_suites(self) -> frozenset[str]:
        """Suites qui peuvent fonder une ACCEPTATION quand un plan les vise
        (pré-enregistrées) : le held-out textuel et la suite d'acceptation
        déclarée par chaque capacité — jamais une tranche ou une garde."""
        return frozenset({PRIMARY, *(c.contract.acceptance_suite for c, _ in self.capabilities.values())})

    @property
    def metric_tolerances(self) -> dict[str, float]:
        return {c.contract.metric: c.contract.guard_tolerance for c, _ in self.capabilities.values()}

    def suite_domain(self, suite: str) -> str | None:
        """Domaine d'entraînement associé à une suite : la tranche held-out d'un
        domaine, ou le premier domaine d'entraînement d'une capacité."""
        if suite.startswith(f"{PRIMARY}:"):
            return suite.split(":", 1)[1]
        name = self.capability_of_suite(suite)
        if name is not None:
            return self.capabilities[name][0].contract.training_domains[0]
        return None

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
            if domain in self.spec.train_only_domains:
                continue  # aucun document de test par construction
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
                "spec": self.spec.identity(),
                "dataset": self.dataset.fingerprint,
                "tokenizer": self.tokenizer.fingerprint(),
                "benchmarks": {name: b.fingerprint for name, b in sorted(self.benchmarks.items())},
                **(
                    {
                        # Le CONTRAT (critères, tolérance, objectif, suite d'acceptation) fait partie
                        # du monde : changer les critères de succès change l'empreinte.
                        "capabilities": {
                            name: {"benchmark": cap.fingerprint(data), "contract": cap.contract.to_dict()}
                            for name, (cap, data) in sorted(self.capabilities.items())
                        }
                    }
                    if self.capabilities
                    else {}
                ),
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
            "capabilities": {name: cap.dump(data) for name, (cap, data) in self.capabilities.items()},
            "capability_decontaminated": list(self.capability_decontaminated),
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
        spec_data = dict(data["spec"])
        for key in ("capabilities", "train_only_domains"):
            if key in spec_data:
                spec_data[key] = tuple(spec_data[key])
        spec = WorldSpec(**spec_data)
        from apex.capabilities.registry import get_capability

        try:
            capability_data = {
                name: get_capability(name).load(payload)
                for name, payload in data.get("capabilities", {}).items()
            }
        except Exception as exc:  # benchmark altéré : empreinte relue différente
            raise WorldIntegrityError(f"benchmark de capacité altéré : {exc}") from exc
        world = cls(
            spec,
            dataset,
            ByteBPETokenizer.from_dict(data["tokenizer"]),
            capability_data=capability_data,
            capability_decontaminated=data.get("capability_decontaminated", ()),
        )
        if world.fingerprint != data["fingerprint"]:
            raise WorldIntegrityError("empreinte du monde incohérente avec son contenu")
        if expected_fingerprint is not None and world.fingerprint != expected_fingerprint:
            raise WorldIntegrityError(
                f"monde {world.fingerprint} différent du monde attendu {expected_fingerprint}"
            )
        return world

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
from apex.neural.dataset import (
    Split,
    TrainingCandidate,
    TrainingDataset,
    build_dataset,
    normalized_text,
    splits_fingerprint,
    text_ngrams,
    verify_no_leakage,
)
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
    #: Empreinte du monde PARENT : l'enfant hérite de son tokenizer (jamais réentraîné) et de
    #: l'architecture, pour que les capacités s'accumulent dans un même cerveau.
    parent: str = ""

    def identity(self) -> dict[str, Any]:
        """Ce qui entre dans l'empreinte : les champs ajoutés pour les
        capacités n'y figurent que s'ils sont utilisés (un monde antérieur
        garde exactement la même empreinte)."""
        data = asdict(self)
        for key in ("capabilities", "train_only_domains", "parent"):
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


def _squashed(text: str) -> str:
    return "".join(text.split())


class _Protection:
    """Textes d'évaluation protégés, reconnus de deux façons : séquences de jetons de code
    partagées (copie reformatée ou partielle d'une solution), et présence TELLE QUELLE aux
    espaces près (texte cité dans un commentaire, une docstring ou de la prose, que la
    tokenisation de code ignore)."""

    def __init__(self) -> None:
        self.grams: set[tuple[str, ...]] = set()
        self.verbatim: set[str] = set()

    def add(self, texts: Sequence[str]) -> None:
        for text in texts:
            self.grams |= _code_grams(text)
            self.verbatim.add(_squashed(text))

    def hits(self, text: str) -> bool:
        if _code_grams(text) & self.grams:
            return True
        squashed = _squashed(text)
        return any(p in squashed for p in self.verbatim if len(p) <= len(squashed))


#: Recouvrement maximal (8-grammes) d'un nouveau document avec l'entraînement de la lignée.
LINEAGE_MAX_OVERLAP = 0.1

#: Champs de la spécification qui fixent la forme des poids : identiques entre parent et enfant.
_ARCHITECTURE = (
    "vocab_size",
    "hidden_size",
    "num_layers",
    "num_heads",
    "intermediate_size",
    "context_length",
)


def _check_lineage(spec: WorldSpec, parent: World) -> None:
    problems = []
    if spec.parent != parent.fingerprint:
        problems.append(f"parent déclaré {spec.parent or '(aucun)'}, parent fourni {parent.fingerprint}")
    for name in _ARCHITECTURE:
        if getattr(spec, name) != getattr(parent.spec, name):
            problems.append(f"architecture : {name} {getattr(parent.spec, name)} -> {getattr(spec, name)}")
    for domain, prefix in parent.spec.domains.items():
        if spec.domains.get(domain) != prefix:
            problems.append(f"domaine du parent « {domain} » absent ou redéfini")
    missing = set(parent.spec.capabilities) - set(spec.capabilities)
    if missing:
        problems.append(f"capacités du parent abandonnées : {sorted(missing)}")
    if problems:
        raise WorldIntegrityError("filiation refusée : " + " ; ".join(problems))


def _extend_dataset(
    parent: TrainingDataset,
    candidates: Sequence[TrainingCandidate],
    train_only: Sequence[TrainingCandidate],
    spec: WorldSpec,
) -> TrainingDataset:
    """Splits du parent inchangés + nouveaux documents répartis entre eux ; contrôle de fuite
    refait sur l'ensemble (un nouveau document d'entraînement ne peut pas recouvrir un test
    du parent, et réciproquement)."""
    parent_docs = [c for split in Split for c in parent.splits[split]]
    parent_sources = {c.source_id for c in parent_docs}
    clash = [c.candidate_id for c in (*candidates, *train_only) if c.source_id in parent_sources]
    if clash:
        raise WorldIntegrityError(
            f"nouveaux documents issus de sources déjà présentes chez le parent : {clash[:5]}"
        )
    known = {normalized_text(c.text) for c in parent_docs}
    dropped = [c.candidate_id for c in (*candidates, *train_only) if normalized_text(c.text) in known]
    # Un nouveau document qui recouvre un document de la lignée est retiré (listé) : côté
    # entraînement, le champion adopté l'a déjà vu (il ne peut servir de test) ; côté test, il
    # ferait fuir ce test dans l'entraînement. Les splits du parent, eux, ne bougent jamais.
    seen: set[tuple[str, ...]] = set()
    for c in parent_docs:
        seen |= text_ngrams(c.text)

    def overlaps(c: TrainingCandidate) -> bool:
        grams = text_ngrams(c.text)
        return bool(grams) and len(grams & seen) / len(grams) > LINEAGE_MAX_OVERLAP

    overlapping = [c.candidate_id for c in candidates if c.candidate_id not in dropped and overlaps(c)]
    excluded = set(dropped) | set(overlapping)
    fresh = [c for c in candidates if c.candidate_id not in excluded]
    fresh_train_only = [c for c in train_only if c.candidate_id not in excluded]
    if fresh:
        added = build_dataset(
            fresh,
            validation_ratio=spec.validation_ratio,
            test_ratio=spec.test_ratio,
            salt=f"apex-world-{spec.seed}",
            decontaminate=True,
            train_only=fresh_train_only,
        )
        added_splits, removed = added.splits, [*overlapping, *added.decontaminated]
        dropped += list(added.dropped_duplicates)
    else:
        unique: dict[str, TrainingCandidate] = {}
        for c in fresh_train_only:
            unique.setdefault(normalized_text(c.text), c)
        added_splits = {Split.TRAIN: tuple(unique.values()), Split.VALIDATION: (), Split.TEST: ()}
        removed = overlapping
    splits = {split: (*parent.splits[split], *added_splits[split]) for split in Split}
    manifest = {split.value: sorted({c.source_id for c in splits[split]}) for split in Split}
    merged = TrainingDataset(
        splits,
        splits_fingerprint(splits),
        (*parent.dropped_duplicates, *dropped),
        0.0,
        manifest,
        (*parent.decontaminated, *removed),
    )
    ratio = verify_no_leakage(merged)
    return TrainingDataset(
        splits, merged.fingerprint, merged.dropped_duplicates, ratio, manifest, merged.decontaminated
    )


class World:
    def __init__(
        self,
        spec: WorldSpec,
        dataset: TrainingDataset,
        tokenizer: ByteBPETokenizer,
        *,
        capability_data: dict[str, Any] | None = None,
        capability_decontaminated: Sequence[str] = (),
        ancestor_datasets: Sequence[str] = (),
    ) -> None:
        from apex.capabilities.registry import get_capability

        if bool(spec.parent) != bool(ancestor_datasets):
            raise WorldIntegrityError("un monde enfant déclare son parent ET les datasets de ses ancêtres")
        self.spec = spec
        self.ancestor_datasets = tuple(ancestor_datasets)
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
        parent: World | None = None,
    ) -> World:
        """`capability_data` : benchmark gelé de chaque capacité déclarée. Tout
        fragment qui partage une séquence de code avec une solution ou une
        version boguée d'ÉVALUATION est retiré de l'entraînement (listé).

        Monde ENFANT (`parent`) : `candidates` ne contient que les NOUVEAUX documents. Les splits
        du parent sont repris tels quels (un document vu par le champion adopté ne change jamais
        de split) ; seuls les nouveaux sont tirés au sort. Les capacités héritées gardent le
        benchmark du parent. Refusé si un document d'entraînement du parent contient une séquence
        d'évaluation d'une NOUVELLE capacité : le champion adopté l'a déjà vue."""
        from apex.capabilities.registry import get_capability

        given = dict(capability_data or {})
        if parent is None:
            if spec.parent:
                raise WorldIntegrityError(f"monde enfant de {spec.parent} : le parent doit être fourni")
            data, inherited = given, set()
        else:
            _check_lineage(spec, parent)
            inherited = set(parent.spec.capabilities)
            data = {name: bench for name, (_, bench) in parent.capabilities.items()}
            for name, bench in given.items():
                capability = get_capability(name)
                if name in inherited and capability.fingerprint(bench) != capability.fingerprint(
                    data[name]
                ):
                    raise WorldIntegrityError(
                        f"capacité héritée « {name} » : benchmark différent du parent"
                    )
                data.setdefault(name, bench)
        missing = set(spec.capabilities) - set(data)
        if missing:
            raise WorldIntegrityError(f"benchmark absent pour les capacités {sorted(missing)}")
        protected = _Protection()
        protected_new = _Protection()
        train_only: list[TrainingCandidate] = []
        for name in spec.capabilities:
            capability = get_capability(name)
            texts = capability.protected_texts(data[name])
            protected.add(texts)
            if name in inherited:
                continue  # ses documents « train seulement » sont déjà dans les splits du parent
            protected_new.add(texts)
            train_only += [
                doc
                for doc in capability.train_documents(data[name])
                if any(doc.source_id.startswith(spec.domains[d]) for d in spec.train_only_domains)
            ]
        removed = [c.candidate_id for c in (*candidates, *train_only) if protected.hits(c.text)]
        gone = set(removed)
        kept = [c for c in candidates if c.candidate_id not in gone]
        kept_train_only = [c for c in train_only if c.candidate_id not in gone]
        if parent is None:
            dataset = build_dataset(
                kept,
                validation_ratio=spec.validation_ratio,
                test_ratio=spec.test_ratio,
                salt=f"apex-world-{spec.seed}",
                decontaminate=True,
                train_only=kept_train_only,
            )
            tokenizer = ByteBPETokenizer.train(dataset.texts(Split.TRAIN), spec.vocab_size)
            ancestors: tuple[str, ...] = ()
        else:
            seen_by_champion = [
                c.candidate_id
                for split in (Split.TRAIN, Split.VALIDATION)
                for c in parent.dataset.splits[split]
                if protected_new.hits(c.text)
            ]
            if seen_by_champion:
                raise WorldIntegrityError(
                    "filiation refusée : des documents d'entraînement du parent contiennent une "
                    f"évaluation de la nouvelle capacité : {seen_by_champion[:5]}"
                )
            dataset = _extend_dataset(parent.dataset, kept, kept_train_only, spec)
            tokenizer = parent.tokenizer  # hérité, jamais réentraîné : les poids restent lisibles
            ancestors = (parent.dataset.fingerprint, *parent.ancestor_datasets)
        return cls(
            spec,
            dataset,
            tokenizer,
            capability_data=data,
            capability_decontaminated=removed,
            ancestor_datasets=ancestors,
        )

    def accepts_dataset(self, dataset_fingerprint: str) -> bool:
        """Un checkpoint est lisible dans ce monde s'il a été entraîné sur son dataset ou sur celui
        d'un ancêtre (même tokenizer, même architecture : garanti à la construction)."""
        return (
            dataset_fingerprint == self.dataset.fingerprint or dataset_fingerprint in self.ancestor_datasets
        )

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
                **({"ancestor_datasets": list(self.ancestor_datasets)} if self.ancestor_datasets else {}),
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
            "ancestor_datasets": list(self.ancestor_datasets),
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
            ancestor_datasets=data.get("ancestor_datasets", ()),
        )
        if world.fingerprint != data["fingerprint"]:
            raise WorldIntegrityError("empreinte du monde incohérente avec son contenu")
        if expected_fingerprint is not None and world.fingerprint != expected_fingerprint:
            raise WorldIntegrityError(
                f"monde {world.fingerprint} différent du monde attendu {expected_fingerprint}"
            )
        return world

"""Dataset d'entraînement : TRAINING CANDIDATE -> TRAINING DATASET
(train / validation / held-out test).

Garanties, vérifiées par tests :
- un candidat sans provenance complète, sans licence compatible ou de
  qualité insuffisante est REFUSÉ (exception), jamais ignoré en silence ;
- les textes identiques sont dédupliqués AVANT le découpage ;
- le découpage se fait par SOURCE : tous les fragments d'un même document
  vont dans le même split (pas de fuite par fragments voisins) ;
- le held-out test ne partage pas de n-grammes significatifs avec le train
  (sinon `LeakageError`) ;
- le jeu est identifié par une empreinte stable.

Ce module n'importe pas PyTorch : il est utilisable par le Knowledge Agent."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from apex.knowledge.filters import canonical

ALLOWED_LICENSES = frozenset({"free", "compatible"})
ALLOWED_QUALITY = frozenset({"high_value", "medium_value"})
_WORD = re.compile(r"\w+", re.UNICODE)


class Split(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


class ProvenanceError(ValueError):
    """Un candidat ne peut pas entrer dans un dataset."""


class LeakageError(ValueError):
    """Le held-out test recouvre les données d'entraînement."""


@dataclass(frozen=True)
class TrainingCandidate:
    candidate_id: str
    text: str
    source_id: str
    content_hash: str
    license_status: str
    quality: str
    origin: str
    evidence: tuple[str, ...] = ()

    def problems(self) -> tuple[str, ...]:
        problems = []
        if not self.text.strip():
            problems.append("texte vide")
        if not self.source_id or not self.content_hash or not self.origin:
            problems.append("provenance incomplète (source, empreinte ou origine absente)")
        if (
            self.content_hash
            and self.content_hash != "sha256:" + hashlib.sha256(self.text.encode()).hexdigest()
        ):
            problems.append("empreinte de contenu incohérente avec le texte")
        if self.license_status not in ALLOWED_LICENSES:
            problems.append(f"licence « {self.license_status} » non utilisable pour l'entraînement")
        if self.quality not in ALLOWED_QUALITY:
            problems.append(f"qualité « {self.quality} » insuffisante")
        return tuple(problems)


def _normalized(text: str) -> str:
    """Clé de comparaison (doublons, n-grammes de fuite) calculée sur la vue
    canonique du Knowledge Agent : un caractère invisible ou pleine chasse ne
    fait pas d'une copie une donnée nouvelle."""
    return " ".join(w.lower() for w in _WORD.findall(canonical(text)))


def _ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    tokens = _normalized(text).split()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def overlap_ratio(test_texts: Iterable[str], train_texts: Iterable[str], n: int = 8) -> float:
    train = set().union(*(_ngrams(t, n) for t in train_texts)) if train_texts else set()
    test = set().union(*(_ngrams(t, n) for t in test_texts)) if test_texts else set()
    return len(test & train) / len(test) if test else 0.0


@dataclass(frozen=True)
class TrainingDataset:
    splits: dict[Split, tuple[TrainingCandidate, ...]]
    fingerprint: str
    dropped_duplicates: tuple[str, ...] = ()
    leakage_ratio: float = 0.0
    manifest: dict[str, list[str]] = field(default_factory=dict)
    decontaminated: tuple[str, ...] = ()

    def texts(self, split: Split) -> list[str]:
        return [c.text for c in self.splits[split]]

    def provenance(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "sources": self.manifest,
            "candidates": {s.value: [c.candidate_id for c in cs] for s, cs in self.splits.items()},
        }


def verify_no_leakage(dataset: TrainingDataset, *, max_overlap: float = 0.1, n: int = 8) -> float:
    """Contrôle PAR DOCUMENT de test : un ratio global serait dilué par les
    documents sains et masquerait quelques fuites complètes. Renvoie le pire
    recouvrement observé."""
    test_sources = {c.source_id for c in dataset.splits[Split.TEST]}
    shared = test_sources & {
        c.source_id for s in (Split.TRAIN, Split.VALIDATION) for c in dataset.splits[s]
    }
    if shared:
        raise LeakageError(
            f"sources présentes à la fois dans le test et l'entraînement : {sorted(shared)[:5]}"
        )
    train_side = dataset.texts(Split.TRAIN) + dataset.texts(Split.VALIDATION)
    train_ngrams: set[tuple[str, ...]] = set().union(*(_ngrams(t, n) for t in train_side))
    worst, worst_id = 0.0, ""
    for test_candidate in dataset.splits[Split.TEST]:
        grams = _ngrams(test_candidate.text, n)
        ratio = len(grams & train_ngrams) / len(grams) if grams else 0.0
        if ratio > worst:
            worst, worst_id = ratio, test_candidate.candidate_id
    if worst > max_overlap:
        raise LeakageError(
            f"{worst:.0%} des {n}-grammes du test « {worst_id} » figurent dans l'entraînement "
            f"(max {max_overlap:.0%})"
        )
    return worst


def build_dataset(
    candidates: Sequence[TrainingCandidate],
    *,
    validation_ratio: float = 0.1,
    test_ratio: float = 0.1,
    salt: str = "apex",
    max_overlap: float = 0.1,
    decontaminate: bool = False,
    train_only: Sequence[TrainingCandidate] = (),
) -> TrainingDataset:
    """`decontaminate=False` (défaut) : toute fuite vers le held-out lève
    `LeakageError`. `decontaminate=True` : les fragments train/validation qui
    partagent des n-grammes avec un document de test fuyant sont RETIRÉS (le
    test reste intact, donc représentatif) et listés dans `decontaminated` ;
    le contrôle de fuite est ensuite refait et reste bloquant."""
    for candidate in (*candidates, *train_only):
        problems = candidate.problems()
        if problems:
            raise ProvenanceError(f"{candidate.candidate_id} refusé : {'; '.join(problems)}")
    seen: dict[str, str] = {}
    unique: list[TrainingCandidate] = []
    dropped: list[str] = []
    for candidate in candidates:
        key = _normalized(candidate.text)
        if key in seen:
            dropped.append(candidate.candidate_id)
            continue
        seen[key] = candidate.candidate_id
        unique.append(candidate)

    sources = sorted(
        {c.source_id for c in unique}, key=lambda s: hashlib.sha256(f"{salt}:{s}".encode()).hexdigest()
    )
    n_test = math.ceil(len(sources) * test_ratio) if test_ratio > 0 else 0
    n_val = math.ceil(len(sources) * validation_ratio) if validation_ratio > 0 else 0
    if len(sources) < n_test + n_val + 1:
        raise ProvenanceError(
            f"{len(sources)} source(s) : trop peu pour train + validation + test disjoints"
        )
    assignment = dict.fromkeys(sources[:n_test], Split.TEST)
    assignment.update(dict.fromkeys(sources[n_test : n_test + n_val], Split.VALIDATION))
    assignment.update(dict.fromkeys(sources[n_test + n_val :], Split.TRAIN))
    splits = {split: tuple(c for c in unique if assignment[c.source_id] is split) for split in Split}
    # Documents réservés à l'entraînement (ex. solutions des tâches TRAIN d'une capacité) :
    # jamais tirés au sort vers validation/test ; dédoublonnés et contrôlés comme les autres.
    forced: list[TrainingCandidate] = []
    for candidate in train_only:
        if candidate.source_id in assignment:
            raise ProvenanceError(
                f"{candidate.source_id} : source à la fois tirée au sort et réservée au train"
            )
        key = _normalized(candidate.text)
        if key in seen:
            dropped.append(candidate.candidate_id)
            continue
        seen[key] = candidate.candidate_id
        forced.append(candidate)
    splits[Split.TRAIN] = (*splits[Split.TRAIN], *forced)
    removed: list[str] = []
    if decontaminate:
        train_side = [c for s in (Split.TRAIN, Split.VALIDATION) for c in splits[s]]
        grams = {c.candidate_id: _ngrams(c.text, 8) for c in train_side}
        union: set[tuple[str, ...]] = set().union(*grams.values())
        for test_candidate in splits[Split.TEST]:
            test_grams = _ngrams(test_candidate.text, 8)
            shared = test_grams & union
            if test_grams and len(shared) / len(test_grams) > max_overlap:
                removed.extend(cid for cid, g in grams.items() if g & shared and cid not in removed)
        if removed:
            gone = set(removed)
            splits = {
                split: tuple(c for c in cs if c.candidate_id not in gone) if split is not Split.TEST else cs
                for split, cs in splits.items()
            }
    digest = hashlib.sha256(
        json.dumps(
            [[split.value, c.candidate_id, c.content_hash] for split in Split for c in splits[split]],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    manifest = {split.value: sorted({c.source_id for c in splits[split]}) for split in Split}
    dataset = TrainingDataset(splits, "sha256:" + digest, tuple(dropped), 0.0, manifest, tuple(removed))
    ratio = verify_no_leakage(dataset, max_overlap=max_overlap)
    return TrainingDataset(splits, dataset.fingerprint, tuple(dropped), ratio, manifest, tuple(removed))

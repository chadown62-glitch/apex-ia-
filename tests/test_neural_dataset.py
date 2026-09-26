"""TRAINING CANDIDATE -> DATASET : provenance, licences, dédoublonnage,
découpage par source et étanchéité du held-out test."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

import pytest

from apex.neural.dataset import (
    LeakageError,
    ProvenanceError,
    Split,
    TrainingCandidate,
    build_dataset,
    overlap_ratio,
    verify_no_leakage,
)

_TOPICS = (
    "rivers",
    "stars",
    "bread",
    "engines",
    "forests",
    "glaciers",
    "violins",
    "harbors",
    "orchids",
    "comets",
)


def _text(source: int, part: int) -> str:
    topic = _TOPICS[source % len(_TOPICS)]
    return f"Document {source} part {part} about {topic}: " + " ".join(
        f"{topic}{source}_{part}_{k}" for k in range(20)
    )


def candidate(source: int, part: int = 0, **overrides: Any) -> TrainingCandidate:
    text = str(overrides.pop("text", _text(source, part)))
    base = TrainingCandidate(
        candidate_id=f"c{source}-{part}",
        text=text,
        source_id=f"src-{source}",
        content_hash="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        license_status="compatible",
        quality="high_value",
        origin="knowledge:documentation",
    )
    return replace(base, **overrides)


def corpus(n_sources: int = 20, parts: int = 3) -> list[TrainingCandidate]:
    return [candidate(s, p) for s in range(n_sources) for p in range(parts)]


def test_splits_are_by_source_and_cover_everything() -> None:
    dataset = build_dataset(corpus())
    sources = {split: {c.source_id for c in dataset.splits[split]} for split in Split}
    assert sources[Split.TRAIN] and sources[Split.VALIDATION] and sources[Split.TEST]
    assert not sources[Split.TRAIN] & sources[Split.TEST]
    assert not sources[Split.TRAIN] & sources[Split.VALIDATION]
    assert not sources[Split.VALIDATION] & sources[Split.TEST]
    assert sum(len(cs) for cs in dataset.splits.values()) == 60
    # Tous les fragments d'une source restent ensemble.
    for split in Split:
        for source in sources[split]:
            assert sum(c.source_id == source for c in dataset.splits[split]) == 3


def test_split_is_deterministic_and_fingerprinted() -> None:
    a, b = build_dataset(corpus()), build_dataset(list(reversed(corpus())))
    assert a.manifest == b.manifest
    c = build_dataset(corpus(), salt="other")
    assert c.manifest != a.manifest
    assert a.fingerprint.startswith("sha256:")
    d = build_dataset([*corpus(), candidate(99)])
    assert d.fingerprint != a.fingerprint


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"source_id": ""}, "provenance incomplète"),
        ({"origin": ""}, "provenance incomplète"),
        ({"content_hash": ""}, "provenance incomplète"),
        ({"content_hash": "sha256:" + "0" * 64}, "empreinte de contenu incohérente"),
        ({"license_status": "unknown"}, "licence"),
        ({"license_status": "restricted"}, "licence"),
        ({"quality": "low_value"}, "qualité"),
        ({"quality": "rejected"}, "qualité"),
        ({"quality": "unsafe"}, "qualité"),
        ({"quality": "unverified"}, "qualité"),
    ],
)
def test_invalid_candidate_refuses_the_whole_build(override: dict[str, str], message: str) -> None:
    bad = candidate(50, 0, **override)
    with pytest.raises(ProvenanceError, match=message):
        build_dataset([*corpus(), bad])


def test_duplicates_do_not_inflate_the_dataset() -> None:
    items = corpus()
    copies = [candidate(s, 0, candidate_id=f"copy{s}", source_id=f"mirror-{s}") for s in range(10)]
    dataset = build_dataset([*items, *copies])
    assert sum(len(cs) for cs in dataset.splits.values()) == 60
    assert sorted(dataset.dropped_duplicates) == sorted(f"copy{s}" for s in range(10))
    # Normalisation : casse et ponctuation ne suffisent pas à contourner.
    shouty = candidate(0, 0, candidate_id="shout", source_id="shout", text=_text(0, 0).upper() + "!!!")
    assert "shout" in build_dataset([*items, shouty]).dropped_duplicates


def test_leakage_between_test_and_train_is_detected() -> None:
    dataset = build_dataset(corpus())
    leaked_text = dataset.splits[Split.TEST][0].text
    leak = candidate(
        0, 0, candidate_id="leak", text=leaked_text + " extra words here", source_id="src-leak"
    )
    tainted = replace(dataset, splits={**dataset.splits, Split.TRAIN: (*dataset.splits[Split.TRAIN], leak)})
    with pytest.raises(LeakageError, match="8-grammes"):
        verify_no_leakage(tainted)
    shared_source = replace(dataset.splits[Split.TEST][0], candidate_id="same-source")
    tainted = replace(
        dataset, splits={**dataset.splits, Split.TRAIN: (*dataset.splits[Split.TRAIN], shared_source)}
    )
    with pytest.raises(LeakageError, match="sources présentes"):
        verify_no_leakage(tainted)


def test_build_refuses_corpus_that_leaks_across_sources() -> None:
    # Le même contenu republié par 20 sources différentes, avec une variation
    # qui défait le dédoublonnage exact : quel que soit le découpage, les
    # documents de test partagent leurs n-grammes avec le train.
    base = " ".join(f"shared{k}" for k in range(40))
    mirrored = [candidate(100 + i, text=f"{base} variant {i}", source_id=f"mirror-{i}") for i in range(20)]
    with pytest.raises(LeakageError, match="8-grammes du test"):
        build_dataset(mirrored)


def test_a_few_leaked_documents_are_not_diluted_by_healthy_ones() -> None:
    # Régression : un ratio GLOBAL passait sous le seuil quand quelques
    # documents de test fuyaient intégralement parmi beaucoup de sains.
    dataset = build_dataset(corpus(40, 3))
    leaked = dataset.splits[Split.TEST][0]
    copy = replace(leaked, candidate_id="copy", source_id="elsewhere", text=leaked.text + " tail")
    tainted = replace(dataset, splits={**dataset.splits, Split.TRAIN: (*dataset.splits[Split.TRAIN], copy)})
    healthy_share = overlap_ratio(tainted.texts(Split.TEST), tainted.texts(Split.TRAIN))
    assert healthy_share < 0.1  # le ratio global ne l'aurait pas vu
    with pytest.raises(LeakageError, match=leaked.candidate_id):
        verify_no_leakage(tainted)


def test_overlap_ratio_values() -> None:
    a = " ".join(f"w{i}" for i in range(20))
    assert overlap_ratio([a], [a]) == 1.0
    assert overlap_ratio([a], ["unrelated " * 20]) == 0.0
    assert overlap_ratio([], [a]) == 0.0


def test_too_few_sources_is_an_error_not_a_silent_empty_split() -> None:
    with pytest.raises(ProvenanceError, match="trop peu"):
        build_dataset([candidate(0), candidate(1)])


def test_decontamination_removes_contaminating_train_fragments_and_keeps_test_intact() -> None:
    items = corpus(20, 3)
    strict = build_dataset(items)
    victim = strict.splits[Split.TEST][0]
    # Une autre source (côté train) recopie le document de test : cas réel
    # observé (docstrings de gzip.open et lzma.open quasi identiques).
    copier = candidate(0, 0, candidate_id="copier", source_id="src-copier", text=victim.text + " copied")
    source_split = {
        s: {c.source_id for c in cs}
        for s, cs in build_dataset([*items, copier], max_overlap=1.01, decontaminate=False).splits.items()
    }
    assert "src-copier" not in source_split[Split.TEST]
    with pytest.raises(LeakageError):
        build_dataset([*items, copier])
    cleaned = build_dataset([*items, copier], decontaminate=True)
    assert cleaned.decontaminated == ("copier",)
    assert [c.candidate_id for c in cleaned.splits[Split.TEST]] == [
        c.candidate_id for c in build_dataset([*items, copier], max_overlap=1.01).splits[Split.TEST]
    ]
    assert all(c.candidate_id != "copier" for s in Split for c in cleaned.splits[s])
    assert cleaned.leakage_ratio <= 0.1
    assert build_dataset(items, decontaminate=True).decontaminated == ()


# Cycle CI 1 (native_learning) : copies maquillées ------------------------------


def test_invisible_or_fullwidth_copies_do_not_inflate_the_dataset() -> None:
    items = corpus()
    original = items[0].text
    zero_width = " ".join(w[:2] + chr(0x200B) + w[2:] if len(w) > 4 else w for w in original.split(" "))
    fullwidth = "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in original)
    copies = [
        candidate(0, 0, candidate_id="zw", source_id="mirror-zw", text=zero_width),
        candidate(0, 0, candidate_id="fw", source_id="mirror-fw", text=fullwidth),
    ]
    dataset = build_dataset([*items, *copies])
    assert set(dataset.dropped_duplicates) == {"zw", "fw"}

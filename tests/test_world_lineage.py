"""F1 — cerveau continu : un monde ENFANT hérite du tokenizer, de l'architecture, des splits et
des capacités de son parent, et adopte le champion ATTESTÉ du parent, re-mesuré dans le nouveau
monde. Monde réduit réel (sources de la stdlib, benchmark `code_repair` construit par exécution),
champion réel produit par la boucle, entraînement réel dans le monde enfant."""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.capabilities.code_repair.benchmark import suite_name
from apex.capabilities.registry import sandbox_provider
from apex.neural.dataset import Split, TrainingCandidate
from apex.neural.loop.controller import LoopController, LoopLimits
from apex.neural.loop.execute import initial_model, load_model_from_artifact
from apex.neural.loop.strategy import StrategyPolicy
from apex.neural.loop.world import World, WorldIntegrityError, WorldSpec
from tests import capability_support as support

try:
    sandbox_provider()
    SANDBOX = True
except Exception:  # pragma: no cover - dépend de l'hôte
    SANDBOX = False
pytestmark = pytest.mark.skipif(not SANDBOX, reason="aucun sandbox NAMESPACED sur cet hôte")

NEW_DOMAIN = {"extra": "extra:"}


def _doc(name: str, text: str) -> TrainingCandidate:
    return TrainingCandidate(
        candidate_id=f"extra:{name}#0",
        text=text,
        source_id=f"extra:{name}",
        content_hash="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        license_status="free",
        quality="high_value",
        origin="test:lineage",
    )


def new_documents() -> list[TrainingCandidate]:
    """Documents RÉELS absents du parent : docstrings de modules de la stdlib non utilisés."""
    import ast
    import dataclasses
    import enum
    import random
    import zlib

    docs = []
    for module in (ast, dataclasses, enum, random, zlib):
        for name, obj in inspect.getmembers(module):
            doc = inspect.getdoc(obj)
            if doc and len(doc) > 200 and not name.startswith("_"):
                docs.append(_doc(f"{module.__name__}.{name}", doc))
            if len(docs) >= 40:
                break
    return docs


def child_spec(parent: World, **changes: object) -> WorldSpec:
    return replace(
        parent.spec, parent=parent.fingerprint, domains={**parent.spec.domains, **NEW_DOMAIN}, **changes
    )


@pytest.fixture(scope="module")
def parent_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, World]:
    """Monde parent réel et un champion ATTESTÉ produit par la boucle (amorçage)."""
    workdir = tmp_path_factory.mktemp("parent")
    world = support.world()
    world.save(workdir / "world.json")
    ctrl = LoopController(
        workdir,
        world,
        limits=LoopLimits(
            max_cycles=1,
            max_experiments_per_cycle=1,
            max_total_experiments=1,
            max_steps_per_experiment=120,
            max_tokens_per_experiment=30_000,
            checkpoint_every=40,
            experiment_timeout_s=300,
        ),
        bootstrap=support.recipe(steps=120, mix={"code": 1.0}),
        log=lambda _m: None,
    )
    ctrl.run()
    champion = ctrl.registry.current()
    assert champion is not None and ctrl.attest(champion) is None
    return workdir, world


@pytest.fixture(scope="module")
def child(parent_run: tuple[Path, World]) -> World:
    _, parent = parent_run
    return World.build(child_spec(parent), new_documents(), parent=parent)


def test_child_keeps_the_parent_splits_tokenizer_and_capabilities(
    parent_run: tuple[Path, World], child: World, tmp_path: Path
) -> None:
    _, parent = parent_run
    assert child.tokenizer.fingerprint() == parent.tokenizer.fingerprint()  # jamais réentraîné
    for split in Split:
        parent_ids = [c.candidate_id for c in parent.dataset.splits[split]]
        child_ids = [c.candidate_id for c in child.dataset.splits[split]]
        assert child_ids[: len(parent_ids)] == parent_ids  # aucun document du parent ne change de split
    added = {c.candidate_id for s in Split for c in child.dataset.splits[s]} - {
        c.candidate_id for s in Split for c in parent.dataset.splits[s]
    }
    assert added and all(i.startswith("extra:") for i in added)
    assert child.capabilities["code_repair"][0].fingerprint(child.capabilities["code_repair"][1]) == (
        parent.capabilities["code_repair"][0].fingerprint(parent.capabilities["code_repair"][1])
    )
    assert child.ancestor_datasets == (parent.dataset.fingerprint,)
    assert child.accepts_dataset(parent.dataset.fingerprint) and child.fingerprint != parent.fingerprint
    child.save(tmp_path / "world.json")
    assert World.load(tmp_path / "world.json").fingerprint == child.fingerprint


def test_lineage_is_refused_when_it_would_break_the_adopted_weights(
    parent_run: tuple[Path, World],
) -> None:
    _, parent = parent_run
    docs = new_documents()
    with pytest.raises(WorldIntegrityError, match="architecture"):
        World.build(child_spec(parent, hidden_size=48), docs, parent=parent)
    with pytest.raises(WorldIntegrityError, match="parent déclaré"):
        World.build(replace(child_spec(parent), parent="sha256:" + "0" * 64), docs, parent=parent)
    with pytest.raises(WorldIntegrityError, match="abandonnées"):
        World.build(child_spec(parent, capabilities=()), docs, parent=parent)
    with pytest.raises(WorldIntegrityError, match="le parent doit être fourni"):
        World.build(child_spec(parent), docs)
    stolen = replace(docs[0], source_id=parent.dataset.splits[Split.TEST][0].source_id)
    with pytest.raises(WorldIntegrityError, match="sources déjà présentes"):
        World.build(child_spec(parent), [stolen, *docs[1:]], parent=parent)


def test_a_new_capability_already_seen_by_the_champion_is_refused() -> None:
    """Parent SANS capacité dont un document d'entraînement contient une solution d'évaluation
    de `code_repair` : l'enfant qui ajoute `code_repair` est refusé (le champion l'a vue)."""
    bench = support.benchmark()
    eval_source = next(r["source"] for r in bench.references.values() if r["split"] == "heldout")
    text = "# note\n" + eval_source
    leak = replace(
        _doc("leak", text),
        candidate_id="leak#0",
        source_id=support.SPEC.domains["code"] + "zz_leaked_copy.py",
    )
    parent_domains = {d: p for d, p in support.SPEC.domains.items() if d != "code_tasks"}
    parent_spec = replace(
        support.SPEC,
        domains=parent_domains,
        capabilities=(),
        train_only_domains=(),
    )
    base = World.build(parent_spec, (*support.candidates(), leak))
    # le document fuyant doit être côté entraînement chez le parent pour que le test ait un sens
    if not any(c.candidate_id == leak.candidate_id for c in base.dataset.splits[Split.TRAIN]):
        pytest.skip("le tirage a placé le document de fuite hors entraînement")
    spec = replace(
        parent_spec,
        domains={**parent_spec.domains, "code_tasks": support.SPEC.domains["code_tasks"]},
        parent=base.fingerprint,
        capabilities=("code_repair",),
        train_only_domains=("code_tasks",),
    )
    with pytest.raises(WorldIntegrityError, match="évaluation de la nouvelle capacité"):
        World.build(spec, [], capability_data={"code_repair": bench}, parent=base)


def test_adopted_champion_is_attested_remeasured_and_trainable(
    parent_run: tuple[Path, World], child: World, tmp_path: Path
) -> None:
    parent_dir, parent = parent_run
    parent_champion = LoopController(parent_dir, parent, log=lambda _m: None).registry.current()
    assert parent_champion is not None
    workdir = tmp_path / "child"
    child.save(workdir / "world.json")
    ctrl = LoopController(
        workdir,
        child,
        limits=LoopLimits(
            max_cycles=1,
            max_experiments_per_cycle=1,
            max_total_experiments=1,
            max_steps_per_experiment=60,
            max_tokens_per_experiment=20_000,
            checkpoint_every=30,
            experiment_timeout_s=300,
        ),
        policy=StrategyPolicy(finetune_steps=40, finetune_lr=1e-3, finetune_tokens=8_000, max_steps=60),
        log=lambda _m: None,
    )
    entry = ctrl.adopt(parent_dir, reason="test de continuité")
    assert entry["kind"] == "adoption" and entry["artifact"] == parent_champion["artifact"]
    assert ctrl.attest(entry) is None
    # Re-mesuré dans le monde enfant ; le benchmark de capacité est le même : valeurs identiques.
    for suite in (suite_name("heldout"), suite_name("transfer")):
        assert entry["reports"][suite]["scores"] == parent_champion["reports"][suite]["scores"]
    assert set(entry["reports"]) == child.suite_names  # toutes les suites de l'enfant, pas du parent
    with pytest.raises(ValueError, match="pas vide"):
        ctrl.adopt(parent_dir, reason="deuxième fois")
    # La boucle de l'enfant part du champion adopté : une expérience réelle, jugée avec rejeu.
    summary = ctrl.run()
    record = next(iter(ctrl.journal.experiments().values()))
    assert record.finished and record.plan["baseline"]["artifact"] == parent_champion["artifact"]
    assert (record.payload("judged") or {}).get("result", {}).get("judge_id")
    assert summary["unfinished"] == []


def test_adoption_is_refused_without_an_attested_parent_champion(
    parent_run: tuple[Path, World], child: World, tmp_path: Path
) -> None:
    parent_dir, _ = parent_run
    # Copie du parent dont le journal ne contient pas la promotion : champion non attesté.
    forged = tmp_path / "forged_parent"
    forged.mkdir()
    for name in ("world.json", "registry.jsonl"):
        (forged / name).write_bytes((parent_dir / name).read_bytes())
    (forged / "journal.jsonl").write_text("")
    ctrl = LoopController(tmp_path / "child", child, log=lambda _m: None)
    with pytest.raises(ValueError, match="non attesté"):
        ctrl.adopt(forged, reason="parent falsifié")
    with pytest.raises(ValueError, match="aucun parent"):
        LoopController(tmp_path / "orphan", support.world(), log=lambda _m: None).adopt(
            parent_dir, reason="monde sans parent"
        )


def test_a_checkpoint_from_an_unrelated_world_is_refused(
    parent_run: tuple[Path, World], child: World, tmp_path: Path
) -> None:
    parent_dir, parent = parent_run
    champion = LoopController(parent_dir, parent, log=lambda _m: None).registry.current()
    assert champion is not None
    from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore

    store = LocalFilesystemArtifactStore(root=str(parent_dir / "artifacts"))
    assert load_model_from_artifact(store, champion["artifact"], child) is not None  # ancêtre : accepté
    stranger = World.build(
        replace(support.SPEC, seed=7),
        support.candidates(),
        capability_data={"code_repair": support.benchmark()},
    )
    with pytest.raises(ValueError, match=r"étranger|tokenizer"):
        load_model_from_artifact(store, champion["artifact"], stranger)
    assert json.dumps(initial_model(child, 0).config.to_dict())  # même architecture que le parent


def test_new_documents_overlapping_the_lineage_training_are_removed(
    parent_run: tuple[Path, World],
) -> None:
    """Un nouveau document dont le contenu est DÉJÀ dans l'entraînement du parent (ici une copie
    d'un fragment de source entraîné) ne peut ni servir de test ni apporter quoi que ce soit."""
    _, parent = parent_run
    seen = next(c for c in parent.dataset.splits[Split.TRAIN] if c.source_id.startswith("file://"))
    copy = _doc("copy_of_parent_training", seen.text)
    child = World.build(child_spec(parent), [copy, *new_documents()], parent=parent)
    assert copy.candidate_id in child.dataset.decontaminated or copy.candidate_id in (
        child.dataset.dropped_duplicates
    )
    assert all(c.candidate_id != copy.candidate_id for s in Split for c in child.dataset.splits[s])


def test_a_checkpoint_from_a_sibling_world_is_refused_despite_the_same_tokenizer(
    parent_run: tuple[Path, World], tmp_path: Path
) -> None:
    """Deux enfants du même parent partagent le tokenizer ; un checkpoint entraîné dans l'un n'est
    PAS de la lignée de l'autre : seule l'empreinte du dataset le refuse."""
    from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
    from apex.neural.checkpoint import save_checkpoint
    from apex.neural.config import TrainingConfig
    from apex.neural.training import TrainingEngine

    _, parent = parent_run
    docs = new_documents()
    first = World.build(child_spec(parent), docs, parent=parent)
    second = World.build(child_spec(parent, seed=parent.spec.seed + 1), docs, parent=parent)
    assert first.tokenizer.fingerprint() == second.tokenizer.fingerprint()
    assert first.dataset.fingerprint != second.dataset.fingerprint
    engine = TrainingEngine(initial_model(first, 0), TrainingConfig(max_steps=1, batch_size=2))
    path = tmp_path / "first.pt"
    save_checkpoint(
        path,
        engine,
        tokenizer=first.tokenizer,
        batcher=None,
        dataset_fingerprint=first.dataset.fingerprint,
        dataset_provenance={"world": first.fingerprint},
        dataset_tokens=1,
    )
    store = LocalFilesystemArtifactStore(root=str(tmp_path / "store"))
    artifact = store.put(path.read_bytes(), content_type="application/x-apex-checkpoint").content_hash
    assert load_model_from_artifact(store, artifact, first) is not None
    with pytest.raises(ValueError, match="étranger"):
        load_model_from_artifact(store, artifact, second)

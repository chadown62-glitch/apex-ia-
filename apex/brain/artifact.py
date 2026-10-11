"""Poids SERVIS du Cerveau APEX : un répertoire autonome, lisible sans pickle.

    <racine>/<nom>/model.safetensors   poids (safetensors : aucun code exécuté au chargement)
                   config.json         architecture (`ModelConfig`)
                   tokenizer.json      tokenizer BPE d'APEX
                   provenance.json     d'où viennent ces poids : expérience, registre, empreintes, mesures

Deux contrôles à chaque chargement, jamais contournés : le SHA-256 du fichier de poids, puis l'empreinte
des poids réellement chargés (`model_fingerprint`), comparés à `provenance.json`. Des poids modifiés ne sont
jamais servis sous le nom du Cerveau APEX.

Les checkpoints d'ENTRAÎNEMENT (`apex.neural.checkpoint`, pickle, état de l'optimiseur) restent hors de
l'application : seul l'export ci-dessous y entre.

Génération 2 : les poids peuvent être servis quantifiés en APEX-Q5 (`apex.brain.quant`) — `provenance.json`
désigne alors le fichier (`weights_file`, ex. `model.q5.safetensors`) et sa quantification ; l'empreinte des
poids est celle des poids DÉQUANTIFIÉS en float32 (déterministe), contrôlée de la même façon."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_NAME = "apex-brain-v1"
WEIGHTS = "model.safetensors"
FILES = (WEIGHTS, "config.json", "tokenizer.json", "provenance.json")
BRAIN_DIR_ENV = "APEX_BRAIN_DIR"


def default_root() -> Path:
    """`APEX_BRAIN_DIR`, sinon `brain/` à côté du paquet `apex` (dépôt : racine ; application installée :
    `app\\brain`)."""
    configured = os.environ.get(BRAIN_DIR_ENV)
    return Path(configured) if configured else Path(__file__).resolve().parents[2] / "brain"


def brain_roots() -> list[Path]:
    """Où chercher les Cerveaux, dans l'ordre : `APEX_BRAIN_DIR` seul s'il est posé ; sinon le dossier des
    Cerveaux de l'utilisateur (`$APEX_HOME/brain` : Cerveaux entraînés sur ce poste, conservés lors des
    mises à jour de l'application), puis celui livré avec l'application."""
    configured = os.environ.get(BRAIN_DIR_ENV)
    if configured:
        return [Path(configured)]
    roots = []
    home = os.environ.get("APEX_HOME")
    if home:
        roots.append(Path(home) / "brain")
    roots.append(Path(__file__).resolve().parents[2] / "brain")
    return roots


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class Inspection:
    ok: bool
    detail: str
    provenance: dict[str, Any] | None = None


def weights_file(provenance: dict[str, Any]) -> str:
    """Nom du fichier de poids servi (`model.safetensors` en float32, ou le fichier APEX-Q5 déclaré)."""
    name = str(provenance.get("weights_file", WEIGHTS))
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"fichier de poids « {name} » refusé (un nom de fichier simple)")
    return name


def inspect(directory: Path) -> Inspection:
    """Présence des fichiers, provenance lisible, SHA-256 des poids conforme. Rien n'est chargé en
    mémoire."""
    absent = [name for name in FILES if name != WEIGHTS and not (directory / name).is_file()]
    if absent:
        return Inspection(False, f"Cerveau APEX absent ou incomplet dans {directory} (manquant : {', '.join(absent)})")
    try:
        provenance = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
        expected = str(provenance["weights_sha256"])
        weights = weights_file(provenance)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return Inspection(False, f"provenance du Cerveau APEX illisible ({exc.__class__.__name__}: {exc})")
    if not (directory / weights).is_file():
        return Inspection(False, f"Cerveau APEX absent ou incomplet dans {directory} (manquant : "
                                 f"{weights})")
    actual = sha256_file(directory / weights)
    if actual != expected:
        return Inspection(False, f"poids du Cerveau APEX modifiés : SHA-256 {actual[:16]}… au lieu de "
                                 f"{expected[:16]}… — refusés", provenance)
    return Inspection(True, "poids conformes à leur provenance", provenance)


def export(model: Any, tokenizer: Any, directory: Path, provenance: dict[str, Any], *,
           quantization: str | None = None) -> dict[str, Any]:
    """Écrit les quatre fichiers ; complète la provenance avec le SHA-256 du fichier de poids et l'empreinte
    des poids. `quantization="apex-q5"` : poids écrits en APEX-Q5 (`model.q5.safetensors`), l'empreinte est
    celle des poids déquantifiés, l'erreur de quantification mesurée est ajoutée à la provenance. Retourne
    la provenance écrite."""
    from safetensors.torch import save_model

    from apex.brain.model import model_fingerprint

    directory.mkdir(parents=True, exist_ok=True)
    if quantization == "apex-q5":
        from apex.brain.config import ModelConfig
        from apex.brain.model import ApexLM
        from apex.brain.quant import FORMAT, load_q5_state, save_q5

        weights = "model.q5.safetensors"
        report = save_q5(model, directory / weights)
        served = ApexLM(ModelConfig.from_dict(model.config.to_dict()))
        served.load_state_dict(load_q5_state(directory / weights))
        provenance = {**provenance, "weights_file": weights, "quantization": FORMAT,
                      "quantization_report": report.to_dict()}
        fingerprint_model = served
    elif quantization is None:
        weights = WEIGHTS
        save_model(model, str(directory / WEIGHTS))
        fingerprint_model = model
    else:
        raise ValueError(f"quantification inconnue : {quantization}")
    (directory / "config.json").write_text(json.dumps(model.config.to_dict(), indent=2, sort_keys=True) + "\n",
                                           encoding="utf-8")
    (directory / "tokenizer.json").write_text(json.dumps(tokenizer.to_dict(), sort_keys=True) + "\n",
                                              encoding="utf-8")
    written = {**provenance, "weights_sha256": sha256_file(directory / weights),
               "model_fingerprint": model_fingerprint(fingerprint_model),
               "parameters": model.num_parameters()}
    (directory / "provenance.json").write_text(json.dumps(written, indent=2, ensure_ascii=False) + "\n",
                                               encoding="utf-8")
    return written


@dataclass
class LoadedBrain:
    model: Any
    tokenizer: Any
    provenance: dict[str, Any]
    fingerprint: str
    load_s: float


def load(directory: Path) -> LoadedBrain:
    """Charge le Cerveau (CPU, float32, mode évaluation) après les deux contrôles ; lève `ValueError` si
    l'un échoue."""
    started = time.perf_counter()
    found = inspect(directory)
    if not found.ok or found.provenance is None:
        raise ValueError(found.detail)
    from safetensors.torch import load_model

    from apex.brain.config import ModelConfig
    from apex.brain.model import ApexLM, model_fingerprint
    from apex.brain.tokenizer import ByteBPETokenizer

    config = ModelConfig.from_dict(json.loads((directory / "config.json").read_text(encoding="utf-8")))
    model = ApexLM(config)
    weights = weights_file(found.provenance)
    if found.provenance.get("quantization"):
        from apex.brain.quant import FORMAT, load_q5_state

        if found.provenance["quantization"] != FORMAT:
            raise ValueError(f"quantification {found.provenance['quantization']} inconnue "
                             f"(attendu {FORMAT})")
        model.load_state_dict(load_q5_state(directory / weights), strict=True)
    else:
        load_model(model, str(directory / weights), strict=True)
    model.eval()
    fingerprint = model_fingerprint(model)
    expected = found.provenance.get("model_fingerprint")
    if fingerprint != expected:
        raise ValueError(f"empreinte des poids chargés {fingerprint[:23]}… différente de la provenance "
                         f"({str(expected)[:23]}…) — Cerveau APEX refusé")
    tokenizer = ByteBPETokenizer.from_dict(json.loads((directory / "tokenizer.json").read_text(encoding="utf-8")))
    return LoadedBrain(model, tokenizer, found.provenance, fingerprint, time.perf_counter() - started)

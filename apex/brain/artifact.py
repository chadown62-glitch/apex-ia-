"""Poids SERVIS du Cerveau APEX : un répertoire autonome, lisible sans pickle.

    <racine>/<nom>/model.safetensors   poids (safetensors : aucun code exécuté au chargement)
                   config.json         architecture (`ModelConfig`)
                   tokenizer.json      tokenizer BPE d'APEX
                   provenance.json     d'où viennent ces poids : expérience, registre, empreintes, mesures

Deux contrôles à chaque chargement, jamais contournés : le SHA-256 du fichier de poids, puis l'empreinte
des poids réellement chargés (`model_fingerprint`), comparés à `provenance.json`. Des poids modifiés ne sont
jamais servis sous le nom du Cerveau APEX.

Les checkpoints d'ENTRAÎNEMENT (`apex.neural.checkpoint`, pickle, état de l'optimiseur) restent hors de
l'application : seul l'export ci-dessous y entre."""

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


def inspect(directory: Path) -> Inspection:
    """Présence des fichiers, provenance lisible, SHA-256 des poids conforme. Rien n'est chargé en mémoire."""
    absent = [name for name in FILES if not (directory / name).is_file()]
    if absent:
        return Inspection(False, f"Cerveau APEX absent ou incomplet dans {directory} (manquant : {', '.join(absent)})")
    try:
        provenance = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
        expected = str(provenance["weights_sha256"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return Inspection(False, f"provenance du Cerveau APEX illisible ({exc.__class__.__name__}: {exc})")
    actual = sha256_file(directory / WEIGHTS)
    if actual != expected:
        return Inspection(False, f"poids du Cerveau APEX modifiés : SHA-256 {actual[:16]}… au lieu de "
                                 f"{expected[:16]}… — refusés", provenance)
    return Inspection(True, "poids conformes à leur provenance", provenance)


def export(model: Any, tokenizer: Any, directory: Path, provenance: dict[str, Any]) -> dict[str, Any]:
    """Écrit les quatre fichiers ; complète la provenance avec le SHA-256 du fichier de poids et l'empreinte
    des poids. Retourne la provenance écrite."""
    from safetensors.torch import save_model

    from apex.brain.model import model_fingerprint

    directory.mkdir(parents=True, exist_ok=True)
    save_model(model, str(directory / WEIGHTS))
    (directory / "config.json").write_text(json.dumps(model.config.to_dict(), indent=2, sort_keys=True) + "\n",
                                           encoding="utf-8")
    (directory / "tokenizer.json").write_text(json.dumps(tokenizer.to_dict(), sort_keys=True) + "\n",
                                              encoding="utf-8")
    written = {**provenance, "weights_sha256": sha256_file(directory / WEIGHTS),
               "model_fingerprint": model_fingerprint(model), "parameters": model.num_parameters()}
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
    """Charge le Cerveau (CPU, float32, mode évaluation) après les deux contrôles ; lève `ValueError` si l'un
    échoue."""
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
    load_model(model, str(directory / WEIGHTS), strict=True)
    model.eval()
    fingerprint = model_fingerprint(model)
    expected = found.provenance.get("model_fingerprint")
    if fingerprint != expected:
        raise ValueError(f"empreinte des poids chargés {fingerprint[:23]}… différente de la provenance "
                         f"({str(expected)[:23]}…) — Cerveau APEX refusé")
    tokenizer = ByteBPETokenizer.from_dict(json.loads((directory / "tokenizer.json").read_text(encoding="utf-8")))
    return LoadedBrain(model, tokenizer, found.provenance, fingerprint, time.perf_counter() - started)

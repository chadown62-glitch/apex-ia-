"""Placement EXPLICITE des poids d'un modèle natif (`apex models fetch`) — seule partie d'APEX qui télécharge
des poids, uniquement à la demande de l'utilisateur (jamais pendant `detect`/`generate`).

Source : Hugging Face Hub (`huggingface_hub`, dépendance de l'extra « native »). Seuls les fichiers nécessaires
à l'inférence sont récupérés (configuration, tokenizer, poids `safetensors`) — jamais de code distant
(`trust_remote_code` n'est jamais activé par le backend natif).
"""

from __future__ import annotations

from pathlib import Path

ALLOWED_PATTERNS = ["*.json", "*.safetensors", "merges.txt", "vocab.json", "*.model", "*.txt"]


def fetch_model(repo_id: str, *, models_dir: Path, name: str, revision: str | None = None) -> Path:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"nom de modèle « {name} » refusé (un nom de répertoire simple)")
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:  # pragma: no cover - dépend de l'installation
        raise RuntimeError("huggingface_hub absent — installer l'extra « native »") from exc
    target = Path(models_dir).expanduser() / name
    target.mkdir(parents=True, exist_ok=True)
    # Un téléchargement interrompu ne laisse jamais un modèle « installé » : la provenance (marqueur relu par
    # le Panel) n'est écrite qu'après le téléchargement ET la vérification des empreintes.
    (target / ".apex_source.json").unlink(missing_ok=True)
    snapshot_download(repo_id=repo_id, revision=revision, local_dir=str(target), allow_patterns=ALLOWED_PATTERNS)
    verified = verify_weights(target, _expected_sha256(repo_id, revision))
    _record_source(target, repo_id, revision, verified=verified)
    return target


def _expected_sha256(repo_id: str, revision: str | None) -> dict[str, str]:
    """Empreintes SHA-256 PUBLIÉES par le Hub pour les gros fichiers (LFS) de CETTE révision."""
    from huggingface_hub import HfApi

    info = HfApi().model_info(repo_id, revision=revision, files_metadata=True)
    expected: dict[str, str] = {}
    for sibling in info.siblings or []:
        lfs = getattr(sibling, "lfs", None)
        sha = getattr(lfs, "sha256", None) if lfs is not None else None
        if sha is None and isinstance(lfs, dict):
            sha = lfs.get("sha256")
        if sha:
            expected[sibling.rfilename] = str(sha).lower()
    return expected


def verify_weights(target: Path, expected: dict[str, str]) -> list[str]:
    """Chaque fichier de poids téléchargé doit avoir l'empreinte publiée ; un fichier différent est SUPPRIMÉ
    (jamais chargé) et le téléchargement refusé. Rend la liste des fichiers vérifiés."""
    import hashlib

    weights = sorted(f.name for f in target.glob("*.safetensors"))
    if not weights:
        raise RuntimeError(f"aucun fichier de poids (*.safetensors) dans {target}")
    missing = [w for w in weights if w not in expected]
    if missing:
        raise RuntimeError(f"empreinte publiée introuvable pour {', '.join(missing)} : vérification impossible")
    for name in weights:
        digest = hashlib.sha256()
        with open(target / name, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected[name]:
            (target / name).unlink(missing_ok=True)
            raise RuntimeError(f"empreinte inattendue pour {name} (fichier supprimé) : relancez l'installation")
    return weights


def _record_source(target: Path, repo_id: str, revision: str | None, *, verified: list[str] | None = None) -> None:
    """Provenance des poids (dépôt, révision demandée, commit résolu) — `.apex_source.json`, relu par
    `source_of` : un résultat mesuré dit exactement QUELS poids l'ont produit."""
    import json

    resolved = None
    try:
        from huggingface_hub import model_info

        resolved = model_info(repo_id, revision=revision).sha
    except Exception:  # la provenance reste « non résolue », jamais inventée
        resolved = None
    (target / ".apex_source.json").write_text(json.dumps(
        {"repo": repo_id, "revision": revision, "commit": resolved, "sha256_verified": verified or []}),
        encoding="utf-8")


def source_of(weights: Path) -> dict | None:
    import json

    path = Path(weights) / ".apex_source.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def default_models_dir() -> Path | None:
    """Répertoire du premier endpoint natif déclaré dans la configuration, s'il existe."""
    from apex.config.loader import load_config
    from apex.models.native import weights_dir

    for endpoint in load_config().endpoints:
        if endpoint.runtime == "transformers":
            path = weights_dir(endpoint)
            if path is not None:
                return path.parent
    return None

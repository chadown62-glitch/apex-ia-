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
    snapshot_download(repo_id=repo_id, revision=revision, local_dir=str(target), allow_patterns=ALLOWED_PATTERNS)
    _record_source(target, repo_id, revision)
    return target


def _record_source(target: Path, repo_id: str, revision: str | None) -> None:
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
        {"repo": repo_id, "revision": revision, "commit": resolved}), encoding="utf-8")


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

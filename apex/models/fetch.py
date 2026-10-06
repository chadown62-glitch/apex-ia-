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
    return target


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

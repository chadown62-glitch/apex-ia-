"""Espaces de travail de l'assistant (mode Code, pièces jointes) : un par projet — partagé par ses conversations —
et un par conversation hors projet, sous `<paths.workspaces>/assistant/`. Mêmes garanties que ceux du TaskLoop
(`apex.tools.workspace.Workspace` : chemins confinés, quotas) ; supprimés avec leur projet ou conversation."""

from __future__ import annotations

import shutil
from pathlib import Path

from apex.tools.workspace import Workspace

_PREFIXES = {"project": "project-", "conversation": "conversation-"}


def _base() -> Path:
    from apex.config.loader import load_config

    return Path(load_config().paths.workspaces) / "assistant"


def _safe_id(value: str) -> str:
    if not value or any(not (c.isalnum() or c == "-") for c in value) or len(value) > 64:
        raise ValueError(f"identifiant d'espace de travail refusé : {value!r}")
    return value


def project_workspace(project_id: str) -> Workspace:
    return Workspace.create(_base(), _PREFIXES["project"] + _safe_id(project_id))


def conversation_workspace(conversation_id: str, project_id: str | None) -> Workspace:
    """Espace du projet si la conversation en a un (continuité entre conversations), sinon le sien."""
    if project_id:
        return project_workspace(project_id)
    return Workspace.create(_base(), _PREFIXES["conversation"] + _safe_id(conversation_id))


def remove(kind: str, owner_id: str) -> bool:
    """Supprime l'espace d'un projet ou d'une conversation supprimés (jamais un chemin hors de la base)."""
    base = _base().resolve()
    target = (base / (_PREFIXES[kind] + _safe_id(owner_id))).resolve()
    if base not in target.parents or not target.exists():
        return False
    shutil.rmtree(target)
    return True

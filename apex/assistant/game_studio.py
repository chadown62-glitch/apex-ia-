"""Suivi, depuis l'assistant, des missions que Game Studio exécute pour une conversation : état, résultats
(empreintes des fichiers, jamais leurs octets), annulation.

Tout passe par les outils de Game Studio (`apex.game_studio.delegation`, propriété de Codex) et le moteur
d'outils, avec l'espace de travail de la conversation (ou de son projet) : Game Studio ne rend une mission
qu'à l'espace de travail qui l'a lancée. Rien de Game Studio n'est réimplémenté ici."""

from __future__ import annotations

import uuid
from typing import Any


class GameStudioUnavailable(RuntimeError):
    """Game Studio absent de l'installation, ou impossible à charger."""


def _engine(session_factory: Any, conversation_id: str, granted: frozenset[Any],
            service: Any) -> tuple[Any, Any]:
    from apex.assistant.tools import register_game_studio
    from apex.assistant.workspace import conversation_workspace
    from apex.conversations.service import ConversationService
    from apex.tools.engine import ToolContext, ToolEngine, ToolRegistry

    conversation = ConversationService(session_factory).get(conversation_id)
    if conversation is None:
        raise LookupError(f"conversation « {conversation_id} » introuvable")
    registry = ToolRegistry()
    absent = register_game_studio(registry)
    if absent is not None:
        raise GameStudioUnavailable(absent)
    workspace = conversation_workspace(conversation.id, conversation.project_id)
    extra = {} if service is None else {"game_studio_service": service}
    return ToolEngine(registry), ToolContext(workspace=workspace, granted=granted, extra=extra)


def _call(engine: Any, context: Any, tool: str, project_id: str, conversation_id: str) -> dict[str, Any]:
    from apex.tools.model import ErrorCode, ToolCall

    call = ToolCall(tool=tool, arguments={"project_id": project_id}, call_id=str(uuid.uuid4()),
                    requested_by=f"assistant:conversation:{conversation_id}")
    result = engine.execute(call, context)
    if result.ok:
        return dict(result.output)
    error = result.error
    if error is not None and error.code in (ErrorCode.NOT_FOUND, ErrorCode.INVALID_INPUT):
        # Mission inconnue, ou lancée depuis un autre espace de travail : jamais distinguées.
        raise LookupError(f"mission Game Studio « {project_id} » introuvable dans cette conversation")
    raise GameStudioUnavailable(f"{tool} : {error.message if error else 'échec'}"[:300])


def follow(session_factory: Any, conversation_id: str, project_id: str, *,
           service: Any = None) -> dict[str, Any]:
    """État (`game_studio.status`) puis résultats (`game_studio.results`) d'une mission de cette
    conversation. Lecture seule : permission `game_studio.read` uniquement."""
    from apex.assistant.modes import GAME_STUDIO_LAUNCH, GAME_STUDIO_PERMISSIONS

    read_only = GAME_STUDIO_PERMISSIONS - GAME_STUDIO_LAUNCH
    engine, context = _engine(session_factory, conversation_id, read_only, service)
    status = _call(engine, context, "game_studio.status", project_id, conversation_id)
    results = _call(engine, context, "game_studio.results", project_id, conversation_id)
    return {"project_id": project_id, "status": status, "results": results}


def cancel(session_factory: Any, conversation_id: str, project_id: str, *, missions_allowed: bool,
           service: Any = None) -> dict[str, Any]:
    """Annule une mission de cette conversation (`game_studio.cancel`), seulement si l'utilisateur a permis
    les missions Game Studio (Paramètres > Outils)."""
    from apex.assistant.modes import GAME_STUDIO_PERMISSIONS

    if not missions_allowed:
        raise PermissionError("missions Game Studio non permises (Paramètres > Outils)")
    cancel_permission = frozenset(p for p in GAME_STUDIO_PERMISSIONS if p.value == "game_studio.cancel")
    engine, context = _engine(session_factory, conversation_id, cancel_permission, service)
    return _call(engine, context, "game_studio.cancel", project_id, conversation_id)

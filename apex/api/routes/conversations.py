"""GET/POST/PATCH/DELETE /conversations — Chat réel (passe « Première
interface utilisable »).

Même limite déjà documentée dans `apex/api/routes/agents.py` : un jeton est
désormais exigé (passe « API Authentication Hardening », `apex.api.auth`),
mais aucun mapping appelant -> `Subject` précis n'existe encore — toute
action ici est exercée comme si l'appelant était `Subject.HUMAN`, la même
hypothèse simplificatrice déjà en place pour `/agents`/`/missions`.

`POST /{id}/messages` est le SEUL point d'entrée réel du parcours :
UI -> API -> `ConversationService.send_message` -> `apex.routing.
select_model` -> `apex.routing.generate_with_fallback` -> `apex.models.
provider.get_default_provider` -> `OllamaProvider`/`OpenAICompatibleProvider`
-> backend réel -> réponse réelle. Jamais un texte simulé : un échec de
génération revient avec `status="real"` (l'enveloppe HTTP décrit la requête,
pas la génération) et `data.assistant_message.status="error"` (voir
`apex.conversations.model.MessageStatus`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from apex.api.schemas import (
    ConversationCreateIn,
    ConversationOut,
    ConversationRenameIn,
    DataEnvelope,
    MessageOut,
    SendMessageIn,
    SendMessageOut,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _to_conversation_out(conversation: object) -> ConversationOut:
    return ConversationOut(
        id=conversation.id,  # type: ignore[attr-defined]
        title=conversation.title,  # type: ignore[attr-defined]
        project_id=conversation.project_id,  # type: ignore[attr-defined]
        created_by=conversation.created_by,  # type: ignore[attr-defined]
        created_at=conversation.created_at.isoformat(),  # type: ignore[attr-defined]
        updated_at=conversation.updated_at.isoformat(),  # type: ignore[attr-defined]
    )


def _to_message_out(message: object) -> MessageOut:
    return MessageOut(
        id=message.id,  # type: ignore[attr-defined]
        conversation_id=message.conversation_id,  # type: ignore[attr-defined]
        role=message.role.value,  # type: ignore[attr-defined]
        content=message.content,  # type: ignore[attr-defined]
        model_id=message.model_id,  # type: ignore[attr-defined]
        provider_id=message.provider_id,  # type: ignore[attr-defined]
        status=message.status.value,  # type: ignore[attr-defined]
        error=message.error,  # type: ignore[attr-defined]
        created_at=message.created_at.isoformat(),  # type: ignore[attr-defined]
    )


@router.get("", response_model=DataEnvelope[list[ConversationOut]])
def list_conversations() -> DataEnvelope[list[ConversationOut]]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.conversations.service import ConversationService

        conversations = ConversationService(get_session_factory()).list_all()
        views = [_to_conversation_out(c) for c in conversations]
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=views)


@router.get("/{conversation_id}", response_model=DataEnvelope[ConversationOut])
def get_conversation(conversation_id: str) -> DataEnvelope[ConversationOut]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.conversations.service import ConversationService

        conversation = ConversationService(get_session_factory()).get(conversation_id)
        if conversation is None:
            return DataEnvelope(status="unknown", data=None, detail=f"conversation « {conversation_id} » introuvable")
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_conversation_out(conversation))


@router.get("/{conversation_id}/messages", response_model=DataEnvelope[list[MessageOut]])
def list_messages(conversation_id: str) -> DataEnvelope[list[MessageOut]]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.conversations.service import ConversationService

        messages = ConversationService(get_session_factory()).list_messages(conversation_id)
        views = [_to_message_out(m) for m in messages]
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=views)


@router.post("", response_model=DataEnvelope[ConversationOut])
def create_conversation(payload: ConversationCreateIn) -> DataEnvelope[ConversationOut]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.conversations.service import ConversationService
        from apex.policy.model import Subject

        conversation = ConversationService(get_session_factory()).create(
            title=payload.title, created_by=Subject.HUMAN, project_id=payload.project_id,
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_conversation_out(conversation))


@router.patch("/{conversation_id}", response_model=DataEnvelope[ConversationOut])
def rename_conversation(conversation_id: str, payload: ConversationRenameIn) -> DataEnvelope[ConversationOut]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.conversations.service import ConversationService
        from apex.policy.model import Subject

        conversation = ConversationService(get_session_factory()).rename(
            conversation_id, title=payload.title, requested_by=Subject.HUMAN,
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_conversation_out(conversation))


@router.delete("/{conversation_id}", response_model=DataEnvelope[bool])
def delete_conversation(conversation_id: str) -> DataEnvelope[bool]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.conversations.service import ConversationService
        from apex.policy.model import Subject

        deleted = ConversationService(get_session_factory()).delete(conversation_id, requested_by=Subject.HUMAN)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=deleted)


@router.post("/{conversation_id}/messages", response_model=DataEnvelope[SendMessageOut])
def send_message(conversation_id: str, payload: SendMessageIn) -> DataEnvelope[SendMessageOut]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.conversations.service import ConversationService
        from apex.policy.model import Subject

        user_message, assistant_message = ConversationService(get_session_factory()).send_message(
            conversation_id, content=payload.content, created_by=Subject.HUMAN, model_role=payload.model_role,
            use_memory=payload.use_memory,
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    recalls = _recalls(get_session_factory(), conversation_id, since=user_message.created_at)
    return DataEnvelope(
        status="real",
        data=SendMessageOut(
            user_message=_to_message_out(user_message), assistant_message=_to_message_out(assistant_message),
            memory=recalls.get(assistant_message.id, []),
        ),
    )


def _recalls(session_factory: Any, conversation_id: str, *, since: Any) -> dict[str, list[dict[str, Any]]]:
    """Souvenirs joints à chaque réponse, relus dans le journal (`conversation.message_received`) :
    message assistant -> souvenirs (provenance, score). Jamais reconstruits."""
    from datetime import timedelta

    from apex.db.engine import session_scope
    from apex.db.repositories import EventRepository

    with session_scope(session_factory) as session:
        rows = EventRepository(session).in_window(logger="apex.conversations", since=since - timedelta(seconds=5),
                                                  limit=5000)
        return {
            row.context["assistant_message_id"]: list(row.context.get("memory") or [])
            for row in rows
            if row.context.get("conversation_id") == conversation_id
            and row.context.get("kind") == "conversation.message_received"
            and row.context.get("assistant_message_id")
        }


@router.get("/{conversation_id}/recalls", response_model=DataEnvelope[dict[str, list[dict[str, Any]]]])
def list_recalls(conversation_id: str) -> DataEnvelope[dict[str, list[dict[str, Any]]]]:
    """Pour chaque réponse de la conversation : les souvenirs système réellement utilisés (provenance)."""
    try:
        from apex.api.dependencies import get_session_factory
        from apex.conversations.service import ConversationService

        factory = get_session_factory()
        conversation = ConversationService(factory).get(conversation_id)
        if conversation is None:
            return DataEnvelope(status="unknown", data=None, detail=f"conversation « {conversation_id} » introuvable")
        data = _recalls(factory, conversation_id, since=conversation.created_at)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=data)

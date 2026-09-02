"""Persistance des conversations/messages — couche domaine au-dessus
d'`apex.db`, même séparation qu'`apex.missions.repository` vis-à-vis
d'`apex.missions.model`. Imports vers `apex.db` différés : `apex.
conversations` doit rester importable sans l'extra `db` installé.
"""

from __future__ import annotations

from typing import Any

from apex.conversations.model import Conversation, Message, MessageRole, MessageStatus


class ConversationRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(self, conversation: Conversation) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import ConversationRepository as _DbConversationRepository

        with session_scope(self._session_factory) as session:
            _DbConversationRepository(session).create(
                id=conversation.id, title=conversation.title, project_id=conversation.project_id,
                created_by=conversation.created_by, created_at=conversation.created_at,
            )

    def rename(self, conversation_id: str, *, title: str) -> Conversation | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import ConversationRepository as _DbConversationRepository
        from apex.conversations.model import _utcnow

        with session_scope(self._session_factory) as session:
            row = _DbConversationRepository(session).rename(conversation_id, title=title, updated_at=_utcnow())
            return Conversation.from_row(row) if row is not None else None

    def touch(self, conversation_id: str) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import ConversationRepository as _DbConversationRepository
        from apex.conversations.model import _utcnow

        with session_scope(self._session_factory) as session:
            _DbConversationRepository(session).touch(conversation_id, updated_at=_utcnow())

    def get(self, conversation_id: str) -> Conversation | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import ConversationRepository as _DbConversationRepository

        with session_scope(self._session_factory) as session:
            row = _DbConversationRepository(session).get(conversation_id)
            return Conversation.from_row(row) if row is not None else None

    def list_all(self, limit: int = 200) -> list[Conversation]:
        from apex.db.engine import session_scope
        from apex.db.repositories import ConversationRepository as _DbConversationRepository

        with session_scope(self._session_factory) as session:
            rows = _DbConversationRepository(session).list_all(limit=limit)
            return [Conversation.from_row(row) for row in rows]

    def delete(self, conversation_id: str) -> bool:
        from apex.db.engine import session_scope
        from apex.db.repositories import ConversationRepository as _DbConversationRepository

        with session_scope(self._session_factory) as session:
            return _DbConversationRepository(session).delete(conversation_id)


class MessageRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def create(
        self,
        *,
        conversation_id: str,
        role: MessageRole,
        content: str,
        model_id: str | None,
        provider_id: str | None,
        status: MessageStatus,
        error: str | None,
    ) -> Message:
        import uuid

        from apex.conversations.model import _utcnow
        from apex.db.engine import session_scope
        from apex.db.repositories import MessageRepository as _DbMessageRepository

        with session_scope(self._session_factory) as session:
            row = _DbMessageRepository(session).create(
                id=str(uuid.uuid4()), conversation_id=conversation_id, role=role.value, content=content,
                model_id=model_id, provider_id=provider_id, status=status.value, error=error,
                created_at=_utcnow(),
            )
            return Message.from_row(row)

    def list_by_conversation(self, conversation_id: str, limit: int = 500) -> list[Message]:
        from apex.db.engine import session_scope
        from apex.db.repositories import MessageRepository as _DbMessageRepository

        with session_scope(self._session_factory) as session:
            rows = _DbMessageRepository(session).list_by_conversation(conversation_id, limit=limit)
            return [Message.from_row(row) for row in rows]

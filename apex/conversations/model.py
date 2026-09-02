"""Modèles de domaine Conversation/Message — dataclasses pures, aucune
dépendance DB (même séparation qu'`apex.missions.model.Mission` vis-à-vis
d'`apex.db.models`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apex.db.models import ConversationRow, MessageRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageStatus(str, Enum):
    """Distinct d'`apex.obs.status.DataStatus` : répond à "cette génération
    a-t-elle réellement produit un texte", pas "la donnée renvoyée par
    l'API est-elle réelle" (l'enveloppe HTTP porte déjà `DataStatus`,
    toujours `"real"` pour un message lu depuis la DB, qu'il soit lui-même
    REAL ou ERROR — ce sont deux questions différentes)."""

    #: Un message utilisateur (toujours REAL) ou une génération réussie.
    REAL = "real"
    #: Aucun candidat disponible, ou tous les candidats ont échoué —
    #: `content` reste vide, `error` porte le détail (jamais un texte
    #: inventé à la place d'un échec réel).
    ERROR = "error"


@dataclass
class Message:
    id: str
    conversation_id: str
    role: MessageRole
    content: str
    model_id: str | None
    provider_id: str | None
    status: MessageStatus
    error: str | None
    created_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def from_row(cls, row: "MessageRow") -> Message:
        return cls(
            id=row.id, conversation_id=row.conversation_id, role=MessageRole(row.role),
            content=row.content, model_id=row.model_id, provider_id=row.provider_id,
            status=MessageStatus(row.status), error=row.error, created_at=row.created_at,
        )


@dataclass
class Conversation:
    id: str
    title: str
    project_id: str | None
    created_by: str
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def new(cls, *, title: str, created_by: str, project_id: str | None = None) -> Conversation:
        now = _utcnow()
        return cls(
            id=str(uuid.uuid4()), title=title, project_id=project_id,
            created_by=created_by, created_at=now, updated_at=now,
        )

    @classmethod
    def from_row(cls, row: "ConversationRow") -> Conversation:
        return cls(
            id=row.id, title=row.title, project_id=row.project_id, created_by=row.created_by,
            created_at=row.created_at, updated_at=row.updated_at,
        )

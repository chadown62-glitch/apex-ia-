"""ConversationService — Chat réel. Voir la docstring d'`apex.conversations`
pour pourquoi ce service ne passe jamais par `apex.missions`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from apex.conversations.model import Conversation, Message, MessageRole, MessageStatus
from apex.conversations.repository import ConversationRepository, MessageRepository
from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject

if TYPE_CHECKING:
    from apex.config.schema import ApexConfig
    from apex.models.provider import ModelProvider
    from apex.models.registry import ModelCandidate


#: Mémoire de conversation transmise au modèle : messages récents seulement, bornés en caractères.
_HISTORY_SCAN_LIMIT = 200
MAX_HISTORY_CHARS = 6000
MAX_HISTORY_MESSAGE_CHARS = 2000
_SPEAKERS = {MessageRole.USER: "Utilisateur", MessageRole.ASSISTANT: "Assistant"}


def compose_conversation_prompt(history: Sequence[Message], content: str) -> tuple[str, int]:
    """Prompt = échanges précédents RÉELS (jamais un message en erreur ni vide), du plus ancien au plus
    récent, dans un budget de `MAX_HISTORY_CHARS` (les plus récents d'abord), puis le nouveau message.
    Sans historique : le message seul (comportement inchangé). Retourne (prompt, messages repris)."""
    lines: list[str] = []
    budget = MAX_HISTORY_CHARS
    for message in reversed(list(history)):
        if message.status is not MessageStatus.REAL or not message.content.strip():
            continue
        speaker = _SPEAKERS.get(message.role)
        if speaker is None:
            continue
        text = message.content.strip()
        if len(text) > MAX_HISTORY_MESSAGE_CHARS:
            text = text[:MAX_HISTORY_MESSAGE_CHARS] + " […]"
        line = f"{speaker} : {text}"
        if len(line) > budget:
            break
        lines.append(line)
        budget -= len(line)
    if not lines:
        return content, 0
    transcript = "\n".join(reversed(lines))
    return (
        "Échanges précédents de cette conversation (du plus ancien au plus récent) :\n"
        f"{transcript}\n\nNouveau message de l'utilisateur :\n{content}",
        len(lines),
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConversationService:
    def __init__(self, session_factory: Any, *, policy: PolicyEngine | None = None) -> None:
        self.policy = policy or PolicyEngine()
        self._session_factory = session_factory
        self._conversations = ConversationRepository(session_factory)
        self._messages = MessageRepository(session_factory)

    # -- cycle de vie de la conversation ------------------------------------

    def create(self, *, title: str, created_by: Subject, project_id: str | None = None) -> Conversation:
        self.policy.authorize(created_by, Action.CREATE_CONVERSATION)
        if not title.strip():
            raise ValueError("le titre de la conversation est obligatoire")
        conversation = Conversation.new(title=title, created_by=created_by.value, project_id=project_id)
        self._conversations.create(conversation)
        self._record_event(conversation.id, "conversation.created", {})
        self._record_audit(conversation.id, actor=created_by.value, action="conversation.created", details={})
        return conversation

    def rename(self, conversation_id: str, *, title: str, requested_by: Subject) -> Conversation:
        self.policy.authorize(requested_by, Action.UPDATE_CONVERSATION)
        if not title.strip():
            raise ValueError("le titre de la conversation est obligatoire")
        conversation = self._conversations.rename(conversation_id, title=title)
        if conversation is None:
            raise LookupError(f"conversation « {conversation_id} » introuvable")
        self._record_audit(
            conversation.id, actor=requested_by.value, action="conversation.renamed", details={"title": title},
        )
        return conversation

    def delete(self, conversation_id: str, *, requested_by: Subject) -> bool:
        self.policy.authorize(requested_by, Action.DELETE_CONVERSATION)
        deleted = self._conversations.delete(conversation_id)
        if deleted:
            self._record_audit(conversation_id, actor=requested_by.value, action="conversation.deleted", details={})
        return deleted

    def get(self, conversation_id: str) -> Conversation | None:
        return self._conversations.get(conversation_id)

    def list_all(self, limit: int = 200) -> list[Conversation]:
        return self._conversations.list_all(limit=limit)

    def list_messages(self, conversation_id: str, limit: int = 500) -> list[Message]:
        return self._messages.list_by_conversation(conversation_id, limit=limit)

    # -- Chat réel -----------------------------------------------------------

    def send_message(
        self,
        conversation_id: str,
        *,
        content: str,
        created_by: Subject,
        model_role: str | None = None,
        config: "ApexConfig | None" = None,
        provider_factory: "Callable[[ModelCandidate], ModelProvider] | None" = None,
        timeout_s: float = 60.0,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> tuple[Message, Message]:
        """UI -> API -> `ConversationService.send_message` -> `apex.routing.
        select_model` -> `apex.routing.generate_with_fallback` -> `apex.
        models.provider.get_default_provider` -> `OllamaProvider` (ou
        `OpenAICompatibleProvider`) -> backend réel -> réponse réelle,
        persistée ci-dessous. Retourne TOUJOURS le couple (message
        utilisateur, message assistant) — même en cas d'échec total : le
        message assistant porte alors `status=ERROR` et `error` le détail,
        jamais un texte inventé à la place."""
        self.policy.authorize(created_by, Action.SEND_MESSAGE)
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise LookupError(f"conversation « {conversation_id} » introuvable")
        if not content.strip():
            raise ValueError("le contenu du message est obligatoire")

        from apex.models.registry import filter_by_role, resolve_candidates
        from apex.routing.generation import generate_with_fallback
        from apex.routing.model import RoutingRequest
        from apex.routing.router import select_model

        # MÉMOIRE de la conversation : les échanges RÉELS précédents (bornés), lus AVANT d'enregistrer le
        # nouveau message. Avant le 05/10/2026, seul le dernier message était envoyé au modèle.
        history = self._messages.recent_by_conversation(conversation_id, limit=_HISTORY_SCAN_LIMIT)
        prompt, used = compose_conversation_prompt(history, content)
        user_message = self._messages.create(
            conversation_id=conversation_id, role=MessageRole.USER, content=content,
            model_id=None, provider_id=None, status=MessageStatus.REAL, error=None,
        )
        self._conversations.touch(conversation_id)
        self._record_event(conversation_id, "conversation.message_sent", {"role": "user"})

        resolved_config = config if config is not None else self._load_config()
        candidates: Sequence[ModelCandidate] = resolve_candidates(resolved_config)
        if model_role is not None:
            candidates = filter_by_role(candidates, model_role)

        decision = select_model(list(candidates), RoutingRequest())
        if decision.selected is None:
            assistant_message = self._messages.create(
                conversation_id=conversation_id, role=MessageRole.ASSISTANT, content="",
                model_id=None, provider_id=None, status=MessageStatus.ERROR,
                error=f"aucun modèle disponible : {decision.reason}",
            )
            self._conversations.touch(conversation_id)
            self._record_event(conversation_id, "conversation.message_failed", {"reason": decision.reason})
            return user_message, assistant_message

        ranked = [sc.candidate for sc in decision.ranked_candidates if sc.score > 0.0]
        fallback_kwargs: dict[str, Any] = dict(
            prompt=prompt, max_tokens=max_tokens, temperature=temperature, timeout_s=timeout_s,
        )
        if provider_factory is not None:
            fallback_kwargs["provider_factory"] = provider_factory
        fallback_result = generate_with_fallback(ranked, **fallback_kwargs)

        if fallback_result.result is None:
            detail = "; ".join(f"{a.candidate.ref.id} : {a.error}" for a in fallback_result.attempts)
            assistant_message = self._messages.create(
                conversation_id=conversation_id, role=MessageRole.ASSISTANT, content="",
                model_id=None, provider_id=None, status=MessageStatus.ERROR,
                error=f"tous les candidats ont échoué ({len(fallback_result.attempts)}) — {detail}",
            )
            self._conversations.touch(conversation_id)
            self._record_event(conversation_id, "conversation.message_failed", {"detail": detail})
            return user_message, assistant_message

        served = fallback_result.succeeded_with
        assert served is not None  # garanti par fallback_result.result is not None
        provider_id = f"{served.endpoint.runtime}:{served.endpoint.url}" if served.endpoint is not None else None
        assistant_message = self._messages.create(
            conversation_id=conversation_id, role=MessageRole.ASSISTANT, content=fallback_result.result.text,
            model_id=served.ref.id, provider_id=provider_id, status=MessageStatus.REAL, error=None,
        )
        self._conversations.touch(conversation_id)
        self._record_event(
            conversation_id, "conversation.message_received",
            {"model_id": served.ref.id, "history_messages_sent": used},
        )
        return user_message, assistant_message

    @staticmethod
    def _load_config() -> "ApexConfig":
        from apex.config.loader import load_config

        return load_config()

    # -- traçabilité -----------------------------------------------------------

    def _record_event(self, conversation_id: str, kind: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(
                ts=_utcnow(), level="INFO", logger="apex.conversations",
                msg=kind, context={"conversation_id": conversation_id, "kind": kind, **details},
            )

    def _record_audit(self, conversation_id: str, *, actor: str, action: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository

        self.policy.authorize(Subject.CORE, Action.WRITE_AUDIT_LOG)
        with session_scope(self._session_factory) as session:
            AuditLogRepository(session).record(
                ts=_utcnow(), actor=actor, action=action, target=conversation_id, details=details,
            )

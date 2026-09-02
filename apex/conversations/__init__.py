"""`apex.conversations` — Chat réel (passe « Première interface
utilisable »), distinct de `apex.missions` : un tour de conversation n'est
JAMAIS soumis à la gouvernance REVIEW d'ADR-0011 — il n'y a rien à valider
ou rejeter dans un échange conversationnel, contrairement à une Mission.

`ConversationService.send_message` réutilise directement
`apex.routing.select_model`/`generate_with_fallback`/`apex.models.registry.
resolve_candidates` — le même chemin réel que `apex.missions.execution.
generate_mission_response_via_router`, sans passer par le cycle de vie
`Mission` (jamais de `mark_running`/`mark_review`, aucune `MissionRow`
créée). C'est une décision d'architecture assumée, pas un oubli : un
message de Chat est une action humaine directe à réponse immédiate, une
Mission est un travail asynchrone soumis à validation humaine différée —
les confondre aurait forcé chaque tour de Chat à attendre une REVIEW.
"""

from apex.conversations.model import Conversation, Message, MessageRole, MessageStatus
from apex.conversations.service import ConversationService

__all__ = ["Conversation", "Message", "MessageRole", "MessageStatus", "ConversationService"]

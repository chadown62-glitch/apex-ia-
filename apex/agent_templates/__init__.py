"""`apex.agent_templates` — DÉFINITIONS d'agent (nom/rôle/modèle préféré/
outils déclarés), distinctes de `apex.agents` (instances RÉELLEMENT
exécutées, `AgentRuntime`). Créer/lister/supprimer un template est réel ;
le faire tourner (spawn depuis un template) reste `PLANNED` — voir
`apex.interfaces.registry`.
"""

from apex.agent_templates.model import AgentTemplate, AgentTemplateStatus
from apex.agent_templates.service import AgentTemplateService

__all__ = ["AgentTemplate", "AgentTemplateStatus", "AgentTemplateService"]

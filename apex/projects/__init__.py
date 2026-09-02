"""`apex.projects` — regroupement nommé de conversations (passe « Première
interface utilisable »). Structure MINIMALE, délibérément : pas de jalons,
pas de kanban, pas de timeline (ces aspects restent `ui-scaffold` côté
frontend, `ProjectManagerPage`) — un `Project` n'est qu'un id + un nom
auquel une `Conversation.project_id` peut se rattacher.
"""

from apex.projects.model import Project
from apex.projects.service import ProjectService

__all__ = ["Project", "ProjectService"]

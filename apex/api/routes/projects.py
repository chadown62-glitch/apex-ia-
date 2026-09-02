"""GET/POST /projects — regroupement minimal de conversations (passe
« Première interface utilisable »). Voir `apex.projects` pour le périmètre
volontairement réduit (pas de jalons/kanban/timeline).
"""

from __future__ import annotations

from fastapi import APIRouter

from apex.api.schemas import DataEnvelope, ProjectCreateIn, ProjectOut

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_project_out(project: object) -> ProjectOut:
    return ProjectOut(
        id=project.id,  # type: ignore[attr-defined]
        name=project.name,  # type: ignore[attr-defined]
        description=project.description,  # type: ignore[attr-defined]
        created_by=project.created_by,  # type: ignore[attr-defined]
        created_at=project.created_at.isoformat(),  # type: ignore[attr-defined]
    )


@router.get("", response_model=DataEnvelope[list[ProjectOut]])
def list_projects() -> DataEnvelope[list[ProjectOut]]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.projects.service import ProjectService

        projects = ProjectService(get_session_factory()).list_all()
        views = [_to_project_out(p) for p in projects]
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=views)


@router.post("", response_model=DataEnvelope[ProjectOut])
def create_project(payload: ProjectCreateIn) -> DataEnvelope[ProjectOut]:
    try:
        from apex.api.dependencies import get_session_factory
        from apex.policy.model import Subject
        from apex.projects.service import ProjectService

        project = ProjectService(get_session_factory()).create(
            name=payload.name, description=payload.description, created_by=Subject.HUMAN,
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_project_out(project))

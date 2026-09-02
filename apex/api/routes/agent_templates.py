"""GET/POST/DELETE /agent-templates — DÉFINITIONS d'agent (passe « Première
interface utilisable »), distinctes des instances réelles exposées par
`/agents` (`AgentRuntime`). Voir `apex.agent_templates` : créer/lister/
supprimer un template est réel ; le faire tourner reste `PLANNED`.
"""

from __future__ import annotations

from fastapi import APIRouter

from apex.api.schemas import AgentTemplateCreateIn, AgentTemplateOut, DataEnvelope

router = APIRouter(prefix="/agent-templates", tags=["agent-templates"])


def _to_template_out(template: object) -> AgentTemplateOut:
    return AgentTemplateOut(
        id=template.id,  # type: ignore[attr-defined]
        name=template.name,  # type: ignore[attr-defined]
        role=template.role.value,  # type: ignore[attr-defined]
        model_role=template.model_role,  # type: ignore[attr-defined]
        tools=list(template.tools),  # type: ignore[attr-defined]
        description=template.description,  # type: ignore[attr-defined]
        status=template.status.value,  # type: ignore[attr-defined]
        created_by=template.created_by,  # type: ignore[attr-defined]
        created_at=template.created_at.isoformat(),  # type: ignore[attr-defined]
    )


@router.get("", response_model=DataEnvelope[list[AgentTemplateOut]])
def list_agent_templates() -> DataEnvelope[list[AgentTemplateOut]]:
    try:
        from apex.agent_templates.service import AgentTemplateService
        from apex.api.dependencies import get_session_factory

        templates = AgentTemplateService(get_session_factory()).list_all()
        views = [_to_template_out(t) for t in templates]
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=views)


@router.post("", response_model=DataEnvelope[AgentTemplateOut])
def create_agent_template(payload: AgentTemplateCreateIn) -> DataEnvelope[AgentTemplateOut]:
    try:
        from apex.agent_templates.model import AgentRole
        from apex.agent_templates.service import AgentTemplateService
        from apex.api.dependencies import get_session_factory
        from apex.policy.model import Subject

        try:
            role = AgentRole(payload.role)
        except ValueError:
            allowed = ", ".join(sorted(r.value for r in AgentRole))
            return DataEnvelope(
                status="unknown", data=None, detail=f"rôle « {payload.role} » inconnu — valeurs autorisées : {allowed}",
            )

        template = AgentTemplateService(get_session_factory()).create(
            name=payload.name, role=role, description=payload.description, created_by=Subject.HUMAN,
            model_role=payload.model_role, tools=tuple(payload.tools),
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_template_out(template))


@router.delete("/{template_id}", response_model=DataEnvelope[bool])
def delete_agent_template(template_id: str) -> DataEnvelope[bool]:
    try:
        from apex.agent_templates.service import AgentTemplateService
        from apex.api.dependencies import get_session_factory
        from apex.policy.model import Subject

        deleted = AgentTemplateService(get_session_factory()).delete(template_id, requested_by=Subject.HUMAN)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=deleted)

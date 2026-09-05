"""GET /agents, GET /agents/{clone_id} — clones persistés (`agents`, phase 2).

Vue construite via `apex.agents.view.to_view` — aucun champ recalculé ici.
`viewer=Subject.HUMAN` par défaut : l'API exige désormais un jeton
(`apex.api.auth.require_api_token`, passe « API Authentication Hardening »)
mais ne mappe toujours pas l'appelant HTTP à un `Subject` précis (aucune
notion d'identité/rôle par requête), donc `available_actions` reflète ce
qu'un `HUMAN` peut faire, pas ce que l'appelant HTTP réel devrait pouvoir
faire. Limite explicite, pas cachée — voir le registre, entrée
ControlCenterAPI.
"""

from __future__ import annotations

from fastapi import APIRouter

from apex.api.schemas import AgentOut, DataEnvelope, MeasurementOut

router = APIRouter(prefix="/agents", tags=["agents"])


def _to_agent_out(view: object) -> AgentOut:
    return AgentOut(
        clone_id=view.clone_id,  # type: ignore[attr-defined]
        type=view.type,  # type: ignore[attr-defined]
        state=view.state,  # type: ignore[attr-defined]
        mission_id=view.mission_id,  # type: ignore[attr-defined]
        genome_ref=view.genome_ref,  # type: ignore[attr-defined]
        pool_id=view.pool_id,  # type: ignore[attr-defined]
        isolation_level=MeasurementOut(**view.isolation_level.to_dict()),  # type: ignore[attr-defined]
        created_at=view.created_at,  # type: ignore[attr-defined]
        started_at=view.started_at,  # type: ignore[attr-defined]
        terminated_at=view.terminated_at,  # type: ignore[attr-defined]
        elapsed_s=MeasurementOut(**view.elapsed_s.to_dict()),  # type: ignore[attr-defined]
        cpu_time_s=MeasurementOut(**view.cpu_time_s.to_dict()),  # type: ignore[attr-defined]
        ram_mb=MeasurementOut(**view.ram_mb.to_dict()),  # type: ignore[attr-defined]
        gpu=MeasurementOut(**view.gpu.to_dict()),  # type: ignore[attr-defined]
        retries_used=view.retries_used,  # type: ignore[attr-defined]
        max_retries=view.max_retries,  # type: ignore[attr-defined]
        last_error=view.last_error,  # type: ignore[attr-defined]
        available_actions=view.available_actions,  # type: ignore[attr-defined]
    )


@router.get("", response_model=DataEnvelope[list[AgentOut]])
def list_agents() -> DataEnvelope[list[AgentOut]]:
    try:
        from apex.agents.model import Clone
        from apex.agents.view import to_view
        from apex.api.dependencies import get_session_factory
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentRepository
        from apex.policy.engine import PolicyEngine
        from apex.policy.model import Subject

        with session_scope(get_session_factory()) as session:
            rows = AgentRepository(session).list_all()
        policy = PolicyEngine()
        views = [
            _to_agent_out(to_view(Clone.from_row(row), viewer=Subject.HUMAN, policy=policy)) for row in rows
        ]
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=views)


@router.get("/{clone_id}", response_model=DataEnvelope[AgentOut])
def get_agent(clone_id: str) -> DataEnvelope[AgentOut]:
    try:
        from apex.agents.model import Clone
        from apex.agents.view import to_view
        from apex.api.dependencies import get_session_factory
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentRepository
        from apex.policy.engine import PolicyEngine
        from apex.policy.model import Subject

        with session_scope(get_session_factory()) as session:
            row = AgentRepository(session).get(clone_id)
        if row is None:
            return DataEnvelope(status="unknown", data=None, detail=f"clone « {clone_id} » introuvable")
        view = to_view(Clone.from_row(row), viewer=Subject.HUMAN, policy=PolicyEngine())
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=_to_agent_out(view))

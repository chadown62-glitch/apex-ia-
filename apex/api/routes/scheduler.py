"""GET /scheduler — état RÉEL du Scheduler applicatif (étape 11).

Projette `apex.scheduler.service.SchedulerService.capacity()`
(`apex.scheduler.capacity.CapacityView`, composée depuis `ComputePool`
status=ACTIVE + `SchedulerPolicy.max_concurrent_missions` — jamais dérivée du
matériel de développement, voir apex.scheduler.capacity) et la taille réelle
de `ExecutionRegistry` de CE processus.

`heartbeat` reste `unknown` : arbitrage humain étape 11, point B —
`HeartbeatService` existe (étape 8) mais n'est délibérément PAS démarré ici.
Ne JAMAIS le présenter comme REAL simplement parce que le code existe.
"""

from __future__ import annotations

from fastapi import APIRouter

from apex.api.schemas import DataEnvelope, MeasurementOut, SchedulerStatusOut

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


@router.get("", response_model=DataEnvelope[SchedulerStatusOut])
def get_scheduler_status() -> DataEnvelope[SchedulerStatusOut]:
    try:
        from apex.api.dependencies import get_scheduler_service

        scheduler = get_scheduler_service()
        capacity = scheduler.capacity()
        data = SchedulerStatusOut(
            max_concurrent_missions=capacity.max_concurrent_missions,
            running_missions=capacity.running_missions,
            available_slots=capacity.available_slots,
            tracked_executions=len(scheduler.registry),
            heartbeat=MeasurementOut(value=None, status="unknown"),
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=data)

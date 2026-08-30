"""GET /gate — dernier rapport d'environnement mesuré (apex.gate, EXISTS).

`"real"` seulement si le rapport a été mesuré sur cette machine par une
sonde réelle (`measured_on_target_machine`) ; `"simulated"` sinon ;
`"unknown"` si aucun rapport n'existe — jamais un statut inventé.
"""

from __future__ import annotations

from fastapi import APIRouter

from apex.api.schemas import DataEnvelope, GateCheckOut, GateOut
from apex.gate.contract import EnvironmentUnknown, load_environment

router = APIRouter(prefix="/gate", tags=["gate"])


@router.get("", response_model=DataEnvelope[GateOut])
def get_gate() -> DataEnvelope[GateOut]:
    try:
        environment = load_environment()
    except EnvironmentUnknown as exc:
        return DataEnvelope(status="unknown", data=None, detail=str(exc))

    raw = environment.raw
    checks = [
        GateCheckOut(
            check_id=c.get("check_id", ""),
            title=c.get("title", ""),
            status=c.get("status", "UNKNOWN"),
            message=c.get("message", ""),
        )
        for c in raw.get("checks", [])
    ]
    out = GateOut(
        gate_status=raw.get("gate_status", "UNKNOWN"),
        phase_2_authorized=bool(raw.get("phase_2_authorized", False)),
        machine_label=raw.get("machine_label"),
        fingerprint=raw.get("fingerprint", ""),
        measured_on_target_machine=environment.measured_on_target,
        checks=checks,
    )
    status = "real" if environment.measured_on_target else "simulated"
    return DataEnvelope(status=status, data=out)

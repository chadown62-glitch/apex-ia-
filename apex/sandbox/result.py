"""Résultat d'une exécution sandboxée — chaque mesure porte son statut
REAL/UNKNOWN (`apex.obs.status.Measurement`), jamais une estimation. Une
valeur non mesurable sur le mécanisme actuellement disponible (ex. PID
peak sans `pids.peak`) reste UNKNOWN plutôt que devinée.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apex.obs.status import Measurement
from apex.sandbox.model import ExecutionStatus, IsolationLevel


@dataclass(frozen=True)
class SandboxResult:
    mission_id: str
    clone_id: str
    status: ExecutionStatus
    isolation_level: IsolationLevel
    exit_code: Measurement  # int | None
    signal: Measurement  # int | None
    elapsed_s: Measurement  # float
    cpu_time_s: Measurement  # float | None
    memory_peak_mb: Measurement  # float | None
    pids_peak: Measurement  # int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    error_detail: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "clone_id": self.clone_id,
            "status": self.status.value,
            "isolation_level": self.isolation_level.value,
            "exit_code": self.exit_code.to_dict(),
            "signal": self.signal.to_dict(),
            "elapsed_s": self.elapsed_s.to_dict(),
            "cpu_time_s": self.cpu_time_s.to_dict(),
            "memory_peak_mb": self.memory_peak_mb.to_dict(),
            "pids_peak": self.pids_peak.to_dict(),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "error_detail": self.error_detail,
            "facts": dict(self.facts),
        }

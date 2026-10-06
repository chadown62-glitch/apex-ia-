"""Pont entre les outils d'exécution et le runtime contrôlé : chaque exécution passe par
`AgentRuntime.spawn` puis `AgentRuntime.execute_in_sandbox` (autorisation PolicyEngine `EXECUTE_IN_SANDBOX`,
clone tracé, journal et audit), jamais par un appel direct au backend de sandbox.

Les limites sont celles du runtime (`ResourceLimits` de la configuration), le délai étant le plus petit du délai
configuré et de celui de l'outil ; le réseau est TOUJOURS coupé (`network="none"`), quel que soit l'appelant.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from apex.agents.runtime import AgentRuntime
from apex.config.schema import ComputePool
from apex.policy.model import Subject
from apex.sandbox.model import CancellationToken, ResourceLimits, SandboxSpec
from apex.sandbox.provider import SandboxProvider


def sandbox_runner(
    *, agent_runtime: AgentRuntime, sandbox: SandboxProvider, pool: ComputePool, base_limits: ResourceLimits,
    requested_by: Subject, clone_subject: Subject = Subject.RESEARCH, label: str = "tool",
    cancel_token: CancellationToken | None = None,
) -> Callable[[tuple[str, ...], tuple[tuple[str, bytes], ...], float], Any]:
    def run(command: tuple[str, ...], files: tuple[tuple[str, bytes], ...], timeout_s: float) -> Any:
        limits = replace(base_limits, timeout_s=min(base_limits.timeout_s, float(timeout_s)), network="none")
        execution_id = str(uuid.uuid4())
        clone = agent_runtime.spawn(
            clone_subject, pool=pool, genome_ref=f"{label}:{execution_id}", spawned_by=requested_by,
            mission_id=None,
        )
        spec = SandboxSpec(
            mission_id=execution_id, clone_id=clone.clone_id, command=tuple(command), limits=limits,
            input_files=files,
        )
        return agent_runtime.execute_in_sandbox(
            clone, sandbox, spec, requested_by=requested_by, cancel_token=cancel_token,
        )

    return run

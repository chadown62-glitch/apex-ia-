"""`ExitCodeOracle` — le seul `OracleProvider` réel de cette étape.

Juge une tentative par le seul `ExecutionStatus` du `SandboxResult` (le code
de sortie du processus a déjà été classifié par `LinuxNamespaceSandbox`, voir
`apex/sandbox/linux_namespace.py::_classify_status` : SUCCESS <=> exit
code 0, FAILED <=> exit code non nul et processus réellement terminé). Ce
mapping couvre exactement les cinq catégories exigées (GO explicite Phase 5) :

- SUCCESS -> PASS (la commande a réellement retourné 0)
- FAILED -> FAIL (la commande a réellement terminé avec un code non nul)
- TIMEOUT -> TIMEOUT
- CANCELLED -> UNKNOWN (interrompu pour une raison externe à la tâche —
  jamais un FAIL deviné)
- OOM_KILLED / RESOURCE_LIMIT_EXCEEDED / PROCESS_LIMIT_EXCEEDED /
  DISK_LIMIT_EXCEEDED / NETWORK_DENIED / PERMISSION_DENIED -> ERROR (échec de
  l'INFRASTRUCTURE d'exécution, jamais confondu avec une réponse incorrecte
  de la tâche elle-même)

N'inspecte PAS `task` : ce jugement ne dépend d'aucune donnée de la tâche,
uniquement du résultat sandbox. Un futur oracle plus riche (comparaison de
sortie attendue, ...) l'utiliserait — voir `apex.oracles.provider`.
"""

from __future__ import annotations

from apex.oracles.model import OracleResult, OracleVerdict
from apex.oracles.provider import OracleProvider
from apex.sandbox.model import ExecutionStatus
from apex.sandbox.result import SandboxResult
from apex.tasks.model import Task

_ERROR_STATUSES = frozenset(
    {
        ExecutionStatus.OOM_KILLED,
        ExecutionStatus.RESOURCE_LIMIT_EXCEEDED,
        ExecutionStatus.PROCESS_LIMIT_EXCEEDED,
        ExecutionStatus.DISK_LIMIT_EXCEEDED,
        ExecutionStatus.NETWORK_DENIED,
        ExecutionStatus.PERMISSION_DENIED,
    }
)


class ExitCodeOracle(OracleProvider):
    def judge(self, *, sandbox_result: SandboxResult, task: Task) -> OracleResult:
        status = sandbox_result.status
        if status is ExecutionStatus.SUCCESS:
            return OracleResult(
                verdict=OracleVerdict.PASS,
                detail=f"code de sortie {sandbox_result.exit_code.value!r} (réel)",
            )
        if status is ExecutionStatus.FAILED:
            return OracleResult(
                verdict=OracleVerdict.FAIL,
                detail=f"code de sortie {sandbox_result.exit_code.value!r} (réel, non nul)",
            )
        if status is ExecutionStatus.TIMEOUT:
            return OracleResult(verdict=OracleVerdict.TIMEOUT, detail="délai imparti dépassé")
        if status is ExecutionStatus.CANCELLED:
            return OracleResult(
                verdict=OracleVerdict.UNKNOWN,
                detail="exécution annulée avant complétion — résultat non évaluable",
            )
        if status in _ERROR_STATUSES:
            return OracleResult(
                verdict=OracleVerdict.ERROR,
                detail=f"échec de l'infrastructure d'exécution ({status.value})"
                + (f" — {sandbox_result.error_detail}" if sandbox_result.error_detail else ""),
            )
        # Vocabulaire ExecutionStatus fermé (Enum) : cette branche n'est
        # atteignable que si une nouvelle valeur est ajoutée sans mise à
        # jour de cet oracle — jamais un verdict deviné dans ce cas.
        return OracleResult(
            verdict=OracleVerdict.UNKNOWN,
            detail=f"statut d'exécution non reconnu par cet oracle ({status.value})",
        )

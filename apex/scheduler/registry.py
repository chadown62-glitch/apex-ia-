"""ExecutionRegistry — état en mémoire, NON persisté, des exécutions
réellement en cours dans CETTE instance de `SchedulerService`.

C'est la seule vue de « quelles sandboxes tournent réellement, maintenant,
supervisées par CE processus ». Elle sert trois usages :

1. Éviter de soumettre deux fois la même mission tant qu'une soumission
   précédente n'est pas résolue (`SchedulerService.tick`).
2. Retrouver le `CancellationToken` d'une mission pour l'annuler réellement
   (`SchedulerService.cancel_mission`) plutôt que de ne changer qu'un champ
   en base.
3. Être structurellement VIDE à l'instanciation — c'est exactement ce qui
   rend `apex.scheduler.recovery.RecoveryService` correct : aucune ligne
   `missions.state = 'running'` trouvée en base à ce moment ne peut avoir
   d'entrée correspondante ici, donc toute ligne de ce genre est orpheline
   par construction, pas par hypothèse.
"""

from __future__ import annotations

import threading

from apex.scheduler.model import ExecutionHandle


class ExecutionRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handles: dict[str, ExecutionHandle] = {}

    def track(self, handle: ExecutionHandle) -> None:
        with self._lock:
            self._handles[handle.mission_id] = handle

    def get(self, mission_id: str) -> ExecutionHandle | None:
        with self._lock:
            return self._handles.get(mission_id)

    def discard(self, mission_id: str) -> None:
        with self._lock:
            self._handles.pop(mission_id, None)

    def __contains__(self, mission_id: str) -> bool:
        with self._lock:
            return mission_id in self._handles

    def __len__(self) -> int:
        with self._lock:
            return len(self._handles)

    def snapshot(self) -> tuple[ExecutionHandle, ...]:
        with self._lock:
            return tuple(self._handles.values())

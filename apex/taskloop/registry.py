"""`TaskLoopRegistry` — état en mémoire, NON persisté, des `TaskLoop`
réellement en cours dans CETTE instance de processus. Même patron,
délibérément, qu'`apex.scheduler.registry.ExecutionRegistry` (N+8) : une
vue en mémoire des exécutions supervisées, jamais une seconde source de
vérité persistée.

**Limite honnête, comme `apex.errors.CycleAlreadyRunning`** : cette garantie
d'idempotence est PAR-PROCESSUS uniquement (contrairement à `apex.errors.
LearningSessionAlreadyRunning`, garantie par un index UNIQUE en base) —
deux processus API distincts pourraient toujours démarrer indépendamment un
`TaskLoop` pour la même `task_id`. Documenté ici, jamais résolu
silencieusement : une garantie inter-processus exigerait une table dédiée,
hors périmètre de cette mission (§« Resume/Recovery » : « implémente
uniquement le minimum nécessaire »)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from apex.errors import TaskAlreadyRunning, TaskLoopAtCapacity
from apex.sandbox import CancellationToken


@dataclass(frozen=True)
class TaskLoopHandle:
    task_id: str
    trace_id: str
    cancel_token: CancellationToken = field(default_factory=CancellationToken)


class TaskLoopRegistry:
    def __init__(self, *, max_concurrent: int = 4) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent doit être >= 1")
        self._lock = threading.Lock()
        self._handles: dict[str, TaskLoopHandle] = {}
        self._max_concurrent = max_concurrent

    def track(self, handle: TaskLoopHandle) -> None:
        """Lève `TaskAlreadyRunning` si `handle.task_id` est déjà suivi —
        jamais un remplacement silencieux d'un suivi existant. Lève
        `TaskLoopAtCapacity` si `max_concurrent` est déjà atteint — vérifié
        AVANT l'unicité (une capacité pleine reste une capacité pleine,
        indépendamment de savoir si CETTE tâche précise tournait déjà)."""
        with self._lock:
            if len(self._handles) >= self._max_concurrent and handle.task_id not in self._handles:
                raise TaskLoopAtCapacity(max_concurrent=self._max_concurrent)
            if handle.task_id in self._handles:
                raise TaskAlreadyRunning(handle.task_id)
            self._handles[handle.task_id] = handle

    def get(self, task_id: str) -> TaskLoopHandle | None:
        with self._lock:
            return self._handles.get(task_id)

    def discard(self, task_id: str) -> None:
        with self._lock:
            self._handles.pop(task_id, None)

    def __contains__(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._handles

    def __len__(self) -> int:
        with self._lock:
            return len(self._handles)

    def snapshot(self) -> tuple[TaskLoopHandle, ...]:
        with self._lock:
            return tuple(self._handles.values())


#: Registre PAR DÉFAUT — même patron que `apex.sandbox.provider.
#: get_default_provider`/`apex.oracles.provider.get_default_oracle` : un
#: SEUL point de construction implicite, réutilisé par tout appelant de
#: `apex.taskloop.loop.run_task_loop` qui ne fournit pas explicitement son
#: propre `TaskLoopRegistry` (ex. pour des tests isolés).
_DEFAULT_REGISTRY = TaskLoopRegistry()


def default_registry() -> TaskLoopRegistry:
    return _DEFAULT_REGISTRY

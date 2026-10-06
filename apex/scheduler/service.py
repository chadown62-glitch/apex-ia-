"""SchedulerService — décide QUAND lancer une mission QUEUED, jamais COMMENT.

`tick()` n'appelle jamais `AgentRuntime`/`SandboxProvider` directement : le
seul point d'entrée est `apex.missions.execution.launch_mission`, déjà
audité (étape 7), qui applique lui-même la chaîne complète

    CORE -> POLICY -> AGENT RUNTIME -> SANDBOX -> MISSION -> RESULT -> REVIEW

Ce module ne duplique ni ne contourne AUCUNE vérification de PolicyEngine :
`launch_mission` autorise déjà `LAUNCH_MISSION`/`EXECUTE_IN_SANDBOX` (CORE
seul), `MissionService.cancel` autorise déjà `CANCEL_MISSION` — le Scheduler
se contente d'appeler ces points d'entrée existants, toujours en tant que
`Subject.CORE`, jamais un sujet exposé à l'extérieur.

**Pas de retry automatique** (consigne étape 8) : un échec de mission reste
`FAILED`, terminal, jusqu'à une décision humaine explicite. **Pas de
résolution automatique d'agent** : une mission sans `assigned_agent` reste
`QUEUED`, jamais assignée à un clone choisi par le Scheduler.

**Limite honnête sur la concurrence multi-instances** : `max_concurrent_missions`
borne le nombre d'exécutions que CETTE instance accepte de suivre
(`ExecutionRegistry`, en mémoire, borne aussi le pool de threads — donc
aucune croissance illimitée de threads). La CORRECTION (une mission ne peut
jamais être lancée deux fois) est garantie GLOBALEMENT, entre plusieurs
instances, par la réclamation atomique en base
(`apex.missions.service.MissionService.mark_running`) — mais la SOMME des
capacités de plusieurs instances simultanées n'est pas aujourd'hui bornée
par un compteur global partagé ; chaque instance applique sa propre borne
locale. Documenté ici plutôt que résolu silencieusement.
"""

from __future__ import annotations

import concurrent.futures
import time
from datetime import datetime, timezone
from typing import Any

from apex.agents.runtime import AgentRuntime
from apex.config.schema import ApexConfig
from apex.missions.model import Mission, MissionPriority
from apex.missions.execution import launch_mission, run_mission_task
from apex.missions.service import MissionService
from apex.missions.states import InvalidMissionTransition
from apex.policy.model import Subject
from apex.sandbox import SandboxProvider
from apex.scheduler.capacity import CapacityView, build_capacity_view
from apex.scheduler.model import ExecutionHandle, SchedulerTickResult
from apex.scheduler.registry import ExecutionRegistry

_PRIORITY_RANK: dict[MissionPriority, int] = {
    MissionPriority.LOW: 0,
    MissionPriority.NORMAL: 1,
    MissionPriority.HIGH: 2,
    MissionPriority.CRITICAL: 3,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SchedulerService:
    def __init__(
        self,
        session_factory: Any,
        *,
        mission_service: MissionService,
        agent_runtime: AgentRuntime,
        sandbox: SandboxProvider | None = None,
        config: ApexConfig | None = None,
        max_concurrent_missions: int | None = None,
        registry: ExecutionRegistry | None = None,
        cancel_wait_timeout_s: float = 5.0,
        task_run_service: Any | None = None,
    ) -> None:
        from apex.config.loader import load_config

        self._session_factory = session_factory
        self._mission_service = mission_service
        self._agent_runtime = agent_runtime
        self._sandbox = sandbox
        self._config = config or load_config()
        self._max_concurrent = (
            max_concurrent_missions
            if max_concurrent_missions is not None
            else self._config.scheduler.max_concurrent_missions
        )
        self._registry = registry or ExecutionRegistry()
        self._cancel_wait_timeout_s = cancel_wait_timeout_s
        #: Chemin autonome canonique des missions à Task vérifiable (`apex.missions.execution.run_mission_task`).
        self._task_run_service = task_run_service
        # Borné une fois pour toutes : quel que soit le nombre de tâches
        # soumises, jamais plus de `max_workers` threads n'existent — c'est
        # la garantie structurelle "aucune croissance illimitée de threads"
        # exigée par la consigne étape 8, pas une convention à respecter.
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self._max_concurrent, thread_name_prefix="apex-scheduler",
        )

    @property
    def registry(self) -> ExecutionRegistry:
        """Lecture seule, exposée pour `apex.scheduler.heartbeat.HeartbeatService`
        (et un futur Control Center) — la Scheduler décide QUAND lancer, le
        Heartbeat prouve QUE ça vit encore : deux responsabilités séparées
        qui partagent la même vue des exécutions suivies, pas fusionnées
        dans une seule classe."""
        return self._registry

    # -- capacité -------------------------------------------------------

    def capacity(self) -> CapacityView:
        return build_capacity_view(self._config, running_missions=self._mission_service.count_running())

    # -- tick -------------------------------------------------------------

    def tick(self) -> SchedulerTickResult:
        """Une itération : sélectionne les missions QUEUED éligibles (triées
        priorité puis FIFO, dépendances satisfaites), en soumet autant que
        la capacité locale le permet. Ne bloque jamais sur une exécution en
        cours — chaque lancement retenu est soumis au pool de threads borné
        et suivi dans `ExecutionRegistry`, retiré à sa complétion."""
        submitted: list[str] = []
        skipped_no_capacity: list[str] = []
        skipped_no_agent: list[str] = []
        skipped_no_task_runner: list[str] = []

        eligible, blocked = self._eligible_queued_missions()
        for mission in eligible:
            if mission.id in self._registry:
                continue  # déjà soumise, en attente d'un slot ou en cours
            if mission.task_id is not None:
                # Mission à Task vérifiable : le TaskLoop crée ses propres clones, aucun agent n'est requis.
                if self._task_run_service is None:
                    skipped_no_task_runner.append(mission.id)
                    continue
            elif mission.assigned_agent is None:
                # Étape 8 : aucune résolution automatique d'agent — la
                # mission reste QUEUED, visible, jamais perdue.
                skipped_no_agent.append(mission.id)
                continue
            if len(self._registry) >= self._max_concurrent:
                skipped_no_capacity.append(mission.id)
                continue
            handle = ExecutionHandle(
                mission_id=mission.id, clone_id=mission.assigned_agent or "", started_at=_utcnow(),
            )
            self._registry.track(handle)
            self._executor.submit(self._launch_and_track, mission, handle)
            submitted.append(mission.id)

        return SchedulerTickResult(
            submitted=tuple(submitted), blocked=tuple(blocked),
            skipped_no_capacity=tuple(skipped_no_capacity), skipped_no_agent=tuple(skipped_no_agent),
            skipped_no_task_runner=tuple(skipped_no_task_runner),
        )

    def _eligible_queued_missions(self) -> tuple[list[Mission], list[str]]:
        queued = self._mission_service.list_queued()
        eligible: list[Mission] = []
        blocked: list[str] = []
        for mission in queued:
            unmet = self._unmet_dependencies(mission)
            if unmet:
                blocked.append(mission.id)
                self._record_scheduler_event(
                    mission.id, "scheduler.mission_blocked", {"unmet_dependencies": unmet},
                )
                continue
            eligible.append(mission)
        eligible.sort(key=lambda m: (-_PRIORITY_RANK[m.priority], m.created_at))
        return eligible, blocked

    def _unmet_dependencies(self, mission: Mission) -> list[str]:
        unmet: list[str] = []
        for dep_id in mission.depends_on:
            from apex.missions.states import MissionState

            dep = self._mission_service.get(dep_id)
            if dep is None or dep.state is not MissionState.COMPLETED:
                unmet.append(dep_id)
        return unmet

    def _launch_and_track(self, mission: Mission, handle: ExecutionHandle) -> None:
        try:
            if mission.task_id is not None:
                result_mission = run_mission_task(
                    mission.id, mission_service=self._mission_service, task_run_service=self._task_run_service,
                    requested_by=Subject.CORE, on_task_run_started=lambda run_id: setattr(handle, "task_run_id", run_id),
                )
            else:
                result_mission = launch_mission(
                    mission.id, session_factory=self._session_factory,
                    mission_service=self._mission_service, agent_runtime=self._agent_runtime,
                    sandbox=self._sandbox, cancel_token=handle.cancel_token, requested_by=Subject.CORE,
                )
            self._record_scheduler_event(
                mission.id, "scheduler.mission_finished", {"state": result_mission.state.value},
            )
        except InvalidMissionTransition as exc:
            # Course perdue face à un autre worker/instance : bénin, pas une
            # erreur — la réclamation atomique (MissionService.mark_running)
            # a simplement tranché en faveur d'un autre appelant.
            self._record_scheduler_event(mission.id, "scheduler.claim_lost", {"reason": str(exc)})
        except Exception as exc:  # le Scheduler ne doit jamais planter le Core
            self._record_scheduler_event(
                mission.id, "scheduler.launch_error", {"reason": f"{exc.__class__.__name__}: {exc}"},
            )
        finally:
            self._registry.discard(mission.id)

    # -- annulation réelle ------------------------------------------------

    def cancel_mission(self, mission_id: str, *, requested_by: Subject) -> Mission:
        """Annule réellement une mission. Si une exécution est suivie pour
        cette mission (dans CE processus), demande sa terminaison réelle
        (`os.killpg`, via le `CancellationToken` — voir
        `apex.sandbox.linux_namespace`) et VÉRIFIE que le groupe de
        processus est effectivement mort avant de considérer l'annulation
        traitée — jamais une confiance aveugle dans le signal envoyé.

        Autorisation : entièrement déléguée à
        `MissionService.cancel` (`Action.CANCEL_MISSION`) — aucune
        vérification dupliquée ici.
        """
        handle = self._registry.get(mission_id)
        process_group_verified_terminated: bool | None = None
        if handle is not None and handle.task_run_id is not None and self._task_run_service is not None:
            # Mission exécutée par le TaskLoop : la mission est annulée D'ABORD (l'enveloppe voit alors
            # CANCELLED et n'écrit rien par-dessus), puis la TaskRun est réellement arrêtée et on CONSTATE
            # qu'elle s'est terminée — jamais une confiance aveugle dans la demande.
            try:
                mission = self._mission_service.cancel(mission_id, requested_by=requested_by)
            except InvalidMissionTransition:
                mission = self._mission_service.get(mission_id)
                if mission is None:
                    raise
            self._task_run_service.cancel(handle.task_run_id)
            finished = self._task_run_service.wait(handle.task_run_id, timeout_s=self._cancel_wait_timeout_s)
            terminated = finished is not None and finished.status.terminal
            self._record_scheduler_event(
                mission_id, "scheduler.cancel_requested",
                {"had_tracked_execution": True, "task_run_id": handle.task_run_id,
                 "task_run_verified_terminated": terminated, "requested_by": requested_by.value},
            )
            return mission
        if handle is not None:
            handle.cancel_token.request_cancel()
            process_group_verified_terminated = handle.cancel_token.wait_until_terminated(
                timeout_s=self._cancel_wait_timeout_s,
            )
            self._registry.discard(mission_id)

        try:
            mission = self._mission_service.cancel(mission_id, requested_by=requested_by)
        except InvalidMissionTransition:
            # La mission s'est terminée (ou a déjà été annulée) avant que
            # cette annulation ne prenne effet — un résultat légitime, pas
            # une erreur à propager : on rapporte l'état réel, tel quel.
            mission = self._mission_service.get(mission_id)
            if mission is None:
                raise

        self._record_scheduler_event(
            mission_id, "scheduler.cancel_requested",
            {
                "had_tracked_execution": handle is not None,
                "process_group_verified_terminated": process_group_verified_terminated,
                "requested_by": requested_by.value,
            },
        )
        return mission

    # -- cycle de vie du Scheduler lui-même --------------------------------

    def wait_for_idle(self, *, timeout_s: float = 30.0, poll_interval_s: float = 0.05) -> bool:
        """Bloque jusqu'à ce qu'aucune exécution ne soit plus suivie, ou
        jusqu'à `timeout_s`. Utilitaire pour les tests/opérations — jamais
        appelé par `tick()` lui-même, qui reste non bloquant."""
        deadline = time.monotonic() + timeout_s
        while len(self._registry) > 0 and time.monotonic() < deadline:
            time.sleep(poll_interval_s)
        return len(self._registry) == 0

    def wait_for_mission(
        self, mission_id: str, *, timeout_s: float = 30.0, poll_interval_s: float = 0.05,
    ) -> bool:
        """Bloque jusqu'à ce que `mission_id` précisément ne soit plus suivie
        (`_launch_and_track` l'a retirée après avoir atteint REVIEW/FAILED, ou
        après une réclamation perdue face à un autre worker/instance), ou
        jusqu'à `timeout_s`. Même patron que `wait_for_idle`, restreint à UNE
        mission — étape 11, utilisé par `POST /missions/{id}/launch` pour
        rendre une réponse HTTP compatible avec le comportement précédent sans
        bloquer sur les AUTRES missions qu'un même `tick()` a pu soumettre.

        Retourne True si le suivi a bien été retiré avant l'échéance, False en
        cas de timeout — dans les deux cas, ce n'est PAS une garantie que la
        mission a atteint un état terminal humain (REVIEW attend encore
        validate/reject) : seulement que CE Scheduler ne la suit plus
        activement. Si `mission_id` n'était déjà pas suivie au moment de
        l'appel (jamais soumise, ou déjà retirée), retourne True immédiatement
        — pas une erreur, juste rien à attendre."""
        deadline = time.monotonic() + timeout_s
        while mission_id in self._registry and time.monotonic() < deadline:
            time.sleep(poll_interval_s)
        return mission_id not in self._registry

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)

    # -- internes -----------------------------------------------------------

    def _record_scheduler_event(self, mission_id: str, kind: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(
                ts=_utcnow(), level="INFO", logger="apex.scheduler",
                msg=kind, context={"mission_id": mission_id, "kind": kind, **details},
            )

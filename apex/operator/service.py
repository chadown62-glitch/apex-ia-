"""OperatorService — couche de contrôle centrale d'APEX (N+16).

Ne réimplémente ni le Task System (`apex.missions`) ni l'orchestrateur
(`apex.scheduler`) : délègue entièrement, et ajoute UNIQUEMENT deux
responsabilités qui n'existaient nulle part ailleurs avant N+16.

1. Un lifecycle explicite et observable (STOPPED/STARTING/READY/RUNNING/
   PAUSED/STOPPING/FAULTED, voir `apex.operator.states`) — jusqu'ici,
   `SchedulerService` existait mais rien ne décidait QUAND il devait
   réellement tourner en continu. `apex.interfaces.registry` (entrée
   "Scheduler") documente explicitement cette absence comme un arbitrage
   humain de l'étape 11 : « aucune boucle de fond autonome ... une boucle
   permanente reste PLANNED, pourra être ajoutée sans modifier ce module ».
   C'est cette boucle qu'`OperatorService._supervise` ajoute — jamais dans
   `SchedulerService` lui-même, qui reste inchangé.
2. Une façade UNIQUE pour Start/Stop/Pause/Resume/Status/SubmitTask/
   CancelTask/GetTaskStatus/GetTaskResult/GetRecentOperations — consommable
   identiquement par une future UI, une API locale, ou un client, sans
   qu'aucun appelant n'ait besoin de connaître `MissionService` ou
   `SchedulerService` directement.

Un « Task » au sens Operator EST une `apex.missions.model.Mission` — jamais
un second concept parallèle. Toute autorisation passe par `PolicyEngine`,
jamais dupliquée/contournée : les opérations de lifecycle exigent
`Subject.HUMAN` (voir `apex.policy.engine.DEFAULT_ALLOWLIST`), les
opérations de tâche délèguent leur propre autorisation à
`MissionService`/`SchedulerService`, déjà auditées.

Singleton applicatif — même discipline que `SchedulerService`
(`apex.api.dependencies.get_operator_service`) : ce service porte un état
(le thread de supervision, le lifecycle lui-même) qui doit rester LE MÊME
objet entre deux appels/requêtes HTTP.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from apex.design.model import DesignPackage
from apex.design.service import DesignService
from apex.errors import ApexError
from apex.missions.model import Mission, MissionPriority
from apex.missions.service import MissionService
from apex.missions.states import MissionState
from apex.operator.model import OperatorStatus, TaskResult
from apex.operator.states import OperatorState, assert_transition_allowed
from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject
from apex.scheduler.service import SchedulerService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OperatorNotReady(ApexError):
    """APEX n'est pas dans un état qui accepte cette opération (ex. soumettre
    une tâche alors que STOPPED/STARTING/STOPPING/FAULTED) — jamais rattrapée
    silencieusement, jamais un raccourci qui contournerait la machine à états."""

    exit_code = 14

    def __init__(self, operation: str, *, current_state: OperatorState) -> None:
        self.operation = operation
        self.current_state = current_state
        super().__init__(
            f"« {operation} » impossible : APEX est « {current_state.value} », "
            "pas prêt à accepter cette opération"
        )


class OperatorService:
    def __init__(
        self,
        session_factory: Any,
        *,
        scheduler: SchedulerService,
        mission_service: MissionService,
        design_service: DesignService | None = None,
        policy: PolicyEngine | None = None,
        tick_interval_s: float = 1.0,
    ) -> None:
        self._session_factory = session_factory
        self._scheduler = scheduler
        self._mission_service = mission_service
        # N+17 : optionnel — un `OperatorService` construit sans Design Engine
        # (ex. un ancien test N+16 non modifié) reste entièrement fonctionnel
        # pour Start/Stop/Pause/Resume/SubmitTask ; seules les méthodes
        # `submit_design`/`get_design_*`/`resume_design` exigent qu'il soit
        # fourni (voir `_require_design_service`).
        self._design_service = design_service
        self.policy = policy or PolicyEngine()
        self._tick_interval_s = tick_interval_s

        self._lock = threading.RLock()
        self._state = OperatorState.STOPPED
        self._since = _utcnow()
        self._last_error: str | None = None
        self._supervisor_thread: threading.Thread | None = None
        self._stop_requested = threading.Event()
        self._paused = threading.Event()  # set == pause en effet
        # `SchedulerService.tick()` n'est pas conçu pour être appelé par
        # PLUSIEURS threads en même temps (voir sa docstring : « une
        # itération ») — le thread de supervision ET `submit_task` (tentative
        # immédiate d'ordonnancement) peuvent tous deux vouloir tick() au même
        # instant. Sans ce verrou, deux tick() concurrents peuvent tous deux
        # réclamer la même mission avant que l'un ou l'autre ne l'ait suivie
        # dans `ExecutionRegistry`, et le perdant peut alors retirer
        # prématurément (dans son propre `finally`) l'entrée du GAGNANT
        # encore en cours d'exécution — constaté empiriquement sous ce test
        # de concurrence réelle. Jamais un problème de `SchedulerService`
        # lui-même (jamais appelé par plusieurs threads dans ses propres
        # tests) : la sérialisation appartient à l'appelant qui en introduit
        # un second, donc ici, jamais dans `SchedulerService`.
        self._tick_lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------------

    def start(self, *, requested_by: Subject = Subject.HUMAN) -> OperatorStatus:
        self.policy.authorize(requested_by, Action.START_OPERATOR)
        with self._lock:
            assert_transition_allowed(self._state, OperatorState.STARTING)
            self._transition(OperatorState.STARTING)

        try:
            # La réconciliation au démarrage (missions RUNNING orphelines)
            # est déjà appliquée par la construction du SchedulerService
            # lui-même (voir apex.api.dependencies.get_scheduler_service /
            # apex.scheduler.recovery.RecoveryService) — jamais dupliquée
            # ici. On vérifie seulement que le Scheduler répond réellement
            # (capacité lisible => Runtime/DB accessibles).
            self._scheduler.capacity()
        except Exception as exc:
            detail = f"{exc.__class__.__name__}: {exc}"
            with self._lock:
                self._last_error = detail
                self._transition(OperatorState.FAULTED)
            self._safe_record_operation("operator.start_failed", {"error": detail})
            raise

        self._stop_requested.clear()
        self._paused.clear()
        with self._lock:
            self._last_error = None
            self._transition(OperatorState.READY)
        self._supervisor_thread = threading.Thread(
            target=self._supervise, name="apex-operator-supervisor", daemon=True,
        )
        self._supervisor_thread.start()
        self._safe_record_operation("operator.started", {"requested_by": requested_by.value})
        return self.status()

    def stop(self, *, requested_by: Subject = Subject.HUMAN, timeout_s: float = 30.0) -> OperatorStatus:
        self.policy.authorize(requested_by, Action.STOP_OPERATOR)
        with self._lock:
            assert_transition_allowed(self._state, OperatorState.STOPPING)
            self._transition(OperatorState.STOPPING)

        self._stop_requested.set()
        thread = self._supervisor_thread
        if thread is not None:
            thread.join(timeout=timeout_s)

        idle = self._scheduler.wait_for_idle(timeout_s=timeout_s)
        with self._lock:
            if not idle:
                self._last_error = "des tâches sont restées actives au-delà du délai d'arrêt"
                self._transition(OperatorState.FAULTED)
                self._safe_record_operation("operator.stop_failed", {"error": self._last_error})
                return self.status()
            self._transition(OperatorState.STOPPED)
        self._safe_record_operation("operator.stopped", {"requested_by": requested_by.value})
        return self.status()

    def pause(self, *, requested_by: Subject = Subject.HUMAN) -> OperatorStatus:
        """Suspend le SEUL déclenchement de NOUVELLES tâches (le thread de
        supervision cesse d'appeler `tick()`) — une tâche déjà RUNNING (déjà
        suivie par `SchedulerService`) continue jusqu'à son terme, jamais
        interrompue par une pause (ce serait une annulation déguisée — voir
        `cancel_task` pour la vraie annulation)."""
        self.policy.authorize(requested_by, Action.PAUSE_OPERATOR)
        with self._lock:
            assert_transition_allowed(self._state, OperatorState.PAUSED)
            self._paused.set()
            self._transition(OperatorState.PAUSED)
        self._safe_record_operation("operator.paused", {"requested_by": requested_by.value})
        return self.status()

    def resume(self, *, requested_by: Subject = Subject.HUMAN) -> OperatorStatus:
        self.policy.authorize(requested_by, Action.RESUME_OPERATOR)
        with self._lock:
            assert_transition_allowed(self._state, OperatorState.READY)
            self._paused.clear()
            self._transition(OperatorState.READY)
        self._safe_record_operation("operator.resumed", {"requested_by": requested_by.value})
        return self.status()

    def status(self) -> OperatorStatus:
        """Toujours retourne un résultat, même si le Scheduler/la DB sont
        indisponibles (ex. juste après un `start()` en échec, état FAULTED) —
        une observabilité qui échouerait précisément quand elle est le plus
        nécessaire (diagnostiquer une panne) serait pire qu'inutile. Les
        champs dépendant du Scheduler dégradent à des valeurs neutres plutôt
        que de propager l'exception."""
        with self._lock:
            state = self._state
            since = self._since
            last_error = self._last_error

        active: tuple[str, ...] = ()
        queued_count = 0
        max_concurrent = 0
        try:
            active = tuple(h.mission_id for h in self._scheduler.registry.snapshot())
            queued_count = len(self._mission_service.list_queued())
            max_concurrent = self._scheduler.capacity().max_concurrent_missions
        except Exception:
            pass

        return OperatorStatus(
            state=state, since=since, active_task_ids=active,
            queued_task_count=queued_count, max_concurrent_tasks=max_concurrent,
            last_error=last_error,
        )

    # -- tâches -----------------------------------------------------------------

    def submit_task(
        self,
        *,
        title: str,
        description: str,
        specialty: str,
        objective: str,
        requested_by: Subject = Subject.HUMAN,
        priority: MissionPriority = MissionPriority.NORMAL,
        constraints: str | None = None,
        assigned_agent: str | None = None,
        agent_id: str | None = None,
        depends_on: tuple[str, ...] = (),
    ) -> Mission:
        """Crée une tâche PUIS tente immédiatement de l'ordonnancer (même
        combinaison que `POST /missions` + `POST /missions/{id}/launch`
        aujourd'hui, réunie en un seul appel cohérent pour un futur client qui
        ne devrait pas avoir à connaître cette distinction). Ne force rien :
        capacité insuffisante, absence d'agent, ou pause en cours laisse la
        tâche QUEUED, jamais perdue — voir `apex.missions.service.
        MissionService.create`/`apex.scheduler.service.SchedulerService.tick`."""
        self._require_accepts_submissions("submit_task")
        mission = self._mission_service.create(
            title=title, description=description, specialty=specialty, objective=objective,
            created_by=requested_by, priority=priority, constraints=constraints,
            assigned_agent=assigned_agent, agent_id=agent_id, depends_on=depends_on,
        )
        with self._lock:
            paused = self._paused.is_set()
        if not paused:
            with self._tick_lock:
                self._scheduler.tick()
        return self._mission_service.get(mission.id) or mission

    def cancel_task(self, task_id: str, *, requested_by: Subject = Subject.HUMAN) -> Mission:
        """Délègue entièrement à `SchedulerService.cancel_mission` —
        annulation RÉELLE (terminaison de processus vérifiée) si la tâche est
        en cours de suivi, simple transition d'état sinon. Jamais dupliqué
        ici — `LookupError` si `task_id` est inconnu, propagée telle quelle."""
        return self._scheduler.cancel_mission(task_id, requested_by=requested_by)

    def get_task_status(self, task_id: str) -> Mission | None:
        return self._mission_service.get(task_id)

    def get_task_result(self, task_id: str) -> TaskResult | None:
        mission = self._mission_service.get(task_id)
        if mission is None:
            return None
        ready = mission.state in (
            MissionState.REVIEW, MissionState.COMPLETED, MissionState.FAILED, MissionState.CANCELLED,
        )
        return TaskResult(
            task_id=mission.id, state=mission.state.value, ready=ready,
            result=mission.result, error=mission.error,
        )

    # -- conceptions (Design Engine, N+17) ---------------------------------------

    def submit_design(self, request_text: str, *, requested_by: Subject = Subject.HUMAN) -> DesignPackage:
        """Soumission `{"type": "design", "request": "..."}` — même garde
        `accepts_submissions` que `submit_task` (READY/RUNNING/PAUSED),
        jamais une seconde machine à états dupliquée. Exécute le pipeline de
        conception de bout en bout (synchrone, voir `apex.design.service.
        DesignService`) et journalise le résultat via `events`, comme toute
        autre opération Operator."""
        self._require_accepts_submissions("submit_design")
        package = self._require_design_service().submit(request_text, requested_by=requested_by)
        self._safe_record_operation(
            "design.submitted",
            {"design_id": package.design_id, "state": package.state.value, "requested_by": requested_by.value},
        )
        return package

    def resume_design(self, design_id: str, *, requested_by: Subject = Subject.HUMAN) -> DesignPackage:
        """Reprend une conception en échec — n'exige PAS que l'Operator
        accepte de nouvelles soumissions (achever une conception déjà
        commencée n'est pas en soi une nouvelle soumission), mais exige tout
        de même `Subject.HUMAN` via `SUBMIT_DESIGN`, même autorisation que
        la soumission initiale."""
        self.policy.authorize(requested_by, Action.SUBMIT_DESIGN)
        package = self._require_design_service().resume(design_id)
        self._safe_record_operation(
            "design.resumed", {"design_id": package.design_id, "state": package.state.value},
        )
        return package

    def get_design_status(self, design_id: str) -> DesignPackage | None:
        return self._require_design_service().get_status(design_id)

    def get_design_result(self, design_id: str) -> DesignPackage | None:
        return self._require_design_service().get_result(design_id)

    def get_recent_operations(self, limit: int = 100) -> list[Any]:
        """Projette `events` (`apex.db.repositories.EventRepository.recent`,
        déjà la source de vérité de tout ce que Mission/Scheduler/Operator
        journalisent) — jamais un second journal d'opérations inventé."""
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            return EventRepository(session).recent(limit=limit)

    # -- cycle de vie du service lui-même (tests/opérations) ---------------------

    def shutdown(self, *, wait: bool = True) -> None:
        """Réservé aux tests/à l'arrêt du processus — force l'arrêt du thread
        de supervision sans passer par la machine à états (contrairement à
        `stop()`), utilisé pour libérer les ressources même depuis un état
        FAULTED. N'arrête PAS le `SchedulerService` sous-jacent (propriété du
        singleton, voir `apex.api.dependencies`)."""
        self._stop_requested.set()
        thread = self._supervisor_thread
        if thread is not None:
            thread.join(timeout=5.0 if wait else 0.0)

    # -- internes -------------------------------------------------------------

    def _require_accepts_submissions(self, operation: str) -> None:
        with self._lock:
            state = self._state
        if not state.accepts_submissions:
            raise OperatorNotReady(operation, current_state=state)

    def _require_design_service(self) -> DesignService:
        if self._design_service is None:
            raise OperatorNotReady("design_engine_unavailable", current_state=self._state)
        return self._design_service

    def _transition(self, target: OperatorState) -> None:
        """Appelée UNIQUEMENT sous `self._lock` déjà tenu par l'appelant."""
        self._state = target
        self._since = _utcnow()

    def _supervise(self) -> None:
        """Boucle de supervision bornée à un seul thread démon — LA pièce
        manquante identifiée dans `apex.interfaces.registry` (entrée
        "Scheduler") : appelle `SchedulerService.tick()` périodiquement tant
        qu'un arrêt n'est pas demandé et que la pause n'est pas active, et
        dérive READY<->RUNNING selon `ExecutionRegistry`. Ne touche jamais
        directement `AgentRuntime`/`SandboxProvider` — le Scheduler reste
        l'unique point d'entrée d'exécution, jamais contourné."""
        while not self._stop_requested.is_set():
            if not self._paused.is_set():
                try:
                    with self._tick_lock:
                        self._scheduler.tick()
                except Exception as exc:  # la supervision ne doit jamais planter le Core
                    self._safe_record_operation(
                        "operator.tick_error", {"error": f"{exc.__class__.__name__}: {exc}"},
                    )
                self._observe_activity()
            self._stop_requested.wait(timeout=self._tick_interval_s)

    def _observe_activity(self) -> None:
        active = len(self._scheduler.registry)
        if self._design_service is not None:
            # Une exécution de pipeline de conception EST une activité
            # Operator au même titre qu'une Mission en cours — jamais un
            # second compteur RUNNING/READY dupliqué, juste une seconde
            # source additionnée à la même dérivation.
            active += self._design_service.active_count()
        with self._lock:
            if active > 0 and self._state is OperatorState.READY:
                self._transition(OperatorState.RUNNING)
            elif active == 0 and self._state is OperatorState.RUNNING:
                self._transition(OperatorState.READY)

    def _safe_record_operation(self, kind: str, details: dict[str, Any]) -> None:
        """Comme `_record_operation`, mais absorbe une DB indisponible —
        utilisée sur les chemins où l'échec de journalisation ne doit jamais
        masquer l'erreur réelle qui a motivé l'appel (ex. `start()` a déjà
        échoué à cause de la DB : réessayer d'y écrire ne ferait
        qu'ajouter une seconde exception sans valeur diagnostique)."""
        try:
            self._record_operation(kind, details)
        except Exception:
            pass

    def _record_operation(self, kind: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(
                ts=_utcnow(), level="INFO", logger="apex.operator", msg=kind, context=details,
            )

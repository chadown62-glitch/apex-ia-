"""`AutonomousLearningService` — point d'entrée production UNIQUE pour
démarrer un cycle d'apprentissage autonome (`apex.metalearner.cycle.
run_experiment_cycle`) sans appeler directement une fonction Python interne.

Passe « Autonomous Learning Trigger + Scheduler/CLI Execution Path ». Ce
module n'implémente AUCUNE logique d'apprentissage — il orchestre
EXCLUSIVEMENT des points d'entrée déjà réels :

    AgentRuntime.spawn (Phase 7, si `clone_id` non fourni)
        -> apex.genome.service.GenomeStoreService.get_current (Phase 17)
        -> run_experiment_cycle (déjà réel, jamais réimplémenté)

Même patron de composition que `apex.scheduler.heartbeat.HeartbeatService` :
exactement UN thread de fond par cycle actif (jamais une croissance
proportionnelle au nombre de démarrages), démarré/arrêté explicitement,
jamais un `asyncio`/une file de tâches distribuée.

**`requested_by` n'est JAMAIS un paramètre de `start_cycle`** — toujours
`Subject.HUMAN`, en dur, exactement comme `apex.api.routes.evals.
create_experiment_cycle` le fait déjà pour `POST /experiment-cycles` :
aucun appelant (CLI, API, Scheduler) ne peut jamais fournir/négocier une
autre identité. Ceci s'applique aussi au Scheduler : `PolicyEngine`
interdit structurellement `Subject.CORE`/un clone de créer une `Task`
(`Action.CREATE_TASK`, HUMAIN seul — §8 anti-auto-illusion, voir la
docstring d'`apex.metalearner.cycle`) — un cycle scheduled sans supervision
humaine directe reste néanmoins légitimement `Subject.HUMAN` selon la
convention DÉJÀ établie par `apex/api/auth.py` : ce processus entier tourne
en mono-utilisateur local, et « activer le Scheduler dans `config.yaml` »
EST l'acte d'autorisation humaine (même raisonnement que « connaître le
jeton API local prouve une action HUMAINE », déjà appliqué à absolument
toutes les routes existantes). Ce n'est jamais un contournement du
PolicyEngine ni une identité fournie par un client externe — le Scheduler
tourne DANS ce processus, jamais atteint par une requête HTTP.
"""

from __future__ import annotations

import dataclasses
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from apex.agents.runtime import AgentRuntime
from apex.errors import CycleAlreadyRunning
from apex.evalrunner.model import EvalMethod
from apex.policy.model import Subject
from apex.sandbox.model import CancellationToken

if TYPE_CHECKING:
    from apex.metalearner.model import ExperimentCycleResult
from apex.tasks.model import TaskSplit

#: Préfixe d'`EvalRun.experiment_ref` pour tout cycle déclenché via CE
#: service (CLI/API/Scheduler) — jamais posé par `POST /experiment-cycles`
#: (appel humain direct, `experiment_ref=None`), ce qui permet à `history()`
#: de ne retrouver QUE les cycles autonomes, jamais un run manuel.
EXPERIMENT_REF_PREFIX = "autonomous-learning:"

#: `apex.db.repositories.EventRepository.add(logger=...)` — même convention
#: que `apex.scheduler.service` (`logger="apex.scheduler"`), jamais un
#: nouveau mécanisme de journalisation.
_EVENT_LOGGER = "apex.metalearner.autonomy"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CycleState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


@dataclass
class CycleStatus:
    """Snapshot en mémoire de l'unique cycle suivi par CE processus — même
    limite honnête qu'`apex.scheduler.registry.ExecutionRegistry` : ne
    survit jamais à un redémarrage (documenté, pas résolu silencieusement).
    `history()` reste la source durable — voir `EXPERIMENT_REF_PREFIX`."""

    state: CycleState = CycleState.IDLE
    cycle_id: str | None = None
    specialty: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    eval_run_id: str | None = None
    task_id: str | None = None
    oracle_verdict: str | None = None
    #: Renseignés par `apex.metalearner.feedback.LearningFeedbackService`
    #: après un cycle `COMPLETED` — jamais promus (`None` par défaut :
    #: preuve insuffisante, panne isolée, ou candidat non encore traité).
    skill_candidate_id: str | None = None
    genome_candidate_id: str | None = None


@dataclass(frozen=True)
class CycleHistoryEntry:
    """UNE ligne d'historique, reconstruite depuis `EventRepository` (déjà
    réel, déjà utilisé par `apex.scheduler.service` pour le même usage) —
    jamais une nouvelle table. `state` est toujours `COMPLETED`/`FAILED`/
    `CANCELLED` (terminal) : un événement `cycle.started` n'apparaît jamais
    seul dans l'historique."""

    cycle_id: str
    specialty: str
    state: CycleState
    eval_run_id: str | None
    task_id: str | None
    oracle_verdict: str | None
    error: str | None
    completed_at: datetime
    skill_candidate_id: str | None = None
    genome_candidate_id: str | None = None


class AutonomousLearningService:
    """UN cycle actif à la fois, dans CE processus. La protection contre un
    double démarrage concurrent est un verrou mémoire (`threading.Lock`) —
    garantit l'absence de doublon PAR INSTANCE, pas globalement entre
    plusieurs processus, même limite honnête que `SchedulerService`
    (documentée dans sa propre docstring, jamais résolue silencieusement
    ici non plus)."""

    def __init__(
        self,
        session_factory: Any,
        *,
        agent_runtime: AgentRuntime,
        config: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._agent_runtime = agent_runtime
        self._config = config
        self._lock = threading.Lock()
        self._status = CycleStatus()
        self._thread: threading.Thread | None = None
        self._cancel_token: CancellationToken | None = None

    # -- lecture ------------------------------------------------------------

    def status(self) -> CycleStatus:
        with self._lock:
            return dataclasses.replace(self._status)

    def history(self, limit: int = 20) -> list[CycleHistoryEntry]:
        """Reconstruit l'historique depuis les événements `cycle.completed`/
        `cycle.failed`/`cycle.cancelled` (`EventRepository.recent`, déjà
        réel). `EventRepository.recent` ne filtre pas par `logger` — le
        filtrage a lieu ici, même limite proportionnelle qu'ailleurs dans ce
        module (aucun index dédié pour une fonctionnalité d'observabilité,
        pas un chemin chaud)."""
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        entries: list[CycleHistoryEntry] = []
        with session_scope(self._session_factory) as session:
            for row in EventRepository(session).recent(limit=max(limit * 8, 200)):
                if row.logger != _EVENT_LOGGER or row.msg == "cycle.started":
                    continue
                ctx = row.context or {}
                entries.append(
                    CycleHistoryEntry(
                        cycle_id=ctx.get("cycle_id", ""), specialty=ctx.get("specialty", ""),
                        state=CycleState(ctx.get("state", CycleState.FAILED.value)),
                        eval_run_id=ctx.get("eval_run_id"), task_id=ctx.get("task_id"),
                        oracle_verdict=ctx.get("oracle_verdict"), error=ctx.get("error"),
                        skill_candidate_id=ctx.get("skill_candidate_id"),
                        genome_candidate_id=ctx.get("genome_candidate_id"),
                        completed_at=row.ts,
                    )
                )
                if len(entries) >= limit:
                    break
        return entries

    # -- écriture -------------------------------------------------------------

    def start_cycle(
        self,
        *,
        specialty: str,
        current_level: int = 1,
        method: EvalMethod = EvalMethod.PRACTICE,
        split: TaskSplit = TaskSplit.TRAIN,
        clone_id: str | None = None,
    ) -> CycleStatus:
        """Démarre un cycle en tâche de fond et retourne IMMÉDIATEMENT son
        état `RUNNING` — jamais bloquant, pour que CLI/API/Scheduler
        reçoivent une réponse rapide et interrogent `status()` ensuite.
        Lève `CycleAlreadyRunning` si un cycle est déjà `RUNNING` — jamais
        un second cycle silencieusement concurrent."""
        with self._lock:
            if self._status.state is CycleState.RUNNING:
                raise CycleAlreadyRunning(self._status.cycle_id or "?")
            cycle_id = uuid.uuid4().hex
            cancel_token = CancellationToken()
            self._cancel_token = cancel_token
            self._status = CycleStatus(
                state=CycleState.RUNNING, cycle_id=cycle_id, specialty=specialty, started_at=_utcnow(),
            )
            snapshot = dataclasses.replace(self._status)
            self._thread = threading.Thread(
                target=self._run_cycle,
                args=(cycle_id, specialty, current_level, method, split, clone_id, cancel_token),
                name=f"apex-autonomous-learning-{cycle_id[:8]}",
                daemon=True,
            )
            self._thread.start()
        return snapshot

    def cancel(self) -> CycleStatus:
        """Demande l'annulation réelle du cycle en cours (`CancellationToken.
        request_cancel`, même mécanisme que `SchedulerService.cancel_mission`)
        — no-op si aucun cycle n'est `RUNNING`. La fenêtre de cancellation
        réelle dépend du backend sandbox (voir `apex.sandbox.linux_namespace`) ;
        une tâche déterministe très courte peut se terminer avant que la
        demande ne prenne effet — jamais présenté comme une garantie
        instantanée."""
        with self._lock:
            if self._status.state is not CycleState.RUNNING:
                return dataclasses.replace(self._status)
            self._status.state = CycleState.CANCEL_REQUESTED
            token = self._cancel_token
            snapshot = dataclasses.replace(self._status)
        if token is not None:
            token.request_cancel()
        return snapshot

    # -- exécution en tâche de fond -------------------------------------------

    def _run_cycle(
        self,
        cycle_id: str,
        specialty: str,
        current_level: int,
        method: EvalMethod,
        split: TaskSplit,
        clone_id: str | None,
        cancel_token: CancellationToken,
    ) -> None:
        from apex.metalearner.cycle import run_experiment_cycle

        self._record_event("cycle.started", {"cycle_id": cycle_id, "specialty": specialty})
        try:
            resolved_clone_id = clone_id or self._spawn_clone(specialty)
            result = run_experiment_cycle(
                session_factory=self._session_factory, agent_runtime=self._agent_runtime,
                clone_id=resolved_clone_id, specialty=specialty, current_level=current_level,
                requested_by=Subject.HUMAN, method=method, split=split,
                experiment_ref=f"{EXPERIMENT_REF_PREFIX}{cycle_id}", cancel_token=cancel_token,
            )
        except Exception as exc:  # un cycle autonome ne doit jamais planter le processus
            with self._lock:
                cancelled = self._status.state is CycleState.CANCEL_REQUESTED
                self._status.state = CycleState.CANCELLED if cancelled else CycleState.FAILED
                self._status.completed_at = _utcnow()
                self._status.error = f"{exc.__class__.__name__}: {exc}"
            self._record_event(
                "cycle.cancelled" if cancelled else "cycle.failed",
                {
                    "cycle_id": cycle_id, "specialty": specialty,
                    "state": self._status.state.value, "error": self._status.error,
                },
            )
            return

        experiment_ref = f"{EXPERIMENT_REF_PREFIX}{cycle_id}"
        skill_candidate_id, genome_candidate_id = self._process_feedback(result, experiment_ref)

        with self._lock:
            self._status.state = CycleState.COMPLETED
            self._status.completed_at = _utcnow()
            self._status.eval_run_id = result.eval_run.id
            self._status.task_id = result.task.id
            self._status.oracle_verdict = result.eval_run.oracle_verdict.value
            self._status.skill_candidate_id = skill_candidate_id
            self._status.genome_candidate_id = genome_candidate_id
        self._record_event(
            "cycle.completed",
            {
                "cycle_id": cycle_id, "specialty": specialty, "state": CycleState.COMPLETED.value,
                "eval_run_id": result.eval_run.id, "task_id": result.task.id,
                "oracle_verdict": result.eval_run.oracle_verdict.value,
                "skill_candidate_id": skill_candidate_id, "genome_candidate_id": genome_candidate_id,
            },
        )

    def _process_feedback(
        self, result: ExperimentCycleResult, experiment_ref: str,
    ) -> tuple[str | None, str | None]:
        """Traitement Skill/Genome — jamais susceptible de faire échouer un
        cycle dont l'expérience elle-même a réussi (§7 isolation des
        pannes) : `LearningFeedbackService` isole déjà ses propres pannes
        Skill/Genome individuellement, mais une panne plus large (DB
        indisponible au moment d'écrire l'événement final, par exemple)
        est capturée ICI aussi, en dernier recours — le cycle reste
        `COMPLETED`, seuls les deux candidats restent `None`."""
        from apex.metalearner.feedback import LearningFeedbackService

        try:
            outcome = LearningFeedbackService(self._session_factory).process_cycle_result(
                result, experiment_ref=experiment_ref,
            )
        except Exception as exc:
            self._record_event(
                "cycle.feedback_failed",
                {"experiment_ref": experiment_ref, "error": f"{exc.__class__.__name__}: {exc}"},
            )
            return None, None
        return outcome.skill_candidate_id, outcome.genome_candidate_id

    def _spawn_clone(self, specialty: str) -> str:
        """Provisionne un clone jetable pour CE cycle — `Subject.LEARNING`
        (vocabulaire déjà défini dans `apex.policy.model.Subject`, jamais
        utilisé avant cette passe), jamais `Subject.RESEARCH` (réservé aux
        appels humains directs via `POST /experiment-cycles`/le Panel
        Experiment Lab). Choisit le premier `ComputePool` ACTIVE déclarant
        `Capability.SANDBOX` (tri par `pool_id`, déterministe) et le
        `GenomeVersion` actuellement PROMOTED — jamais une valeur fabriquée
        si aucun des deux n'existe."""
        from apex.config.loader import load_config
        from apex.config.schema import Capability, PoolStatus
        from apex.genome.service import GenomeStoreService

        config = self._config or load_config()
        candidates = sorted(
            (p for p in config.pools.values() if p.status is PoolStatus.ACTIVE and Capability.SANDBOX in p.capabilities),
            key=lambda p: p.pool_id,
        )
        if not candidates:
            raise ValueError(
                "aucun ComputePool ACTIVE ne déclare la capability « sandbox » — "
                "impossible de provisionner un clone pour un cycle autonome"
            )
        genome = GenomeStoreService(self._session_factory).get_current()
        if genome is None:
            raise ValueError(
                "aucune GenomeVersion PROMOTED — impossible de provisionner un clone "
                "pour un cycle autonome (voir apex.genome.service.GenomeStoreService.promote)"
            )
        clone = self._agent_runtime.spawn(
            Subject.LEARNING, pool=candidates[0], genome_ref=genome.content_hash, spawned_by=Subject.CORE,
        )
        return clone.clone_id

    def _record_event(self, msg: str, context: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(ts=_utcnow(), level="INFO", logger=_EVENT_LOGGER, msg=msg, context=context)

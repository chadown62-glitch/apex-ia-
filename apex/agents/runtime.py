"""AgentRuntime — instanciation, cycle de vie et isolation d'un clone.

Registre : `AgentRuntime`, `STUB` (pas `IMPLEMENTED` — voir la limite
honnête ci-dessous). Réutilise entièrement l'existant, rien recréé :

- `PolicyEngine` (phase précédente) décide qui a le droit de spawn/terminer
  un clone et ce que chaque type de clone peut faire — ce module ne contient
  AUCUNE règle de permission en dur, tout passe par `self.policy`.
- `SandboxDefaults` (`apex.config.schema`, EXISTS phase 1) fournit le budget
  (`timeout_s`) par défaut.
- `ComputePool` (EXISTS phase 2) est fourni par l'appelant à `spawn()`.
- `events`/`EventRepository` et `audit_log`/`AuditLogRepository` (EXISTS
  phase 2) sont les seuls canaux d'écriture — aucun nouveau mécanisme de
  journalisation.

**Limite honnête, à ne jamais présenter comme résolue** : ce module ne
sandbox RIEN au niveau du système d'exploitation. `SandboxProvider` (PLANNED,
phase 3) n'existe pas encore. Le `work` exécuté par `run()` tourne comme un
appel Python ordinaire dans un thread du même processus — le budget CPU/RAM
n'est ni cloisonné ni appliqué par un cgroup, seulement mesuré (RAM : pas du
tout, `UNKNOWN` — voir `apex/agents/view.py`). Un dépassement de délai est
DÉTECTÉ (le thread est abandonné) mais pas interrompu de force : Python ne
peut pas tuer un thread bloquant sans support OS. C'est exactement pourquoi
le registre porte `STUB` et non `IMPLEMENTED` : l'« isolation » nommée dans
la description de l'abstraction n'est pas encore une garantie réelle.

Un seul type de clone est exercé de bout en bout par les tests
(`Subject.RESEARCH`, le moins risqué : pas d'écriture de code, accès réseau
déjà cadré par `PolicyEngine`). Aucune sous-classe par type n'existe : le
contrat commun (`Subject` + allow-list de `PolicyEngine`) suffit pour tous
les types — ajouter une spécialité ne demande pas de toucher ce fichier.
"""

from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypeVar

from apex.agents.model import CLONE_SUBJECTS, Clone, CloneState
from apex.config.schema import ComputePool
from apex.errors import IsolationUnavailable
from apex.gate.contract import EnvironmentUnknown
from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject
from apex.sandbox import CancellationToken, ExecutionStatus, SandboxProvider, SandboxResult, SandboxSpec

T = TypeVar("T")

#: Étape 8 — chemin LÉGAL minimal, depuis chaque état non terminal de
#: `CloneState._TRANSITIONS` (apex.agents.model), vers un état terminal —
#: utilisé UNIQUEMENT par `AgentRuntime.recover_orphan` (voir sa docstring).
#: FAILED est préféré partout où il est atteignable ; TERMINATED n'est
#: utilisé que là où c'est la SEULE sortie légale (REPORTING/TERMINATING) —
#: jamais un raccourci hors de la machine à états existante.
_ORPHAN_RECOVERY_PATH: dict[CloneState, tuple[CloneState, ...]] = {
    CloneState.SPAWNING: (CloneState.FAILED,),
    CloneState.WORKING: (CloneState.CRASHED, CloneState.FAILED),
    CloneState.REPORTING: (CloneState.TERMINATED,),
    CloneState.TERMINATING: (CloneState.TERMINATED,),
    CloneState.CRASHED: (CloneState.FAILED,),
    CloneState.RECOVERING: (CloneState.SPAWNING, CloneState.FAILED),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _measured_isolation_level() -> str:
    """Isolation réellement mesurée par le Gate, jamais supposée.

    `"unknown"` si aucun rapport n'existe ou s'il n'est pas mesuré sur cette
    machine — jamais une valeur optimiste par défaut (même principe que
    `Environment.capability()`, qui lève plutôt que de renvoyer `False`).
    """
    from apex.gate.contract import load_environment

    try:
        environment = load_environment()
    except EnvironmentUnknown:
        return "unknown"
    if not environment.measured_on_target:
        return "unknown"
    level = environment.raw.get("capabilities", {}).get("isolation_level")
    return str(level) if level else "unknown"


def _cpu_time_s() -> float | None:
    """Temps CPU du processus courant. `None` (jamais 0) si non mesurable.

    Agrégé au niveau du processus entier, pas isolé par clone — c'est une
    approximation documentée, pas une mesure par clone tant que
    `SandboxProvider` (subprocess réel) n'existe pas.
    """
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_utime
    except (ImportError, AttributeError):  # pragma: no cover - dépend de l'OS
        return None


@dataclass(frozen=True)
class RunResult:
    success: bool
    output: Any | None
    error: str | None
    elapsed_s: float
    cpu_time_s: float | None
    retried: bool = False


class AgentRuntime:
    """Orchestrateur de clones. Ne journalise que via `events`/`audit_log`
    existants ; ne stocke l'état en mémoire que le temps de l'appel — la
    persistance (`AgentRepository`) est la source de vérité entre deux appels.
    """

    def __init__(
        self,
        session_factory: Any,
        *,
        policy: PolicyEngine | None = None,
        default_timeout_s: float = 900.0,
    ) -> None:
        self.policy = policy or PolicyEngine()
        self._session_factory = session_factory
        self._default_timeout_s = default_timeout_s

    # -- cycle de vie --------------------------------------------------------

    def spawn(
        self,
        clone_type: Subject,
        *,
        pool: ComputePool,
        genome_ref: str,
        spawned_by: Subject,
        mission_id: str | None = None,
        max_retries: int = 2,
    ) -> Clone:
        if clone_type not in CLONE_SUBJECTS:
            raise ValueError(f"« {clone_type.value} » n'est pas un type de clone valide")
        if not genome_ref:
            raise ValueError(
                "genome_ref est obligatoire — un clone sans version épinglée du "
                "Genome produit des résultats incomparables (COGNITIVE_ARCHITECTURE.md §2.2)"
            )
        if not pool.capabilities:
            raise ValueError(f"le pool « {pool.pool_id} » ne déclare aucune capability")

        # Seul CORE ou HUMAN peut spawn un clone — appliqué par PolicyEngine,
        # pas par une condition en dur ici (§2.11 : aucune auto-élévation).
        self.policy.authorize(spawned_by, Action.SPAWN_CLONE)

        clone = Clone.new(
            clone_type,
            genome_ref=genome_ref,
            spawned_by=spawned_by.value,
            pool_id=pool.pool_id,
            isolation_level=_measured_isolation_level(),
            mission_id=mission_id,
            max_retries=max_retries,
        )
        self._persist_create(clone)
        self._record_event(clone, "clone.spawned", {"pool_id": pool.pool_id})
        self._advance(clone, CloneState.READY)
        return clone

    def run(
        self,
        clone: Clone,
        work: Callable[[], T],
        *,
        timeout_s: float | None = None,
    ) -> RunResult:
        """Exécute `work` pour ce clone. Ne lève jamais — voir docstring module
        pour la limite réelle du délai (détecté, pas imposé de force)."""
        if clone.state is not CloneState.READY:
            raise ValueError(
                f"le clone {clone.clone_id} doit être READY pour démarrer un travail "
                f"(état actuel : {clone.state.value})"
            )
        budget = timeout_s if timeout_s is not None else self._default_timeout_s

        self._advance(clone, CloneState.WORKING)
        clone.started_at = _utcnow()
        self._persist_update(clone, started_at=clone.started_at)
        self._record_event(clone, "clone.heartbeat", {"phase": "start"})

        started = time.perf_counter()
        cpu_started = _cpu_time_s()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(work)
            try:
                output = future.result(timeout=budget)
            except concurrent.futures.TimeoutError:
                return self._handle_failure(
                    clone, reason=f"délai dépassé ({budget:.0f}s)",
                    elapsed_s=time.perf_counter() - started,
                    cpu_time_s=_delta(cpu_started, _cpu_time_s()),
                )
            except Exception as exc:  # le clone ne fait jamais planter le Core
                return self._handle_failure(
                    clone, reason=f"{exc.__class__.__name__}: {exc}",
                    elapsed_s=time.perf_counter() - started,
                    cpu_time_s=_delta(cpu_started, _cpu_time_s()),
                )

        elapsed = time.perf_counter() - started
        cpu_time = _delta(cpu_started, _cpu_time_s())

        self._advance(clone, CloneState.REPORTING)
        self._record_event(clone, "clone.result", {"elapsed_s": round(elapsed, 3)})
        self._advance(clone, CloneState.TERMINATED)
        clone.terminated_at = _utcnow()
        self._persist_update(clone, terminated_at=clone.terminated_at)
        self._record_audit(clone, action="clone.completed", details={"elapsed_s": round(elapsed, 3)})
        return RunResult(success=True, output=output, error=None, elapsed_s=elapsed, cpu_time_s=cpu_time)

    def execute_in_sandbox(
        self,
        clone: Clone,
        sandbox: SandboxProvider,
        spec: SandboxSpec,
        *,
        requested_by: Subject = Subject.CORE,
        cancel_token: CancellationToken | None = None,
    ) -> SandboxResult:
        """AGENT RUNTIME -> SANDBOX de la chaîne obligatoire (étape 7). Ne
        contourne jamais PolicyEngine : `EXECUTE_IN_SANDBOX` n'est accordée
        qu'à CORE — ni HUMAN, ni (a fortiori) un clone ne peut lancer sa
        propre exécution. Si `sandbox` ne peut garantir l'isolation demandée,
        `SandboxProvider.execute` lève `IsolationUnavailable` : ce module ne
        rattrape cette exception que pour journaliser puis la relève — jamais
        une exécution dégradée silencieuse à la place.

        Contrairement à `run()`, aucune reprise automatique (RECOVERING) :
        un échec de sandbox est un état terminal direct (CRASHED -> FAILED),
        cohérent avec le fait qu'une mission qui échoue en sandbox repasse
        par une décision humaine (REVIEW/reject), pas par un relancement
        automatique — voir `apex.missions.execution`.

        `cancel_token` (étape 8) est transmis tel quel à `sandbox.execute` —
        ce module ne sait pas comment annuler une exécution, seul le backend
        le sait. Une annulation réussie (`ExecutionStatus.CANCELLED`) termine
        proprement le clone (TERMINATING -> TERMINATED), jamais via
        CRASHED/FAILED : ce n'est pas un échec, c'est un arrêt volontaire.
        """
        self.policy.authorize(requested_by, Action.EXECUTE_IN_SANDBOX)
        if clone.state is not CloneState.READY:
            raise ValueError(
                f"le clone {clone.clone_id} doit être READY pour exécuter en sandbox "
                f"(état actuel : {clone.state.value})"
            )

        self._advance(clone, CloneState.WORKING)
        clone.started_at = _utcnow()
        self._persist_update(clone, started_at=clone.started_at)
        self._record_event(clone, "sandbox.created", {"mission_id": spec.mission_id})

        try:
            result = sandbox.execute(spec, cancel_token=cancel_token)
        except IsolationUnavailable as exc:
            self._record_event(clone, "sandbox.failed", {"reason": str(exc), "mission_id": spec.mission_id})
            self._fail_clone(clone, reason=str(exc))
            self._record_audit(
                clone, action="sandbox.unavailable",
                details={"mission_id": spec.mission_id, "reason": str(exc)},
            )
            raise

        self._record_event(
            clone, "sandbox.started",
            {"mission_id": spec.mission_id, "isolation_level": result.isolation_level.value},
        )

        if result.status is ExecutionStatus.SUCCESS:
            self._advance(clone, CloneState.REPORTING)
            self._record_event(
                clone, "sandbox.completed",
                {
                    "mission_id": spec.mission_id, "exit_code": result.exit_code.to_dict(),
                    "elapsed_s": result.elapsed_s.to_dict(),
                },
            )
            self._advance(clone, CloneState.TERMINATED)
            clone.terminated_at = _utcnow()
            self._persist_update(clone, terminated_at=clone.terminated_at)
        elif result.status is ExecutionStatus.CANCELLED:
            self._record_event(
                clone, "sandbox.cancelled",
                {"mission_id": spec.mission_id, "exit_code": result.exit_code.to_dict()},
            )
            self._advance(clone, CloneState.TERMINATING)
            self._advance(clone, CloneState.TERMINATED)
            clone.terminated_at = _utcnow()
            self._persist_update(clone, terminated_at=clone.terminated_at)
        else:
            kind = "sandbox.timeout" if result.status is ExecutionStatus.TIMEOUT else "sandbox.failed"
            self._record_event(
                clone, kind,
                {
                    "mission_id": spec.mission_id, "status": result.status.value,
                    "exit_code": result.exit_code.to_dict(), "signal": result.signal.to_dict(),
                },
            )
            self._fail_clone(clone, reason=result.error_detail or result.status.value)

        self._record_audit(
            clone, action="sandbox.execution_completed",
            details={"mission_id": spec.mission_id, "status": result.status.value},
        )
        return result

    def recover_orphan(self, clone: Clone, *, reason: str) -> Clone:
        """Fait progresser un clone trouvé dans un état NON TERMINAL au
        démarrage du processus vers l'état terminal légal le plus proche.

        N'est JAMAIS appelé pendant une exécution normale — uniquement par
        `apex.scheduler.recovery.RecoveryService`, exactement une fois par
        clone orphelin détecté (voir sa docstring pour l'invariant qui rend
        cela sûr : le registre d'exécution en mémoire est nécessairement
        vide à ce moment, donc tout état non terminal trouvé en base ne peut
        plus correspondre à un processus réellement supervisé). Ne tente
        AUCUNE reprise : c'est une terminaison propre, jamais une résomption
        — reprendre supposerait de retrouver un sous-processus dont plus
        personne n'a la supervision, ce que ce module refuse explicitement.
        """
        path = _ORPHAN_RECOVERY_PATH.get(clone.state)
        if path is None:
            raise ValueError(
                f"« {clone.state.value} » est déjà terminal ou absent de la table de récupération"
            )
        clone.last_error = reason
        for step in path:
            self._advance(clone, step)
        clone.terminated_at = _utcnow()
        self._persist_update(clone, last_error=reason, terminated_at=clone.terminated_at)
        self._record_event(
            clone, "clone.recovered_orphan", {"reason": reason, "final_state": clone.state.value},
        )
        self._record_audit(
            clone, action="clone.recovered_orphan",
            details={"reason": reason, "final_state": clone.state.value},
        )
        return clone

    def _fail_clone(self, clone: Clone, *, reason: str) -> None:
        clone.last_error = reason
        self._advance(clone, CloneState.CRASHED)
        self._persist_update(clone, last_error=reason)
        self._advance(clone, CloneState.FAILED)
        clone.terminated_at = _utcnow()
        self._persist_update(clone, terminated_at=clone.terminated_at)

    def terminate(self, clone: Clone, *, requested_by: Subject = Subject.CORE, reason: str) -> Clone:
        self.policy.authorize(requested_by, Action.TERMINATE_CLONE)
        self._advance(clone, CloneState.TERMINATING)
        self._record_event(clone, "clone.terminated", {"reason": reason, "requested_by": requested_by.value})
        self._advance(clone, CloneState.TERMINATED)
        clone.terminated_at = _utcnow()
        self._persist_update(clone, terminated_at=clone.terminated_at)
        self._record_audit(
            clone, action="clone.terminated",
            details={"reason": reason, "requested_by": requested_by.value},
        )
        return clone

    # -- échec / reprise ------------------------------------------------------

    def _handle_failure(
        self, clone: Clone, *, reason: str, elapsed_s: float, cpu_time_s: float | None
    ) -> RunResult:
        clone.last_error = reason
        self._advance(clone, CloneState.CRASHED)
        self._record_event(clone, "clone.error", {"reason": reason})
        self._persist_update(clone, last_error=reason)

        if clone.retries_used < clone.max_retries:
            self._advance(clone, CloneState.RECOVERING)
            clone.retries_used += 1
            self._advance(clone, CloneState.SPAWNING)
            self._advance(clone, CloneState.READY)
            clone.started_at = None
            self._persist_update(clone, retries_used=clone.retries_used)
            self._record_event(clone, "clone.recovering", {"retries_used": clone.retries_used})
            return RunResult(
                success=False, output=None, error=reason,
                elapsed_s=elapsed_s, cpu_time_s=cpu_time_s, retried=True,
            )

        self._advance(clone, CloneState.FAILED)
        clone.terminated_at = _utcnow()
        self._persist_update(clone, terminated_at=clone.terminated_at)
        self._record_audit(clone, action="clone.failed", details={"reason": reason})
        return RunResult(
            success=False, output=None, error=reason,
            elapsed_s=elapsed_s, cpu_time_s=cpu_time_s, retried=False,
        )

    # -- internes ---------------------------------------------------------

    def _advance(self, clone: Clone, new_state: CloneState) -> None:
        clone.transition(new_state)  # lève InvalidTransition si illégal
        self._persist_update(clone, state=clone.state.value)

    def _persist_create(self, clone: Clone) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentRepository

        with session_scope(self._session_factory) as session:
            AgentRepository(session).create(
                clone_id=clone.clone_id, type=clone.type.value, state=clone.state.value,
                genome_ref=clone.genome_ref, spawned_by=clone.spawned_by, pool_id=clone.pool_id,
                isolation_level=clone.isolation_level, mission_id=clone.mission_id,
                max_retries=clone.max_retries, created_at=clone.created_at,
            )

    def _persist_update(self, clone: Clone, **overrides: Any) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentRepository

        with session_scope(self._session_factory) as session:
            AgentRepository(session).update_state(
                clone.clone_id,
                overrides.pop("state", clone.state.value),
                started_at=overrides.get("started_at"),
                terminated_at=overrides.get("terminated_at"),
                last_heartbeat_at=overrides.get("last_heartbeat_at"),
                retries_used=overrides.get("retries_used"),
                last_error=overrides.get("last_error"),
            )

    def _record_event(self, clone: Clone, kind: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(
                ts=_utcnow(), level="INFO", logger=f"apex.agents.{clone.type.value}",
                msg=kind, context={"clone_id": clone.clone_id, "kind": kind, **details},
            )

    def _record_audit(self, clone: Clone, *, action: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository

        # Écrit en tant que CORE : un clone n'a jamais WRITE_AUDIT_LOG par
        # défaut (allow-list de PolicyEngine) — c'est le runtime qui journalise
        # pour son compte, jamais le clone lui-même.
        self.policy.authorize(Subject.CORE, Action.WRITE_AUDIT_LOG)
        with session_scope(self._session_factory) as session:
            AuditLogRepository(session).record(
                ts=_utcnow(), actor="agent_runtime", action=action,
                target=clone.clone_id, details=details,
            )


def _delta(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return end - start

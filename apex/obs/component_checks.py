"""État RÉEL des composants d'APEX, pour le Panel (`APEX.exe`) — `GET /api/v1/status`.

Chaque check fait une vraie opération bornée dans le temps (requête SQL, `PING` Redis, requête HTTP
Ollama, détection du sandbox, lecture du registre du cerveau) et rend un état dans le vocabulaire du
Panel ; aucun n'est déduit de la configuration seule. Un check ne lève jamais : une exception devient
l'état « error » avec sa cause.

États : `ready` (mesure positive), `degraded` (répond, mais incomplet), `offline` (ne répond pas /
n'est pas démarré / non configuré), `error` (répond mal ou mesure impossible), `unknown` (pas mesuré
dans le délai).
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

READY = "ready"
DEGRADED = "degraded"
OFFLINE = "offline"
ERROR = "error"
UNKNOWN = "unknown"

#: Délai par opération réseau ; l'ensemble des checks tourne en parallèle.
NETWORK_TIMEOUT_S = 2.0
#: Délai global au-delà duquel un check encore en cours est rapporté `unknown`.
TOTAL_BUDGET_S = 6.0
#: La détection du sandbox lance plusieurs sous-processus : résultat gardé ce temps-là.
SANDBOX_CACHE_S = 300.0

BRAIN_WORKDIR_ENV = "APEX_BRAIN_WORKDIR"


@dataclass(frozen=True)
class ComponentStatus:
    component: str
    state: str
    detail: str
    latency_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _refused(exc: BaseException) -> bool:
    text = f"{exc.__class__.__name__}: {exc}".lower()
    return isinstance(exc, ConnectionRefusedError) or "refused" in text or "refusée" in text


def check_database() -> ComponentStatus:
    try:
        from sqlalchemy import text

        from apex.api.dependencies import get_session_factory
        from apex.db.engine import session_scope

        from apex.db.schema import schema_state

        with session_scope(get_session_factory()) as session:
            session.execute(text("SELECT 1"))
            engine = session.get_bind()
        # Joignable ne suffit pas : sans les tables du code en cours (audit du 03/10/2026, aucune
        # migration n'était jamais appliquée), aucune donnée ne peut être lue ni écrite.
        state = schema_state(engine)
        if state.ok:
            return ComponentStatus("database", READY, f"connectée ; {state.describe()}")
        return ComponentStatus(
            "database", DEGRADED,
            f"connectée, mais {state.describe()} — redémarrez APEX (launch.sh applique les migrations) "
            "ou « apex db upgrade »",
        )
    except Exception as exc:
        state = OFFLINE if _refused(exc) else ERROR
        return ComponentStatus("database", state, f"{exc.__class__.__name__}: {exc}"[:300])


def _service(name: str) -> tuple[str, int] | None:
    from apex.config.loader import load_config

    endpoint = load_config().services.get(name)
    return None if endpoint is None else (endpoint.host, endpoint.port)


def check_redis(address: tuple[str, int] | None = None) -> ComponentStatus:
    try:
        address = address or _service("redis")
        if address is None:
            return ComponentStatus("redis", OFFLINE, "« redis » non déclaré dans la configuration")
        with socket.create_connection(address, timeout=NETWORK_TIMEOUT_S) as conn:
            conn.settimeout(NETWORK_TIMEOUT_S)
            conn.sendall(b"PING\r\n")
            reply = conn.recv(64)
    except TimeoutError:
        return ComponentStatus("redis", ERROR, f"aucune réponse sous {NETWORK_TIMEOUT_S:.0f}s")
    except OSError as exc:
        state = OFFLINE if _refused(exc) else ERROR
        return ComponentStatus("redis", state, f"{exc.__class__.__name__}: {exc}")
    if reply.startswith(b"+PONG"):
        return ComponentStatus("redis", READY, f"PONG ({address[0]}:{address[1]})")
    if reply.startswith(b"-NOAUTH"):
        return ComponentStatus("redis", DEGRADED, "Redis répond mais exige une authentification")
    where = f"{address[0]}:{address[1]}"
    return ComponentStatus("redis", ERROR, f"réponse inattendue sur {where} : {reply[:32]!r}")


def check_ollama(address: tuple[str, int] | None = None) -> ComponentStatus:
    try:
        address = address or _service("ollama")
        if address is None:
            return ComponentStatus("ollama", OFFLINE, "« ollama » non déclaré dans la configuration")
        url = f"http://{address[0]}:{address[1]}/api/tags"
        with urllib.request.urlopen(url, timeout=NETWORK_TIMEOUT_S) as response:
            payload = json.loads(response.read(1_000_000))
    except urllib.error.HTTPError as exc:
        return ComponentStatus("ollama", ERROR, f"HTTP {exc.code}")
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError):
            return ComponentStatus("ollama", ERROR, f"aucune réponse sous {NETWORK_TIMEOUT_S:.0f}s")
        state = OFFLINE if _refused(reason if isinstance(reason, BaseException) else exc) else ERROR
        return ComponentStatus("ollama", state, f"{reason}")
    except ValueError:
        return ComponentStatus("ollama", ERROR, "réponse non JSON : ce port n'est pas un serveur Ollama")
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return ComponentStatus("ollama", ERROR, "réponse inattendue de /api/tags")
    if not models:
        return ComponentStatus("ollama", DEGRADED, "Ollama répond mais aucun modèle n'est installé")
    return ComponentStatus("ollama", READY, f"{len(models)} modèle(s) disponible(s)")


_sandbox_cache: tuple[float, ComponentStatus] | None = None
_sandbox_lock = threading.Lock()


def check_sandbox(
    detect: Callable[[], Any] | None = None, now: Callable[[], float] = time.monotonic
) -> ComponentStatus:
    global _sandbox_cache
    with _sandbox_lock:
        if detect is None and _sandbox_cache is not None and now() - _sandbox_cache[0] < SANDBOX_CACHE_S:
            return _sandbox_cache[1]
        try:
            backend = ""
            if detect is None:
                # La sélection RÉELLE du runtime (`apex.sandbox.provider.select_sandbox_provider`, celle
                # qu'`AgentRuntime`/TaskLoop utilisent), pas seulement le backend par namespaces : constaté
                # sur WSL2 réel (run 18, B04), `detect_capabilities` seul disait « Sandbox OFFLINE »
                # pendant que le runtime retenait ContainerSandbox, isolation vérifiée sur un vrai conteneur.
                from apex.sandbox.provider import select_sandbox_provider

                selection = select_sandbox_provider()
                report, backend = selection.report, f"{selection.provider_name} : "
            else:
                report = detect()
            level = getattr(report.level, "value", str(report.level))
            status = (
                ComponentStatus("sandbox", READY, f"{backend}isolation {level} disponible")
                if report.is_available
                else ComponentStatus(
                    "sandbox", OFFLINE, f"{backend}aucune isolation réelle ({level}) : {report.detail}"[:300]
                )
            )
        except Exception as exc:
            status = ComponentStatus("sandbox", ERROR, f"{exc.__class__.__name__}: {exc}"[:300])
        _sandbox_cache = (now(), status)
        return status


def check_brain(workdir: str | None = None) -> ComponentStatus:
    root = workdir if workdir is not None else os.environ.get(BRAIN_WORKDIR_ENV)
    if not root:
        return ComponentStatus("brain", OFFLINE, f"cerveau non configuré ({BRAIN_WORKDIR_ENV} absent)")
    path = Path(root)
    if not path.is_dir():
        return ComponentStatus("brain", ERROR, f"répertoire du cerveau introuvable : {root}")
    from apex.util.extensions import load_research_extension

    research = load_research_extension()
    if research is None:
        return ComponentStatus("brain", OFFLINE, "cerveau neuronal absent de cette distribution (Runtime)")
    try:
        champion = research.check_brain(path)
    except Exception as exc:
        return ComponentStatus("brain", ERROR, f"{exc.__class__.__name__}: {exc}"[:300])
    if champion.get("status") == "not_available":
        return ComponentStatus("brain", DEGRADED, f"aucun champion : {champion.get('reason')}")
    if champion.get("attested"):
        return ComponentStatus("brain", READY, f"champion {champion.get('name')} attesté")
    problem = champion.get("attestation_problem")
    return ComponentStatus("brain", ERROR, f"champion NON attesté : {problem}"[:300])


CHECKS: dict[str, Callable[[], ComponentStatus]] = {
    "database": check_database,
    "redis": check_redis,
    "ollama": check_ollama,
    "sandbox": check_sandbox,
    "brain": check_brain,
}


def collect(
    checks: dict[str, Callable[[], ComponentStatus]] | None = None, budget_s: float = TOTAL_BUDGET_S
) -> list[ComponentStatus]:
    """Exécute les checks en parallèle ; un check qui dépasse le budget est rapporté `unknown`
    (jamais bloquant pour la réponse, jamais un état positif par défaut)."""
    checks = CHECKS if checks is None else checks
    out: list[ComponentStatus] = []
    pool = ThreadPoolExecutor(max_workers=len(checks), thread_name_prefix="apex-status")
    try:
        started = {name: (time.perf_counter(), pool.submit(fn)) for name, fn in checks.items()}
        deadline = time.monotonic() + budget_s
        for name, (t0, future) in started.items():
            try:
                result = future.result(timeout=max(0.0, deadline - time.monotonic()))
            except FutureTimeout:
                out.append(ComponentStatus(name, UNKNOWN, f"non mesuré sous {budget_s:.0f}s"))
                continue
            except Exception as exc:
                result = ComponentStatus(name, ERROR, f"{exc.__class__.__name__}: {exc}"[:300])
            out.append(
                ComponentStatus(
                    result.component,
                    result.state,
                    result.detail,
                    round((time.perf_counter() - t0) * 1000, 1),
                )
            )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return out


def warm_up() -> threading.Thread:
    """Lance en arrière-plan les checks lents (détection du sandbox, import et attestation du
    cerveau) au démarrage d'`apex serve`, pour que la PREMIÈRE requête du Panel ne les voie pas
    dépasser le budget (mesuré : 3,8 s à froid pour le cerveau, 0,03 s ensuite). N'influence aucun
    état : le résultat de ce préchauffage n'est jamais renvoyé."""
    thread = threading.Thread(
        target=lambda: (check_sandbox(), check_brain()), name="apex-status-warmup", daemon=True
    )
    thread.start()
    return thread

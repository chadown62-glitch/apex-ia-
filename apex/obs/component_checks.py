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


_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


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
            if address is not None and address[0] in _LOOPBACK:
                # Sous Windows, rien n'écoutant sur 127.0.0.1, la connexion n'est pas refusée tout de suite : elle
                # expire (~2 s, constaté sur Windows 11 réel). Ce n'est pas un Ollama en panne.
                return ComponentStatus("ollama", OFFLINE, f"aucune réponse sous {NETWORK_TIMEOUT_S:.0f}s sur "
                                       f"{address[0]}:{address[1]} — Ollama n'est probablement pas démarré")
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


#: Rôles avec lesquels APEX GÉNÈRE (Chat, TaskLoop, missions) : ce qu'il faut pour répondre.
GENERATION_ROLES = ("coder_main", "fast")


def check_models(config: Any = None) -> ComponentStatus:
    """Inférence RÉELLEMENT possible maintenant : `detect()` du backend de chaque endpoint des rôles de
    génération (poids présents pour le backend natif, serveur joignable et modèle présent pour Ollama…).
    Avant le 06/10/2026 seul Ollama était sondé ici : un APEX servant un modèle natif s'affichait dégradé,
    et un APEX sans aucun modèle exécutable n'avait aucune ligne pour le dire."""
    from apex.config.loader import load_config
    from apex.models.provider import get_default_provider
    from apex.models.registry import resolve_candidates

    config = config if config is not None else load_config()
    generation = [c for c in resolve_candidates(config)
                  if c.role in GENERATION_ROLES and c.endpoint is not None and c.endpoint.enabled]
    endpoints = {(e.runtime, e.url, e.served_model): e for e in (c.endpoint for c in generation) if e is not None}
    seen: dict[tuple[str, str, str], tuple[bool, str]] = {}
    if endpoints:
        # En parallèle : sous Windows, une connexion refusée sur 127.0.0.1 met ~2 s à échouer (constaté sur
        # Windows 11 réel, 09/10/2026) ; quatre modèles Ollama sondés l'un après l'autre dépassaient le budget du
        # statut et « Modèles » restait UNKNOWN tant qu'Ollama n'était pas démarré.
        with ThreadPoolExecutor(max_workers=len(endpoints), thread_name_prefix="apex-models") as pool:
            futures = {key: pool.submit(lambda e=e: get_default_provider(e).detect()) for key, e in endpoints.items()}
            for key, future in futures.items():
                report = future.result()
                seen[key] = (report.is_available, report.detail)
    usable: list[str] = []
    brain = _conversational_brain()
    if brain is not None:
        usable.append(brain)
    for candidate in generation:
        endpoint = candidate.endpoint
        assert endpoint is not None
        key = (endpoint.runtime, endpoint.url, endpoint.served_model)
        if seen[key][0]:
            usable.append(f"{candidate.role} → {endpoint.served_model} ({endpoint.runtime})")
    if not seen and not usable:
        return ComponentStatus("models", OFFLINE,
                               "aucun endpoint de génération configuré (coder_main, fast)")
    if usable:
        return ComponentStatus("models", READY, f"exécutable(s) : {', '.join(dict.fromkeys(usable))}"[:300])
    # Une raison par cause (plusieurs modèles d'un même serveur absent partagent la leur), le backend en
    # processus d'abord : c'est l'action la plus directe (`apex models fetch`).
    grouped: dict[str, list[str]] = {}
    native_first = sorted(seen.items(), key=lambda kv: kv[0][0] != "transformers")
    for (runtime, _url, served), (_ok, detail) in native_first:
        grouped.setdefault(f"{runtime} : {detail}", []).append(served)
    reasons = " ; ".join(f"{', '.join(names)} ({why})" for why, names in grouped.items())
    return ComponentStatus("models", OFFLINE, f"aucun modèle exécutable — {reasons}"[:600])


def _conversational_brain() -> str | None:
    """Le Cerveau APEX servi, s'il sait converser (génération 2) et que ses poids sont intègres : il répond
    seul, sans Ollama ni autre modèle. Le Cerveau v1 (il continue un texte) n'est pas compté."""
    try:
        from apex.assistant.engines import brain_candidate
        from apex.models.provider import get_default_provider

        candidate = brain_candidate()
        report = get_default_provider(candidate.endpoint).detect()
    except Exception:  # un Cerveau illisible ne compte simplement pas
        return None
    if not (report.is_available and report.facts.get("conversational")):
        return None
    return f"Cerveau APEX → {candidate.endpoint.served_model} (apex_brain)"


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


# -- Composants de l'application : Cerveau, moteur intégré, GPU, Game Studio ------------------------------
#
# Chacun vient d'une vraie sonde (`detect()` du moteur, sonde de Game Studio `capabilities.probe()`,
# `blender --version`, `nvidia-smi`, activation GPU mesurée au démarrage). Ils sont FACULTATIFS : rapportés,
# jamais comptés dans le verdict global (APEX répond sans eux), et un composant présent mais jamais qualifié
# sur ce PC (aucune mission réussie, par exemple) est « degraded », jamais « ready ».

#: Résultats des sondes coûteuses, gardés ce temps-là (la sonde de Game Studio importe torch).
PROBE_CACHE_S = 60.0
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, ttl_s: float, compute: Callable[[], Any],
            now: Callable[[], float] = time.monotonic) -> Any:
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now() - hit[0] < ttl_s:
            return hit[1]
    value = compute()
    with _cache_lock:
        _cache[key] = (now(), value)
    return value


def check_apex_brain() -> ComponentStatus:
    """Le Cerveau APEX (ApexLM) servi : poids présents et intègres, conversationnel (`detect()`)."""
    try:
        from apex.assistant.engines import brain_candidate
        from apex.models.provider import get_default_provider

        report = get_default_provider(brain_candidate().endpoint).detect()
    except Exception as exc:
        return ComponentStatus("apex_brain", ERROR, f"{exc.__class__.__name__}: {exc}"[:300])
    if not report.is_available:
        return ComponentStatus("apex_brain", OFFLINE, report.detail[:300])
    if not report.facts.get("conversational"):
        return ComponentStatus("apex_brain", DEGRADED, report.detail[:300])
    return ComponentStatus("apex_brain", READY, report.detail[:300])


def check_native_model(config: Any = None) -> ComponentStatus:
    """Le moteur intégré (backend `transformers`, en processus) : poids du modèle par défaut présents."""
    try:
        from apex.config.loader import load_config
        from apex.models.provider import get_default_provider

        config = config if config is not None else load_config()
        endpoints = [e for e in config.endpoints if e.runtime == "transformers" and e.enabled]
        if not endpoints:
            return ComponentStatus("native_model", OFFLINE, "aucun moteur intégré configuré")
        reports = [(e, get_default_provider(e).detect()) for e in endpoints]
    except Exception as exc:
        return ComponentStatus("native_model", ERROR, f"{exc.__class__.__name__}: {exc}"[:300])
    ready = [e.served_model for e, r in reports if r.is_available]
    if ready:
        return ComponentStatus("native_model", READY, f"poids présents : {', '.join(ready)}"[:300])
    endpoint, report = reports[0]
    return ComponentStatus("native_model", OFFLINE, (
        f"{endpoint.served_model} : {report.detail} — « Installer le modèle » dans le Panel")[:300])


def _nvidia() -> dict[str, Any]:
    from apex.standalone import gpu

    driver: dict[str, Any] = _cached("nvidia", 300.0, gpu.detect_driver)
    return driver


def check_gpu() -> ComponentStatus:
    """GPU CUDA : ce que le dernier démarrage a MESURÉ (surcouche facultative, `apex.standalone.gpu`), ce
    que torch voit dans ce processus, et le pilote NVIDIA détecté. Jamais « prêt » sans mesure positive."""
    import sys

    try:
        from apex.standalone import gpu

        activation = gpu.last_activation() or {}
        torch = sys.modules.get("torch")  # jamais importé pour cette sonde : le backend l'a déjà chargé
        cuda = bool(torch is not None and torch.cuda.is_available())
        name = torch.cuda.get_device_name(0) if cuda and torch is not None else None
        state = activation.get("state")
        if cuda:
            how = (f"accélération GPU active ({activation.get('variant')})" if state == "active"
                   else "torch CUDA")
            return ComponentStatus("gpu", READY, f"{name} — {how}, sondé au démarrage"[:300])
        if state in ("inactive", "failed"):
            why = f"calcul sur le processeur : {activation.get('reason')}"
            return ComponentStatus("gpu", DEGRADED, why[:300])
        if state == "disabled":
            return ComponentStatus("gpu", OFFLINE, "accélération GPU désactivée : calcul sur le processeur")
        driver = _nvidia()
        if driver.get("found") and driver.get("gpus"):
            card = driver["gpus"][0]
            return ComponentStatus("gpu", OFFLINE, (
                f"GPU détecté : {card['name']} (pilote {card.get('driver_version')}, CUDA "
                f"{driver.get('cuda_driver') or '?'}) — accélération GPU non installée : « Accélération "
                "GPU (facultatif) » dans le Panel")[:300])
        why = f"calcul sur le processeur : {driver.get('reason') or 'aucun GPU'}"
        return ComponentStatus("gpu", OFFLINE, why[:300])
    except Exception as exc:
        return ComponentStatus("gpu", ERROR, f"{exc.__class__.__name__}: {exc}"[:300])


def _game_studio() -> tuple[str | None, dict[str, Any]]:
    """(raison de son absence, rapport de SA sonde) — Game Studio appartient à Codex : lu, jamais
    réimplémenté."""

    def compute() -> tuple[str | None, dict[str, Any]]:
        from apex.assistant.game_studio import _probe
        from apex.assistant.tools import register_game_studio
        from apex.tools.engine import ToolRegistry

        absent = register_game_studio(ToolRegistry())
        if absent is not None:
            return absent, {}
        try:
            return None, _probe(None) or {}
        except Exception as exc:
            return f"la sonde de Game Studio a échoué : {exc.__class__.__name__}: {exc}"[:300], {}

    result: tuple[str | None, dict[str, Any]] = _cached("game_studio", PROBE_CACHE_S, compute)
    return result


_VALIDATED = "available_validated"
_UNQUALIFIED = "available_unqualified"
_FAMILIES = (("vehicle_blender", "véhicule"), ("building_blender", "bâtiment"), ("chair_blender", "objet"))


def _validated(report: dict[str, Any]) -> list[str]:
    return [label for key, label in _FAMILIES if (report.get(key) or {}).get("status") == _VALIDATED]


def _missing(report: dict[str, Any]) -> list[str]:
    from apex.assistant.game_studio import missing_from

    return missing_from(report, "cpu")  # le calcul choisi n'entre pas ici : seulement ce qui manque


def check_game_studio() -> ComponentStatus:
    absent, report = _game_studio()
    if absent is not None:
        return ComponentStatus("game_studio", OFFLINE, absent)
    status = (report.get("environment") or {}).get("status")
    validated = _validated(report)
    if validated:
        return ComponentStatus("game_studio", READY, f"mission réussie sur ce PC ({', '.join(validated)})")
    if status == _UNQUALIFIED:
        return ComponentStatus("game_studio", DEGRADED,
                               "dépendances présentes ; aucune mission encore réussie sur ce PC (non "
                               "qualifié)")
    return ComponentStatus("game_studio", OFFLINE, ("manque : " + " ".join(_missing(report)))[:600])


def check_planner() -> ComponentStatus:
    absent, report = _game_studio()
    if absent is not None:
        return ComponentStatus("planner", OFFLINE, absent)
    vehicle = report.get("vehicle_blender") or {}
    pinned = bool(vehicle.get("coder_manifest_present") and vehicle.get("llama_cpp_present"))
    native = bool(vehicle.get("apex_native_coder_present"))
    if not (pinned or native):
        parts = []
        if not vehicle.get("coder_manifest_present"):
            parts.append("Qwen2.5-Coder-7B épinglé absent (APEX_HOME/.apex/game-studio/coder-model.json)")
        if not vehicle.get("llama_cpp_present"):
            parts.append("llama-cpp-python absent")
        parts.append("moteur intégré Qwen2.5-Coder absent")
        return ComponentStatus("planner", OFFLINE, "aucun planificateur : " + " ; ".join(parts))
    which = "Qwen2.5-Coder-7B épinglé (llama-cpp)" if pinned else "moteur intégré d'APEX (Qwen2.5-Coder)"
    if _validated(report):
        return ComponentStatus("planner", READY, f"{which} ; missions réussies sur ce PC")
    return ComponentStatus("planner", DEGRADED,
                           f"{which} présent ; non qualifié (aucune mission réussie sur ce PC)")


def _blender_version(path: str) -> str | None:
    import subprocess

    def compute() -> str | None:
        try:
            done = subprocess.run([path, "--background", "--version"],  # noqa: S603 — Blender du PATH
                                  capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        first = next((line.strip() for line in (done.stdout or "").splitlines() if line.strip()), "")
        return first if done.returncode == 0 and first.lower().startswith("blender") else None

    result: str | None = _cached(f"blender:{path}", 600.0, compute)
    return result


def check_blender() -> ComponentStatus:
    import shutil

    _, report = _game_studio()
    path = (report.get("vehicle_blender") or {}).get("blender") or shutil.which("blender")
    if not path:
        return ComponentStatus("blender", OFFLINE,
                               "Blender introuvable dans le PATH (requis par Game Studio)")
    version = _blender_version(str(path))
    if version is None:
        return ComponentStatus("blender", ERROR, f"{path} ne répond pas à « --version »")
    return ComponentStatus("blender", READY, f"{version} ({path})")


def check_game_graphics() -> ComponentStatus:
    absent, report = _game_studio()
    if absent is not None:
        return ComponentStatus("game_graphics", OFFLINE, absent)
    validated = _validated(report)
    if validated:
        return ComponentStatus("game_graphics", READY, f"jugé réussi sur ce PC : {', '.join(validated)}")
    if any((report.get(key) or {}).get("status") == _UNQUALIFIED for key, _ in _FAMILIES):
        return ComponentStatus("game_graphics", DEGRADED,
                               "véhicule, bâtiment, objet : prêts à essayer, non qualifiés sur ce PC")
    return ComponentStatus("game_graphics", OFFLINE, ("manque : " + " ".join(_missing(report)))[:600])


def check_game_engineer() -> ComponentStatus:
    from importlib.util import find_spec

    absent, report = _game_studio()
    if absent is not None:
        return ComponentStatus("game_engineer", OFFLINE, absent)
    if find_spec("jsonschema") is None:
        return ComponentStatus("game_engineer", OFFLINE,
                               "bibliothèque jsonschema absente (dépendance de Game Studio non déclarée)")
    engineer = report.get("game_engineer") or {}
    state = READY if engineer.get("status") == _VALIDATED else DEGRADED
    detail = str(engineer.get("reason") or engineer.get("status"))
    return ComponentStatus("game_engineer", state, detail[:300])


CHECKS: dict[str, Callable[[], ComponentStatus]] = {
    "database": check_database,
    "redis": check_redis,
    "ollama": check_ollama,
    "models": check_models,
    "sandbox": check_sandbox,
    "brain": check_brain,
    "apex_brain": check_apex_brain,
    "native_model": check_native_model,
    "gpu": check_gpu,
    "game_studio": check_game_studio,
    "planner": check_planner,
    "blender": check_blender,
    "game_graphics": check_game_graphics,
    "game_engineer": check_game_engineer,
}

#: Composants de l'application rapportés hors du verdict : facultatifs (APEX répond sans GPU ni Game
#: Studio), ou déjà comptés par « models » (Cerveau APEX, moteur intégré).
APPLICATION = frozenset({"apex_brain", "native_model", "gpu", "game_studio", "planner", "blender",
                         "game_graphics", "game_engineer"})

#: Composants rapportés mais hors du verdict global : Ollama n'est qu'un backend FACULTATIF parmi d'autres ;
#: ce qui compte (un modèle exécutable) est mesuré par « models ». Un Ollama absent ne dégrade donc plus un
#: APEX qui répond avec le backend natif — et un APEX sans aucun modèle reste dégradé via « models ».
INFORMATIONAL = frozenset({"ollama"}) | APPLICATION

#: Profil AUTONOME (`apex.standalone`, APEX.exe) : pas de Redis (aucun service d'infrastructure, base embarquée)
#: — le composant n'est pas rapporté du tout plutôt qu'« offline » ; le cerveau neuronal relève de la
#: Recherche, absent de cette distribution : rapporté, hors verdict.
STANDALONE_EXCLUDED = frozenset({"redis"})
STANDALONE_INFORMATIONAL = frozenset({"ollama", "brain"}) | APPLICATION


def profile_checks() -> dict[str, Callable[[], ComponentStatus]]:
    from apex.standalone import is_standalone

    if is_standalone():
        return {name: fn for name, fn in CHECKS.items() if name not in STANDALONE_EXCLUDED}
    return CHECKS


def informational() -> frozenset[str]:
    from apex.standalone import is_standalone

    return STANDALONE_INFORMATIONAL if is_standalone() else INFORMATIONAL


def not_applicable() -> frozenset[str]:
    """Composants que CE profil n'utilise pas (jamais mesurés, jamais rapportés) — le Panel n'affiche pas leur ligne."""
    from apex.standalone import is_standalone

    return STANDALONE_EXCLUDED if is_standalone() else frozenset()


def profile() -> str:
    from apex.standalone import STANDALONE, is_standalone

    return STANDALONE if is_standalone() else "server"


def collect(
    checks: dict[str, Callable[[], ComponentStatus]] | None = None, budget_s: float = TOTAL_BUDGET_S
) -> list[ComponentStatus]:
    """Exécute les checks en parallèle ; un check qui dépasse le budget est rapporté `unknown`
    (jamais bloquant pour la réponse, jamais un état positif par défaut)."""
    checks = profile_checks() if checks is None else checks
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
        target=lambda: (check_sandbox(), check_brain(), _game_studio(), check_blender()),
        name="apex-status-warmup", daemon=True,
    )
    thread.start()
    return thread

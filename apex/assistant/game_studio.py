"""Game Studio vu de l'assistant : disponibilité expliquée, lancement d'une mission PROPOSÉE par le Cerveau
(acte de l'utilisateur), suivi (état, résultats, aperçus), annulation, reprise d'une mission interrompue.

Tout passe par les outils de Game Studio (`apex.game_studio.delegation`, propriété de Codex) et le moteur
d'outils, avec l'espace de travail de la conversation (ou de son projet) : Game Studio ne rend une mission
qu'à l'espace de travail qui l'a lancée. Seuls les octets d'un aperçu sont lus par ses services, et
seulement pour un artefact que ses outils viennent de rendre à cette conversation. Rien de Game Studio n'est
réimplémenté ici.

Partage du GPU (le Cerveau APEX tient le bail `model-session.lock` tant qu'il est chargé sur CUDA) :
- Game Studio avec son propre adaptateur (`apex.game_studio.gpu`, Codex #29) demande lui-même le GPU autour
  de chaque étape, et refuse une étape si un autre prêt court : l'assistant ne prête donc rien ;
- pile antérieure (chaque étape prend le bail partagé via `resource_lease`, même sur le processeur, sans
  rien demander) : avant de lancer ou de reprendre une mission, l'assistant demande le GPU au Cerveau
  (`apex.models.gpu_handoff.request_gpu`) et le lui rend quand la mission ne tourne plus.
Pendant un prêt, le Cerveau répond sur le processeur, et ses mesures le disent."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from importlib.util import find_spec
from typing import Any


class GameStudioUnavailable(RuntimeError):
    """Game Studio absent de l'installation, ou impossible à charger."""


class AlreadyLaunched(ValueError):
    """La mission proposée par ce message a déjà été lancée."""


class GpuBusy(RuntimeError):
    """Le Cerveau APEX n'a pas libéré le GPU dans le délai : rien n'a été lancé."""


#: Un lancement à la fois : deux clics simultanés sur la même proposition ne lancent qu'une mission.
_LAUNCH_LOCK = threading.Lock()
#: Délai accordé au Cerveau pour finir ses générations en cours et quitter le GPU, au clic (secondes).
GPU_HANDOFF_TIMEOUT_S = 30.0
#: Intervalle entre deux lectures de l'état d'une mission qui détient le GPU (secondes).
LOAN_POLL_S = 3.0
#: États d'une mission qui détient encore le GPU (`ProjectService` de Game Studio).
LOAN_ACTIVE = frozenset({"queued", "running"})
#: Lectures en échec consécutives après lesquelles le GPU est rendu quand même.
LOAN_READ_FAILURES = 3
#: Prêts en cours : demandeur → signal d'arrêt de son suivi.
_LOANS: dict[str, threading.Event] = {}
#: Aperçus servis : images seulement, bornées (les fichiers restent sur la page Game Studio).
PREVIEW_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
PREVIEW_LIMIT = 4_000_000

#: Refus connus de Game Studio, expliqués à l'utilisateur (le code exact reste affiché à côté).
REFUSALS: dict[str, str] = {
    "cuda_unavailable_choose_cpu_explicitly": (
        "aucun GPU CUDA n'est disponible sur ce PC. Le calcul sur processeur, beaucoup plus lent, se "
        "choisit dans Paramètres > Outils > Calcul des missions."),
    "local_planner_model_missing": (
        "le modèle planificateur local de Game Studio (Qwen2.5-Coder-7B) n'est pas installé."),
    "cuda_game_studio_requires_qualified_brain_quiescence_handoff": (
        "Game Studio n'accepte encore que le calcul sur processeur : le partage du GPU avec le Cerveau "
        "n'est pas encore qualifié. Choisissez « Processeur » dans Paramètres > Outils > Calcul des "
        "missions."),
    "dynamic_planner_requires_pinned_7b_until_native_handoff_is_qualified": (
        "le planificateur natif d'APEX n'est pas encore qualifié pour Game Studio."),
    "shared_resource_profile_required": "le profil APEX (APEX_HOME) n'est pas défini pour partager le GPU.",
    "core_active_handoff_required": "une autre instance d'APEX utilise les ressources : fermez-la d'abord.",
    "external_graphics_workload_active": "Blender ou Unreal tourne déjà sur ce PC : fermez-le d'abord.",
    "external_model_workload_active": (
        "un autre serveur de modèle tourne déjà sur ce PC : arrêtez-le d'abord."),
    "game_studio_project_missing": "mission introuvable dans cette conversation.",
    "blender_worker_or_local_coder_missing": "Blender ou le planificateur local manque sur ce PC.",
    "pinned_7b_model_missing_no_download_performed": (
        "le modèle planificateur Qwen2.5-Coder-7B n'est pas installé (rien n'a été téléchargé)."),
    "game_studio_project_process_interrupted": "APEX a été arrêté pendant la mission.",
    "game_studio_process_interrupted": "APEX a été arrêté pendant la mission.",
    # Reprise (`ProjectService.resume` de Game Studio).
    "only_interrupted_failed_or_incomplete_projects_can_resume": (
        "seule une mission interrompue, échouée ou incomplète peut être reprise."),
    "project_already_running": "cette mission est déjà en cours.",
    "project_already_delivered_for_review": "cette mission est déjà livrée : ses fichiers sont prêts.",
    "cumulative_time_budget_exhausted": (
        "le temps total accordé à cette mission est épuisé : lancez une nouvelle mission."),
    "legacy_budget_unknown_new_mission_required": (
        "cette mission est trop ancienne pour être reprise : lancez une nouvelle mission."),
}
#: États dans lesquels l'Atelier 3D propose de reprendre une mission (Game Studio décide en dernier).
RESUMABLE = frozenset({"interrupted", "failed", "cancelled"})


def explain(message: str | None) -> str:
    """Le refus de Game Studio en clair ; un refus inconnu est rendu tel quel."""
    if not message:
        return "échec sans détail"
    known = REFUSALS.get(message)
    if known is not None:
        return known
    if "ModuleNotFoundError" in message or "No module named" in message:
        return f"une bibliothèque nécessaire à Game Studio manque sur ce PC ({message})."
    return message


def _engine(session_factory: Any, conversation_id: str, granted: frozenset[Any],
            service: Any) -> tuple[Any, Any, Any]:
    from apex.assistant.tools import register_game_studio
    from apex.assistant.workspace import conversation_workspace
    from apex.conversations.service import ConversationService
    from apex.tools.engine import ToolContext, ToolEngine, ToolRegistry

    conversation = ConversationService(session_factory).get(conversation_id)
    if conversation is None:
        raise LookupError(f"conversation « {conversation_id} » introuvable")
    registry = ToolRegistry()
    absent = register_game_studio(registry)
    if absent is not None:
        raise GameStudioUnavailable(absent)
    workspace = conversation_workspace(conversation.id, conversation.project_id)
    extra = {} if service is None else {"game_studio_service": service}
    context = ToolContext(workspace=workspace, granted=granted, extra=extra)
    return ToolEngine(registry), context, conversation


def _execute(engine: Any, context: Any, tool: str, arguments: dict[str, Any], requested_by: str) -> Any:
    from apex.tools.model import ToolCall

    return engine.execute(ToolCall(tool=tool, arguments=arguments, call_id=str(uuid.uuid4()),
                                   requested_by=requested_by), context)


def _call(engine: Any, context: Any, tool: str, project_id: str, conversation_id: str) -> dict[str, Any]:
    from apex.tools.model import ErrorCode

    result = _execute(engine, context, tool, {"project_id": project_id},
                      f"assistant:conversation:{conversation_id}")
    if result.ok:
        return dict(result.output)
    error = result.error
    if error is not None and error.code in (ErrorCode.NOT_FOUND, ErrorCode.INVALID_INPUT):
        # Mission inconnue, ou lancée depuis un autre espace de travail : jamais distinguées.
        raise LookupError(f"mission Game Studio « {project_id} » introuvable dans cette conversation")
    raise GameStudioUnavailable(f"{tool} : {explain(error.message if error else None)}"[:400])


def _permissions(*values: str) -> frozenset[Any]:
    from apex.assistant.modes import GAME_STUDIO_PERMISSIONS

    return frozenset(p for p in GAME_STUDIO_PERMISSIONS if p.value in values)


def studio_hands_off_gpu() -> bool:
    """Game Studio demande-t-il lui-même le GPU au Cerveau autour de chaque étape (`apex.game_studio.gpu`,
    Codex #29) ? Son adaptateur refuse une étape quand un autre prêt court (`brain_handoff_busy`) : un prêt
    de l'assistant pour toute la mission ferait alors échouer chaque étape."""
    try:
        return find_spec("apex.game_studio.gpu") is not None
    except (ImportError, ValueError):
        return False


def _loan_requester(name: str) -> str | None:
    """Le nom du prêt de l'assistant pour cette mission, ou None quand Game Studio s'en charge."""
    return None if studio_hands_off_gpu() else name


def _borrow_gpu(requester: str | None) -> dict[str, Any] | None:
    """Le GPU demandé au Cerveau pour une mission ; GpuBusy (rien n'est lancé) s'il ne le libère pas."""
    from apex.models.gpu_handoff import request_gpu

    if requester is None:
        return None
    ack = request_gpu(GPU_HANDOFF_TIMEOUT_S, requester=requester)
    if not ack.get("released"):
        raise GpuBusy(f"le Cerveau APEX n'a pas libéré le GPU pour Game Studio ({ack.get('reason')}) : "
                      "rien n'a été lancé, réessayez dans un instant")
    return ack


def _hand_back(requester: str | None) -> None:
    from apex.models.gpu_handoff import return_gpu

    if requester is None:
        return
    stop = _LOANS.pop(requester, None)
    if stop is not None:
        stop.set()
    return_gpu(requester=requester)


def _keep_lent(requester: str, read_status: Callable[[], str | None]) -> None:
    """Le GPU reste prêté tant que la mission tourne (lue par l'outil de suivi de Game Studio), puis il
    est rendu au Cerveau, aussi quand la mission est illisible plusieurs fois de suite."""
    stop = threading.Event()
    _LOANS[requester] = stop

    def watch() -> None:
        failures = 0
        try:
            while not stop.wait(LOAN_POLL_S):
                try:
                    status = read_status()
                except Exception:
                    failures += 1
                    if failures >= LOAN_READ_FAILURES:
                        return
                    continue
                failures = 0
                if status not in LOAN_ACTIVE:
                    return
        finally:
            if _LOANS.get(requester) is stop:
                _hand_back(requester)

    threading.Thread(target=watch, daemon=True, name=f"apex-gpu-loan-{requester}").start()


def _status_reader(session_factory: Any, conversation_id: str, project_id: str,
                   service: Any) -> Callable[[], str | None]:
    engine, context, _ = _engine(session_factory, conversation_id, _permissions("game_studio.read"),
                                 service)

    def read() -> str | None:
        return _call(engine, context, "game_studio.status", project_id, conversation_id).get("status")

    return read


def _reset_loans_for_tests() -> None:
    for requester in list(_LOANS):
        _hand_back(requester)


def follow(session_factory: Any, conversation_id: str, project_id: str, *,
           service: Any = None) -> dict[str, Any]:
    """État (`game_studio.status`) puis résultats (`game_studio.results`) d'une mission de cette
    conversation. Lecture seule : permission `game_studio.read` uniquement."""
    engine, context, _ = _engine(session_factory, conversation_id, _permissions("game_studio.read"),
                                 service)
    status = _call(engine, context, "game_studio.status", project_id, conversation_id)
    results = _call(engine, context, "game_studio.results", project_id, conversation_id)
    return {"project_id": project_id, "status": status, "results": results}


def cancel(session_factory: Any, conversation_id: str, project_id: str, *,
           service: Any = None) -> dict[str, Any]:
    """L'utilisateur annule une mission de cette conversation (`game_studio.cancel`) : acte humain, vérifié
    par le moteur de politique ; annuler n'engage aucune ressource."""
    from apex.policy.engine import PolicyEngine
    from apex.policy.model import Action, Subject

    PolicyEngine().authorize(Subject.HUMAN, Action.CANCEL_MISSION)
    engine, context, _ = _engine(session_factory, conversation_id, _permissions("game_studio.cancel"),
                                 service)
    return _call(engine, context, "game_studio.cancel", project_id, conversation_id)


def resume(session_factory: Any, conversation_id: str, project_id: str, *,
           service: Any = None) -> dict[str, Any]:
    """L'utilisateur reprend une mission de CETTE conversation, interrompue (APEX arrêté pendant la mission)
    ou échouée : acte humain, vérifié comme un lancement (la reprise relance le calcul). La mission est
    d'abord retrouvée par l'outil de suivi de Game Studio (une mission d'un autre espace de travail est
    introuvable), puis reprise par Game Studio lui-même, dans ses propres limites (budget cumulé, mission
    déjà livrée…). La reprise est enregistrée comme un nouveau message ; rend ce message."""
    from apex.conversations.model import MessageRole, MessageStatus
    from apex.conversations.repository import ConversationRepository, MessageRepository
    from apex.policy.engine import PolicyEngine
    from apex.policy.model import Action, Subject

    PolicyEngine().authorize(Subject.HUMAN, Action.CREATE_MISSION)
    engine, context, conversation = _engine(session_factory, conversation_id,
                                            _permissions("game_studio.read"), service)
    before = _call(engine, context, "game_studio.status", project_id, conversation_id)
    projects = service if service is not None else _default_services()[0]
    # Un prêt par reprise (une reprise refusée ne rend jamais le prêt d'une autre).
    requester = _loan_requester(f"game_studio:{project_id[:8]}-{uuid.uuid4().hex[:4]}")
    _borrow_gpu(requester)
    try:
        record = projects.resume(project_id)
    except ValueError as exc:
        _hand_back(requester)
        raise ValueError(explain(str(exc))) from exc
    except BaseException:
        _hand_back(requester)
        raise
    status = record.get("status") if isinstance(record, dict) else None
    if requester is not None:
        _keep_lent(requester, _status_reader(session_factory, conversation_id, project_id, service))
    content = f"Mission Game Studio reprise à votre demande : projet {project_id}, état « {status} »."
    meta = {"mode": "3d", "source": "apex", "action": "game_studio.resume",
            "game_studio": {"trigger": "utilisateur", "state": "resumed", "project_id": project_id,
                            "previous_status": before.get("status"), "status": status,
                            "authorization": "confirmée par l'utilisateur"}}
    message = MessageRepository(session_factory).create(
        conversation_id=conversation.id, role=MessageRole.ASSISTANT, content=content, model_id=None,
        provider_id=None, status=MessageStatus.REAL, error=None, meta=meta)
    ConversationRepository(session_factory).touch(conversation.id)
    return {"message_id": message.id, "content": content, "meta": meta}


def launch_proposed(session_factory: Any, conversation_id: str, message_id: str, *, compute: str,
                    service: Any = None) -> dict[str, Any]:
    """L'utilisateur confirme la mission que le Cerveau a PROPOSÉE dans le message `message_id` : acte
    humain (`CREATE_MISSION`, vérifié par le moteur de politique), une seule mission par proposition, la
    demande d'origine de l'utilisateur pour description, le calcul choisi dans les Paramètres. Le
    lancement est enregistré comme un nouveau message de la conversation (un message n'est jamais
    réécrit) ; rend ce message."""
    from apex.assistant.turns import game_studio_description
    from apex.conversations.model import MessageRole, MessageStatus
    from apex.conversations.repository import ConversationRepository, MessageRepository
    from apex.policy.engine import PolicyEngine
    from apex.policy.model import Action, Subject

    PolicyEngine().authorize(Subject.HUMAN, Action.CREATE_MISSION)
    messages = MessageRepository(session_factory).list_by_conversation(conversation_id)
    proposal = next((m for m in messages if m.id == message_id), None)
    record = (proposal.meta or {}).get("game_studio") if proposal is not None else None
    if proposal is None or not isinstance(record, dict) or record.get("state") != "proposed":
        raise LookupError("aucune mission proposée par ce message")
    request = next((m for m in messages if m.id == record.get("request_message_id")), None)
    if request is None or request.role is not MessageRole.USER:
        raise LookupError("la demande d'origine de cette proposition est introuvable")
    engine, context, conversation = _engine(session_factory, conversation_id,
                                            _permissions("game_studio.create"), service)
    with _LAUNCH_LOCK:
        launched = [m for m in MessageRepository(session_factory).list_by_conversation(conversation_id)
                    if ((m.meta or {}).get("game_studio") or {}).get("proposal_of") == message_id]
        if launched:
            raise AlreadyLaunched("cette mission proposée a déjà été lancée")
        arguments = {"description": game_studio_description(request.content), "compute_backend": compute}
        requester = _loan_requester(f"game_studio:launch-{message_id[:8]}")
        _borrow_gpu(requester)  # GpuBusy : rien n'est lancé, la proposition reste à confirmer
        try:
            result = _execute(engine, context, "game_studio.create", arguments,
                              f"human:conversation:{conversation_id}")
        except BaseException:
            _hand_back(requester)
            raise
        output = dict(result.output) if result.ok else {}
        error = result.error.to_dict() if result.error else None
        if requester is not None and result.ok and output.get("project_id"):
            _keep_lent(requester, _status_reader(session_factory, conversation_id,
                                                 str(output["project_id"]), service))
        else:
            _hand_back(requester)
        step = {"step": 1, "tool": "game_studio.create", "source": "utilisateur",
                "arguments": {**arguments, "description": arguments["description"][:600]}, "ok": result.ok,
                "observation": {"ok": result.ok, "output": output, "error": error},
                "duration_s": round(result.duration_s, 3)}
        if result.ok:
            content = (f"Mission Game Studio lancée à votre demande : projet {output.get('project_id')}, "
                       f"état « {output.get('status')} ». Suivi dans l'Atelier 3D.")
        else:
            content = f"Game Studio a refusé la mission : {explain(error and error.get('message'))}"
        meta = {"mode": "3d", "source": "apex", "action": "game_studio.launch", "steps": [step],
                "game_studio": {"trigger": "utilisateur", "state": "launched" if result.ok else "refused",
                                "launched": result.ok, "proposal_of": message_id,
                                "request_message_id": request.id,
                                "authorization": "confirmée par l'utilisateur",
                                "project_id": output.get("project_id"),
                                "mission_id": output.get("mission_id"),
                                "status": output.get("status"), "error": error,
                                **({"explanation": explain(error.get("message"))} if error else {})}}
        message = MessageRepository(session_factory).create(
            conversation_id=conversation.id, role=MessageRole.ASSISTANT, content=content, model_id=None,
            provider_id=None, status=MessageStatus.REAL, error=None, meta=meta)
    ConversationRepository(session_factory).touch(conversation.id)
    return {"message_id": message.id, "content": content, "meta": meta}


def _probe(probe: Any) -> dict[str, Any] | None:
    if probe is None:
        try:
            from apex.game_studio import (  # type: ignore[import-untyped, import-not-found, unused-ignore]
                capabilities,
            )
        except ImportError:
            return None
        probe = capabilities.probe
    report = probe()
    return report if isinstance(report, dict) else None


def availability(settings: Any, *, probe: Any = None) -> dict[str, Any]:
    """Ce que Game Studio peut faire sur CE PC, d'après sa propre sonde (`apex.game_studio.capabilities`),
    et ce qui manque, expliqué. Rien n'est supposé : une information absente de la sonde n'est jamais
    comptée comme présente."""
    from apex.assistant.tools import register_game_studio
    from apex.models.gpu_handoff import gpu_status
    from apex.tools.engine import ToolRegistry

    compute = settings.tools.game_studio_compute
    gpu = {k: v for k, v in gpu_status().items() if k in ("lease_held", "lent_to", "admissions_open",
                                                          "cuda_available", "cuda_residents")}
    # Lancement : toujours par l'utilisateur (acte humain), jamais par le Cerveau. Qui demande le GPU au
    # Cerveau pendant une mission : Game Studio autour de chaque étape, ou l'assistant pour la mission.
    base = {"launch": "human", "compute": compute, "gpu": gpu,
            "gpu_loan": "game_studio" if studio_hands_off_gpu() else "assistant"}
    absent = register_game_studio(ToolRegistry())
    if absent is not None:
        return {**base, "installed": False, "ready": False, "missing": [absent], "notes": []}
    try:
        report = _probe(probe) or {}
    except Exception as exc:  # sonde de Game Studio en échec : dit tel quel, jamais « prêt »
        return {**base, "installed": True, "ready": False, "notes": [],
                "missing": [f"la sonde de Game Studio a échoué : {exc.__class__.__name__}: {exc}"[:300]]}
    missing = missing_from(report, compute)
    cuda = report.get("cuda") or {}
    notes = [n for n in (cuda.get("reason"), (report.get("specialist") or {}).get("gpu_policy")) if n]
    environment = (report.get("environment") or {}).get("status")
    return {**base, "installed": True, "ready": not missing, "missing": missing, "notes": notes,
            "environment_status": environment, "cuda_available": bool(cuda.get("available"))}


def missing_from(report: dict[str, Any], compute: str) -> list[str]:
    """Ce qui manque sur CE PC d'après le rapport de la sonde de Game Studio, en clair (aussi lu par
    l'état des composants, `apex.obs.component_checks`)."""
    blender = report.get("vehicle_blender") or {}
    cuda = report.get("cuda") or {}
    missing: list[str] = []
    if not blender.get("blender"):
        where = ("(APEX_BLENDER_EXECUTABLE, PATH, Program Files)" if "blender_discovery" in blender
                 else "dans le PATH")
        missing.append(f"Blender n'est pas installé, ou introuvable {where}.")
    if blender.get("workers_present") is False:
        missing.append("Les scripts de Game Studio (scripts/game_graphics/studio) sont absents.")
    # Les missions que le Cerveau propose passent par la planification dynamique de Game Studio
    # (`create_dynamic`), qui exige le Qwen2.5-Coder-7B épinglé : le moteur intégré d'APEX n'y est pas
    # accepté (« dynamic_planner_requires_pinned_7b_until_native_handoff_is_qualified », Game Studio).
    if not (blender.get("coder_manifest_present") and blender.get("llama_cpp_present")):
        missing.append("Le modèle planificateur des missions (Qwen2.5-Coder-7B épinglé, avec "
                       "llama-cpp-python) n'est pas installé.")
    libraries = [name for name, ok in (blender.get("python_dependencies") or {}).items() if not ok]
    libraries += [name for name in ("jsonschema",) if find_spec(name) is None]
    if libraries:
        missing.append(f"Bibliothèques Python manquantes : {', '.join(libraries)}.")
    if compute != "cpu" and not cuda.get("available"):
        missing.append("Aucun GPU CUDA : choisissez « Processeur » dans Paramètres > Outils > Calcul des "
                       "missions (beaucoup plus lent), ou utilisez un PC équipé d'un GPU NVIDIA.")
    elif compute != "cpu" and cuda.get("dispatch_qualified") is False:
        # Garde de Game Studio : aucune mission sur GPU tant que le partage avec le Cerveau n'est pas
        # qualifié.
        missing.append("Game Studio n'accepte encore que le calcul sur processeur (partage du GPU avec "
                       "le Cerveau non qualifié) : choisissez « Processeur » dans Paramètres > Outils > "
                       "Calcul des missions.")
    return missing


def _default_services() -> tuple[Any, Any]:
    try:
        from apex.api import dependencies
    except ImportError as exc:  # pragma: no cover - API absente de l'installation
        raise GameStudioUnavailable(str(exc)) from exc
    project = getattr(dependencies, "get_game_studio_project_service", None)
    graphics = getattr(dependencies, "get_game_studio_service", None)
    if project is None or graphics is None:
        raise GameStudioUnavailable("services de Game Studio absents de cette installation")
    return project(), graphics()


def preview(session_factory: Any, conversation_id: str, project_id: str, digest: str, *,
            service: Any = None, graphics: Any = None) -> tuple[bytes, str]:
    """Octets d'un APERÇU (image `view`) que les résultats de cette mission viennent de rendre à cette
    conversation ; lus par les services de Game Studio, comme sa propre route d'artefacts."""
    results = follow(session_factory, conversation_id, project_id, service=service)["results"]
    item = next((a for a in results.get("artifacts") or [] if a.get("hash") == digest), None)
    if item is None or item.get("role") != "view":
        raise LookupError("aperçu introuvable dans les résultats de cette mission")
    if service is None or graphics is None:
        default_project, default_graphics = _default_services()
        service, graphics = service or default_project, graphics or default_graphics
    data, mime = (graphics.artifact(item["job_id"], digest) if item.get("job_id")
                  else service.artifact(project_id, digest))
    if mime not in PREVIEW_TYPES:
        raise ValueError(f"aperçu au format {mime} : ouvrez la page Game Studio pour ce fichier")
    if len(data) > PREVIEW_LIMIT:
        raise ValueError(f"aperçu trop gros pour être affiché ici ({len(data)} octets)")
    return data, mime


# -- qualité d'une mission : ce que vérifient les juges, versions, mesures des rendus, avis humains ----

#: Critères d'appréciation humaine (1 à 5), dans l'ordre de l'Atelier 3D. Ces avis calibreront les futurs
#: juges artistiques : ils ne décident de rien aujourd'hui.
RATING_CRITERIA: dict[str, str] = {
    "agressivite": "agressivité du design",
    "proportions": "cohérence des proportions",
    "details": "qualité des détails",
    "materiaux": "réalisme des matériaux",
    "lumiere": "qualité de la lumière",
    "fidelite": "fidélité à la demande et aux références",
    "impact": "impact visuel du rendu",
}
RATINGS_FILE = "game-studio-ratings.jsonl"
_RATINGS_LOCK = threading.Lock()
#: Mesures déjà calculées, par empreinte du rendu et du masque (un rendu livré ne change jamais).
_METRICS: dict[tuple[str, str | None, str | None], dict[str, Any]] = {}


def _studio_meta(message: Any) -> dict[str, Any]:
    record = (message.meta or {}).get("game_studio") if message is not None else None
    return record if isinstance(record, dict) else {}


def _brief_of(session_factory: Any, conversation_id: str, project_id: str) -> dict[str, Any] | None:
    """Le brief de la proposition dont le lancement a créé cette mission (lancement, puis proposition)."""
    from apex.conversations.repository import MessageRepository

    messages = MessageRepository(session_factory).list_by_conversation(conversation_id)
    launch = next((m for m in messages if _studio_meta(m).get("project_id") == project_id), None)
    proposal_id = _studio_meta(launch).get("proposal_of")
    proposal = next((m for m in messages if m.id == proposal_id), None) if proposal_id else None
    brief = _studio_meta(proposal).get("brief")
    return brief if isinstance(brief, dict) else None


def _requested_body_color(brief: dict[str, Any] | None) -> str | None:
    for color in (brief or {}).get("colors") or []:
        if color.get("part") == "carrosserie":
            return color.get("color")
    return None


def _measure(graphics: Any, job_id: str, view: dict[str, Any], mask: dict[str, Any] | None,
             requested: str | None) -> dict[str, Any]:
    from apex.assistant import render_metrics

    key = (view["hash"], mask["hash"] if mask else None, requested)
    if key not in _METRICS:
        image, _ = graphics.artifact(job_id, view["hash"])
        silhouette = graphics.artifact(job_id, mask["hash"])[0] if mask else None
        try:
            _METRICS[key] = render_metrics.measure(image, silhouette, requested=requested)
        except ValueError as exc:
            return {"view": view["name"], "error": str(exc)}
    return {"view": view["name"], **_METRICS[key]}


def _revision_quality(graphics: Any, child: dict[str, Any], requested: str | None) -> dict[str, Any]:
    """Versions d'une mission enfant, ce que son juge a vérifié pour la version retenue, et les mesures de
    ses rendus (avec la silhouette de la même vue quand Game Studio l'a produite)."""
    import json as _json

    from apex.assistant import render_metrics

    selected_number = child.get("selected_revision_number")
    versions = [{"number": r.get("number"), "verdict": r.get("verdict"),
                 "selected": r.get("number") == selected_number,
                 "defects": [d.get("id") for d in r.get("defects") or [] if isinstance(d, dict)],
                 "unavailable": list(r.get("unavailable") or [])} for r in child.get("revisions") or []]
    selected = next((r for r in child.get("revisions") or [] if r.get("number") == selected_number), None)
    judge: dict[str, Any] | None = None
    metrics: list[dict[str, Any]] = []
    if selected is not None:
        artifacts = selected.get("artifacts") or []
        report = next((a for a in artifacts if a.get("name") == "judge-report.json"), None)
        if report is not None:
            try:
                data = _json.loads(graphics.artifact(child["id"], report["hash"])[0])
                judge = {"scope": data.get("scope"), "criteria": data.get("criteria"),
                         "advisory": list(data.get("advisory") or []),
                         "unavailable": list(data.get("unavailable") or [])}
            except (LookupError, ValueError, TypeError, KeyError):
                judge = None
        if render_metrics.available():
            masks = {a.get("name"): a for a in artifacts if a.get("role") == "silhouette"}
            for view in [a for a in artifacts if a.get("role") == "view"][:4]:
                mask = masks.get("silhouette_" + view["name"])
                metrics.append(_measure(graphics, child["id"], view, mask, requested))
    return {"versions": versions, "selection_reason": child.get("selection_reason"), "judge": judge,
            "limitations": list(child.get("limitations") or []), "metrics": metrics}


def quality(session_factory: Any, conversation_id: str, project_id: str, *, service: Any = None,
            graphics: Any = None) -> dict[str, Any]:
    """La qualité d'une mission de cette conversation, sans rien certifier : le brief de la demande et le
    sort de chacun de ses éléments, les versions et verdicts de Game Studio, ce que son juge a vérifié et ce
    qu'il dit ne pas vérifier, des mesures objectives des rendus retenus (instrument non calibré,
    `apex.assistant.render_metrics`), et les avis humains déjà donnés. Lecture seule ; propriété vérifiée
    par les outils de Game Studio."""
    from apex.assistant import render_metrics

    follow(session_factory, conversation_id, project_id, service=service)  # mission de cette conversation
    if service is None or graphics is None:
        default_project, default_graphics = _default_services()
        service, graphics = service or default_project, graphics or default_graphics
    record = service.get(project_id)
    brief = _brief_of(session_factory, conversation_id, project_id)
    requested = _requested_body_color(brief)
    finished = record.get("status") not in LOAN_ACTIVE
    steps = []
    for step in record.get("steps") or []:
        child = step.get("mission") or {}
        item = {"key": step.get("key"), "kind": step.get("kind"), "status": step.get("status"),
                "handoff": (step.get("handoff") or {}).get("status")}
        if child and finished:
            item.update(_revision_quality(graphics, child, requested))
        steps.append(item)
    return {"project_id": project_id, "status": record.get("status"),
            "delivery_status": record.get("delivery_status"), "brief": brief, "steps": steps,
            "limitations": list(record.get("limitations") or []),
            "metrics_available": render_metrics.available(), "criteria": RATING_CRITERIA,
            "ratings": ratings(project_id)}


def _ratings_path() -> Any:
    from apex.standalone import paths

    return paths().home / ".apex" / RATINGS_FILE


def ratings(project_id: str) -> list[dict[str, Any]]:
    """Les avis humains déjà donnés sur cette mission (fichier local, une ligne JSON par avis)."""
    import json as _json

    path = _ratings_path()
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = _json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("project_id") == project_id:
            out.append(entry)
    return out


def rate(session_factory: Any, conversation_id: str, project_id: str, scores: dict[str, Any],
         comment: str = "", *, service: Any = None) -> dict[str, Any]:
    """L'avis de l'utilisateur sur le rendu livré, critère par critère (1 à 5), avec ce qu'il a vu
    (empreintes des aperçus) et le brief : une donnée de calibration pour les futurs juges, jamais un
    verdict."""
    import json as _json
    from datetime import UTC, datetime

    unknown = sorted(set(scores) - set(RATING_CRITERIA))
    if unknown:
        raise ValueError(f"critère inconnu : {', '.join(unknown)}")
    if not scores or any(type(v) is not int or not 1 <= v <= 5 for v in scores.values()):
        raise ValueError("chaque note est un entier de 1 à 5, et au moins un critère est noté")
    if len(comment) > 1000:
        raise ValueError("commentaire limité à 1 000 caractères")
    followed = follow(session_factory, conversation_id, project_id, service=service)
    results = followed["results"]
    if not results.get("ready"):
        raise ValueError("la mission n'a pas encore livré de rendu à apprécier")
    views = [a["hash"] for a in results.get("artifacts") or [] if a.get("role") == "view"]
    brief = _brief_of(session_factory, conversation_id, project_id)
    entry = {"id": str(uuid.uuid4()), "at": datetime.now(UTC).isoformat(), "project_id": project_id,
             "conversation_id": conversation_id, "scores": scores, "comment": comment.strip(),
             "views": views, "delivery_status": results.get("delivery_status"),
             "brief_version": (brief or {}).get("version"), "rater": "utilisateur"}
    path = _ratings_path()
    with _RATINGS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    return entry

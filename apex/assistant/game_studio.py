"""Game Studio vu de l'assistant : disponibilité expliquée, lancement d'une mission PROPOSÉE par le Cerveau
(acte de l'utilisateur), suivi (état, résultats, aperçus), annulation.

Tout passe par les outils de Game Studio (`apex.game_studio.delegation`, propriété de Codex) et le moteur
d'outils, avec l'espace de travail de la conversation (ou de son projet) : Game Studio ne rend une mission
qu'à l'espace de travail qui l'a lancée. Seuls les octets d'un aperçu sont lus par ses services, et
seulement pour un artefact que ses outils viennent de rendre à cette conversation. Rien de Game Studio n'est
réimplémenté ici."""

from __future__ import annotations

import threading
import uuid
from importlib.util import find_spec
from typing import Any


class GameStudioUnavailable(RuntimeError):
    """Game Studio absent de l'installation, ou impossible à charger."""


class AlreadyLaunched(ValueError):
    """La mission proposée par ce message a déjà été lancée."""


#: Un lancement à la fois : deux clics simultanés sur la même proposition ne lancent qu'une mission.
_LAUNCH_LOCK = threading.Lock()
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
    "dynamic_planner_requires_pinned_7b_until_native_handoff_is_qualified": (
        "le planificateur natif d'APEX n'est pas encore qualifié pour Game Studio."),
    "shared_resource_profile_required": "le profil APEX (APEX_HOME) n'est pas défini pour partager le GPU.",
    "core_active_handoff_required": "une autre instance d'APEX utilise les ressources : fermez-la d'abord.",
    "external_graphics_workload_active": "Blender ou Unreal tourne déjà sur ce PC : fermez-le d'abord.",
    "external_model_workload_active": (
        "un autre serveur de modèle tourne déjà sur ce PC : arrêtez-le d'abord."),
    "game_studio_project_missing": "mission introuvable dans cette conversation.",
}


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
        result = _execute(engine, context, "game_studio.create", arguments,
                          f"human:conversation:{conversation_id}")
        output = dict(result.output) if result.ok else {}
        error = result.error.to_dict() if result.error else None
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
    # Lancement : toujours par l'utilisateur (acte humain), jamais par le Cerveau.
    base = {"launch": "human", "compute": compute, "gpu": gpu}
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
        missing.append("Blender n'est pas installé, ou introuvable dans le PATH.")
    if blender.get("workers_present") is False:
        missing.append("Les scripts de Game Studio (scripts/game_graphics/studio) sont absents.")
    planner = (blender.get("coder_manifest_present") and blender.get("llama_cpp_present")) or blender.get(
        "apex_native_coder_present")
    if not planner:
        missing.append("Le modèle planificateur local (Qwen2.5-Coder-7B épinglé et llama-cpp-python) n'est "
                       "pas installé.")
    libraries = [name for name, ok in (blender.get("python_dependencies") or {}).items() if not ok]
    libraries += [name for name in ("jsonschema",) if find_spec(name) is None]
    if libraries:
        missing.append(f"Bibliothèques Python manquantes : {', '.join(libraries)}.")
    if compute != "cpu" and not cuda.get("available"):
        missing.append("Aucun GPU CUDA : choisissez « Processeur » dans Paramètres > Outils > Calcul des "
                       "missions (beaucoup plus lent), ou utilisez un PC équipé d'un GPU NVIDIA.")
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

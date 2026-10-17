"""API de l'assistant (0.4.0) — modes, paramètres, tours asynchrones, espace de travail, projets complets et
pièces jointes. Mêmes règles que le reste de l'API : jeton exigé (inclusion du routeur), aucune donnée inventée,
erreur de l'appelant -> 4xx explicite, panne -> enveloppe `unknown` avec la cause.

Parcours d'un message : `POST /conversations/{id}/turns` (message utilisateur inséré, tour en file) ->
`GET /turns/{id}` (état, étapes d'outils) -> message de l'assistant dans `GET /conversations/{id}/messages`.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from apex.api.schemas import DataEnvelope

router = APIRouter(tags=["assistant"])


class TurnIn(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class ProjectPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    instructions: str | None = Field(default=None, max_length=8000)


class ProjectCreateFullIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    instructions: str = Field(default="", max_length=8000)


class AttachmentIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    #: Contenu du fichier en base64 (aucune dépendance multipart) — taille vérifiée après décodage.
    content_base64: str = Field(min_length=1, max_length=800_000)


class ConversationIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    project_id: str | None = None
    mode: str = "normal"


class ConversationPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    mode: str | None = None


def _factory() -> Any:
    from apex.api.dependencies import get_session_factory

    return get_session_factory()


def _client_error(exc: Exception) -> HTTPException:
    from apex.assistant.attachments import AttachmentRefused
    from apex.assistant.turns import TurnBusy

    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, TurnBusy):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (AttachmentRefused, ValueError)):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


def _project_out(project: Any) -> dict[str, Any]:
    return {"id": project.id, "name": project.name, "description": project.description,
            "instructions": project.instructions, "created_by": project.created_by,
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat() if project.updated_at else None}


def _conversation_out(conversation: Any) -> dict[str, Any]:
    return {"id": conversation.id, "title": conversation.title, "project_id": conversation.project_id,
            "mode": conversation.mode, "created_by": conversation.created_by,
            "created_at": conversation.created_at.isoformat(), "updated_at": conversation.updated_at.isoformat()}


def _message_out(message: Any) -> dict[str, Any]:
    return {"id": message.id, "conversation_id": message.conversation_id, "role": message.role.value,
            "content": message.content, "model_id": message.model_id, "provider_id": message.provider_id,
            "status": message.status.value, "error": message.error, "created_at": message.created_at.isoformat(),
            "meta": message.meta}


# -- modes et paramètres ----------------------------------------------------------------------------------


@router.get("/assistant/modes", response_model=DataEnvelope[list[dict[str, Any]]])
def list_modes() -> DataEnvelope[list[dict[str, Any]]]:
    from apex.assistant.modes import MODES
    from apex.assistant.tools import assistant_registry

    available = {spec.name for spec in assistant_registry().specs()}  # Game Studio absent : non annoncé
    return DataEnvelope(status="real", data=[mode.describe(available) for mode in MODES.values()])


@router.get("/assistant/settings", response_model=DataEnvelope[dict[str, Any]])
def get_settings() -> DataEnvelope[dict[str, Any]]:
    from apex.assistant.settings import SettingsService

    try:
        settings, problems = SettingsService(_factory()).load()
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data={**settings.model_dump(), "problems": problems})


@router.put("/assistant/settings", response_model=DataEnvelope[dict[str, Any]])
def update_settings(changes: dict[str, Any]) -> DataEnvelope[dict[str, Any]]:
    from apex.assistant.settings import SettingsService

    try:
        settings = SettingsService(_factory()).update(changes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DataEnvelope(status="real", data={**settings.model_dump(), "problems": []})


@router.get("/assistant/engines", response_model=DataEnvelope[list[dict[str, Any]]])
def list_engines() -> DataEnvelope[list[dict[str, Any]]]:
    """Moteurs de réponse (Automatique, Ollama, APEX intégré, Cerveau APEX), leurs modèles et leur disponibilité
    MESURÉE (`apex.assistant.engines`)."""
    try:
        from apex.assistant.engines import describe
        from apex.config.loader import load_config

        data = describe(load_config())
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=data)


@router.get("/assistant/models", response_model=DataEnvelope[list[dict[str, Any]]])
def list_models() -> DataEnvelope[list[dict[str, Any]]]:
    """Modèles déclarés et leur disponibilité MESURÉE (détection réelle, rien n'est chargé ni téléchargé)."""
    try:
        from apex.config.loader import load_config
        from apex.models.provider import get_default_provider
        from apex.models.registry import resolve_candidates

        data, seen = [], set()
        for candidate in resolve_candidates(load_config()):
            if candidate.ref.id in seen:
                continue  # un modèle peut servir plusieurs rôles : une seule ligne
            seen.add(candidate.ref.id)
            endpoint = candidate.endpoint
            if endpoint is None or not endpoint.enabled:
                data.append({"id": candidate.ref.id, "runtime": None, "served_model": None, "available": False,
                             "detail": "aucun endpoint" if endpoint is None else "désactivé en configuration"})
                continue
            report = get_default_provider(endpoint).detect()
            data.append({"id": candidate.ref.id, "runtime": endpoint.runtime, "served_model": endpoint.served_model,
                         "available": report.is_available, "detail": report.detail})
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=data)


# -- projets ---------------------------------------------------------------------------------------------


@router.post("/assistant/projects", response_model=DataEnvelope[dict[str, Any]])
def create_project(payload: ProjectCreateFullIn) -> DataEnvelope[dict[str, Any]]:
    from apex.policy.model import Subject
    from apex.projects.service import ProjectService

    try:
        project = ProjectService(_factory()).create(name=payload.name, description=payload.description,
                                                   instructions=payload.instructions, created_by=Subject.HUMAN)
    except (ValueError, LookupError) as exc:
        raise _client_error(exc) from exc
    return DataEnvelope(status="real", data=_project_out(project))


@router.get("/assistant/projects", response_model=DataEnvelope[list[dict[str, Any]]])
def list_projects() -> DataEnvelope[list[dict[str, Any]]]:
    from apex.projects.service import ProjectService

    try:
        projects = ProjectService(_factory()).list_all()
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_project_out(p) for p in projects])


@router.get("/assistant/projects/{project_id}", response_model=DataEnvelope[dict[str, Any]])
def get_project(project_id: str) -> DataEnvelope[dict[str, Any]]:
    from apex.projects.service import ProjectService

    project = ProjectService(_factory()).get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"projet « {project_id} » introuvable")
    return DataEnvelope(status="real", data=_project_out(project))


@router.patch("/assistant/projects/{project_id}", response_model=DataEnvelope[dict[str, Any]])
def update_project(project_id: str, payload: ProjectPatchIn) -> DataEnvelope[dict[str, Any]]:
    from apex.policy.model import Subject
    from apex.projects.service import ProjectService

    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=422, detail="aucun champ à modifier")
    try:
        project = ProjectService(_factory()).update(project_id, requested_by=Subject.HUMAN, **fields)
    except (ValueError, LookupError) as exc:
        raise _client_error(exc) from exc
    return DataEnvelope(status="real", data=_project_out(project))


@router.delete("/assistant/projects/{project_id}", response_model=DataEnvelope[dict[str, Any]])
def delete_project(project_id: str) -> DataEnvelope[dict[str, Any]]:
    from apex.api.dependencies import get_turn_manager
    from apex.conversations.service import ConversationService
    from apex.policy.model import Subject
    from apex.projects.service import ProjectService

    factory = _factory()
    conversation_ids = [c.id for c in ConversationService(factory).list_filtered(project_id=project_id,
                                                                                 standalone=False)]
    if get_turn_manager().busy(conversation_ids):
        raise HTTPException(status_code=409, detail="une réponse est en cours dans une conversation de ce projet : "
                                                    "attendez-la ou annulez-la avant de supprimer le projet")
    try:
        counts = ProjectService(factory).delete(project_id, requested_by=Subject.HUMAN)
    except (ValueError, LookupError) as exc:
        raise _client_error(exc) from exc
    return DataEnvelope(status="real", data={"deleted": True, **counts})


@router.get("/assistant/projects/{project_id}/attachments", response_model=DataEnvelope[list[dict[str, Any]]])
def list_attachments(project_id: str) -> DataEnvelope[list[dict[str, Any]]]:
    from apex.assistant.attachments import AttachmentService

    return DataEnvelope(status="real", data=[a.describe() for a in AttachmentService(_factory()).list_all(project_id)])


@router.post("/assistant/projects/{project_id}/attachments", response_model=DataEnvelope[dict[str, Any]])
def add_attachment(project_id: str, payload: AttachmentIn) -> DataEnvelope[dict[str, Any]]:
    from apex.assistant.attachments import AttachmentService

    try:
        raw = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="contenu base64 invalide") from exc
    try:
        attachment = AttachmentService(_factory()).add(project_id, payload.filename, raw)
    except (ValueError, LookupError) as exc:
        raise _client_error(exc) from exc
    return DataEnvelope(status="real", data=attachment.describe())


@router.delete("/assistant/attachments/{attachment_id}", response_model=DataEnvelope[bool])
def delete_attachment(attachment_id: str) -> DataEnvelope[bool]:
    from apex.assistant.attachments import AttachmentService

    deleted = AttachmentService(_factory()).delete(attachment_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"pièce jointe « {attachment_id} » introuvable")
    return DataEnvelope(status="real", data=True)


# -- conversations ----------------------------------------------------------------------------------------


@router.get("/assistant/conversations", response_model=DataEnvelope[list[dict[str, Any]]])
def list_conversations(project_id: str | None = None, standalone: bool = False) -> DataEnvelope[list[dict[str, Any]]]:
    from apex.conversations.service import ConversationService

    try:
        conversations = ConversationService(_factory()).list_filtered(project_id=project_id, standalone=standalone)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=[_conversation_out(c) for c in conversations])


@router.post("/assistant/conversations", response_model=DataEnvelope[dict[str, Any]])
def create_conversation(payload: ConversationIn) -> DataEnvelope[dict[str, Any]]:
    from apex.conversations.service import ConversationService
    from apex.policy.model import Subject

    try:
        conversation = ConversationService(_factory()).create(
            title=payload.title, created_by=Subject.HUMAN, project_id=payload.project_id, mode=payload.mode)
    except (ValueError, LookupError) as exc:
        raise _client_error(exc) from exc
    return DataEnvelope(status="real", data=_conversation_out(conversation))


@router.get("/assistant/conversations/{conversation_id}", response_model=DataEnvelope[dict[str, Any]])
def get_conversation(conversation_id: str) -> DataEnvelope[dict[str, Any]]:
    from apex.conversations.service import ConversationService

    conversation = ConversationService(_factory()).get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail=f"conversation « {conversation_id} » introuvable")
    return DataEnvelope(status="real", data=_conversation_out(conversation))


@router.delete("/assistant/conversations/{conversation_id}", response_model=DataEnvelope[bool])
def delete_conversation(conversation_id: str) -> DataEnvelope[bool]:
    """Supprime la conversation, ses messages et ses tours ; refusée (409) tant qu'une réponse y est en cours."""
    from apex.api.dependencies import get_turn_manager
    from apex.conversations.service import ConversationService
    from apex.policy.model import Subject

    service = ConversationService(_factory())
    if service.get(conversation_id) is None:
        raise HTTPException(status_code=404, detail=f"conversation « {conversation_id} » introuvable")
    if get_turn_manager().busy([conversation_id]):
        raise HTTPException(status_code=409, detail="une réponse est en cours dans cette conversation : "
                                                    "attendez-la ou annulez-la avant de la supprimer")
    return DataEnvelope(status="real", data=service.delete(conversation_id, requested_by=Subject.HUMAN))


@router.patch("/assistant/conversations/{conversation_id}", response_model=DataEnvelope[dict[str, Any]])
def update_conversation(conversation_id: str, payload: ConversationPatchIn) -> DataEnvelope[dict[str, Any]]:
    from apex.conversations.service import ConversationService
    from apex.policy.model import Subject

    service = ConversationService(_factory())
    try:
        conversation = None
        if payload.title is not None:
            conversation = service.rename(conversation_id, title=payload.title, requested_by=Subject.HUMAN)
        if payload.mode is not None:
            conversation = service.set_mode(conversation_id, mode=payload.mode, requested_by=Subject.HUMAN)
    except (ValueError, LookupError) as exc:
        raise _client_error(exc) from exc
    if conversation is None:
        raise HTTPException(status_code=422, detail="aucun champ à modifier")
    return DataEnvelope(status="real", data=_conversation_out(conversation))


@router.get("/assistant/conversations/{conversation_id}/messages", response_model=DataEnvelope[list[dict[str, Any]]])
def list_messages(conversation_id: str) -> DataEnvelope[list[dict[str, Any]]]:
    from apex.conversations.service import ConversationService

    service = ConversationService(_factory())
    if service.get(conversation_id) is None:
        raise HTTPException(status_code=404, detail=f"conversation « {conversation_id} » introuvable")
    return DataEnvelope(status="real", data=[_message_out(m) for m in service.list_messages(conversation_id)])


# -- tours ---------------------------------------------------------------------------------------------------


@router.post("/assistant/conversations/{conversation_id}/turns", response_model=DataEnvelope[dict[str, Any]],
             status_code=202)
def submit_turn(conversation_id: str, payload: TurnIn) -> DataEnvelope[dict[str, Any]]:
    from apex.api.dependencies import get_turn_manager

    try:
        user_message, turn = get_turn_manager().submit(conversation_id, payload.content)
    except (ValueError, LookupError) as exc:
        raise _client_error(exc) from exc
    except RuntimeError as exc:
        raise _client_error(exc) from exc
    return DataEnvelope(status="real", data={"user_message": _message_out(user_message), "turn": turn.to_dict()})


@router.get("/assistant/conversations/{conversation_id}/turns", response_model=DataEnvelope[list[dict[str, Any]]])
def list_turns(conversation_id: str) -> DataEnvelope[list[dict[str, Any]]]:
    from apex.api.dependencies import get_turn_manager

    return DataEnvelope(status="real", data=[t.to_dict() for t in get_turn_manager().by_conversation(conversation_id)])


@router.get("/assistant/turns/{turn_id}", response_model=DataEnvelope[dict[str, Any]])
def get_turn(turn_id: str) -> DataEnvelope[dict[str, Any]]:
    from apex.api.dependencies import get_turn_manager

    turn = get_turn_manager().get(turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail=f"tour « {turn_id} » introuvable")
    return DataEnvelope(status="real", data=turn.to_dict())


@router.post("/assistant/turns/{turn_id}/cancel", response_model=DataEnvelope[dict[str, Any]])
def cancel_turn(turn_id: str) -> DataEnvelope[dict[str, Any]]:
    from apex.api.dependencies import get_turn_manager

    requested = get_turn_manager().cancel(turn_id)
    return DataEnvelope(status="real", data={"cancel_requested": requested})


# -- missions Game Studio d'une conversation (mode 3D) -----------------------------------------------------


def _game_studio_error(exc: Exception) -> HTTPException:
    from apex.assistant.game_studio import AlreadyLaunched, GameStudioUnavailable, GpuBusy
    from apex.errors import PolicyDenied

    if isinstance(exc, (PermissionError, PolicyDenied)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (AlreadyLaunched, GpuBusy)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, GameStudioUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    return _client_error(exc)


class GameStudioLaunchIn(BaseModel):
    #: Message de l'assistant qui PROPOSE la mission (sa demande d'origine est retrouvée côté serveur).
    message_id: str = Field(min_length=1, max_length=64)


@router.get("/assistant/game-studio/availability", response_model=DataEnvelope[dict[str, Any]])
def game_studio_availability() -> DataEnvelope[dict[str, Any]]:
    """Game Studio sur ce PC : installé, prêt, et ce qui manque, expliqué (sonde de Game Studio)."""
    from apex.assistant import game_studio
    from apex.assistant.settings import SettingsService

    settings, _ = SettingsService(_factory()).load()
    return DataEnvelope(status="real", data=game_studio.availability(settings))


@router.get("/assistant/gpu", response_model=DataEnvelope[dict[str, Any]])
def gpu_sharing() -> DataEnvelope[dict[str, Any]]:
    """Partage du GPU entre les modèles principaux et les spécialistes (`apex.models.gpu_handoff`) :
    bail, prêt en cours, dernier accusé de réception ; et l'accélération GPU facultative telle que le
    dernier démarrage l'a mesurée (`apex.standalone.gpu` : active, absente, ou pourquoi inactive)."""
    from apex.models.gpu_handoff import gpu_status
    from apex.standalone import gpu

    return DataEnvelope(status="real", data={**gpu_status(), "component": gpu.last_activation()})


@router.post("/assistant/conversations/{conversation_id}/game-studio/launch",
             response_model=DataEnvelope[dict[str, Any]])
def launch_game_studio_mission(conversation_id: str,
                               payload: GameStudioLaunchIn) -> DataEnvelope[dict[str, Any]]:
    """L'utilisateur lance la mission que le Cerveau a proposée : une seule par proposition."""
    from apex.assistant import game_studio
    from apex.assistant.settings import SettingsService
    from apex.errors import PolicyDenied

    settings, _ = SettingsService(_factory()).load()
    try:
        return DataEnvelope(status="real", data=game_studio.launch_proposed(
            _factory(), conversation_id, payload.message_id, compute=settings.tools.game_studio_compute))
    except (LookupError, PermissionError, PolicyDenied, RuntimeError, ValueError) as exc:
        raise _game_studio_error(exc) from exc


@router.get("/assistant/conversations/{conversation_id}/game-studio/{project_id}",
            response_model=DataEnvelope[dict[str, Any]])
def game_studio_mission(conversation_id: str, project_id: str) -> DataEnvelope[dict[str, Any]]:
    """État et résultats (empreintes, jamais les octets) d'une mission que Game Studio exécute pour cette
    conversation ; une mission d'un autre espace de travail est introuvable ici."""
    from apex.assistant import game_studio

    try:
        return DataEnvelope(status="real", data=game_studio.follow(_factory(), conversation_id, project_id))
    except (LookupError, PermissionError, RuntimeError, ValueError) as exc:
        raise _game_studio_error(exc) from exc


@router.get("/assistant/conversations/{conversation_id}/game-studio/{project_id}/preview/{digest}",
            response_model=DataEnvelope[dict[str, Any]])
def game_studio_preview(conversation_id: str, project_id: str, digest: str) -> DataEnvelope[dict[str, Any]]:
    """Un aperçu (image) rendu par cette mission, en base64 ; les autres fichiers restent sur la page Game
    Studio."""
    from apex.assistant import game_studio

    try:
        data, mime = game_studio.preview(_factory(), conversation_id, project_id, digest)
    except (LookupError, PermissionError, RuntimeError, ValueError) as exc:
        raise _game_studio_error(exc) from exc
    return DataEnvelope(status="real", data={"content_type": mime, "size": len(data),
                                             "content_base64": base64.b64encode(data).decode("ascii")})


@router.post("/assistant/conversations/{conversation_id}/game-studio/{project_id}/resume",
             response_model=DataEnvelope[dict[str, Any]])
def resume_game_studio_mission(conversation_id: str, project_id: str) -> DataEnvelope[dict[str, Any]]:
    """L'utilisateur reprend une mission interrompue ou échouée de cette conversation ; un refus de Game
    Studio (mission déjà livrée, budget épuisé…) est rendu en clair (409)."""
    from apex.assistant import game_studio
    from apex.errors import PolicyDenied

    try:
        return DataEnvelope(status="real", data=game_studio.resume(_factory(), conversation_id, project_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (LookupError, PermissionError, PolicyDenied, RuntimeError) as exc:
        raise _game_studio_error(exc) from exc


class StudioInstallIn(BaseModel):
    #: L'utilisateur a lu la taille, la provenance et la licence, et accepte le téléchargement.
    confirm: bool = False


@router.get("/assistant/game-studio/dependencies", response_model=DataEnvelope[dict[str, Any]])
def game_studio_dependencies() -> DataEnvelope[dict[str, Any]]:
    """Chaque composant dont Game Studio a besoin : état mesuré, taille et provenance connues, et son
    installation, offerte seulement quand Game Studio en publie le contrat
    (`apex.assistant.studio_setup`)."""
    from apex.assistant import studio_setup

    return DataEnvelope(status="real", data=studio_setup.dependencies())


@router.post("/assistant/game-studio/dependencies/{item_id}/install",
             response_model=DataEnvelope[dict[str, Any]])
def install_game_studio_dependency(item_id: str, payload: StudioInstallIn) -> DataEnvelope[dict[str, Any]]:
    """L'utilisateur installe UN composant par le contrat de Game Studio, après confirmation explicite ;
    l'installation tourne en arrière-plan (son état est relu par `GET …/dependencies`)."""
    from apex.assistant import studio_setup

    try:
        job = studio_setup.start_install(item_id, confirmed=payload.confirm)
        return DataEnvelope(status="real", data=job)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/assistant/conversations/{conversation_id}/game-studio/{project_id}/cancel",
             response_model=DataEnvelope[dict[str, Any]])
def cancel_game_studio_mission(conversation_id: str, project_id: str) -> DataEnvelope[dict[str, Any]]:
    """L'utilisateur annule une mission de cette conversation."""
    from apex.assistant import game_studio
    from apex.errors import PolicyDenied

    try:
        return DataEnvelope(status="real", data=game_studio.cancel(_factory(), conversation_id, project_id))
    except (LookupError, PermissionError, PolicyDenied, RuntimeError, ValueError) as exc:
        raise _game_studio_error(exc) from exc


# -- espace de travail (mode Code) ---------------------------------------------------------------------------


@router.get("/assistant/conversations/{conversation_id}/workspace", response_model=DataEnvelope[dict[str, Any]])
def workspace_files(conversation_id: str) -> DataEnvelope[dict[str, Any]]:
    from apex.assistant.workspace import conversation_workspace
    from apex.conversations.service import ConversationService

    conversation = ConversationService(_factory()).get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail=f"conversation « {conversation_id} » introuvable")
    workspace = conversation_workspace(conversation.id, conversation.project_id)
    files = [{"path": path, "size": size} for path, size in workspace.files()]
    # Dossier RÉEL sur ce PC (l'utilisateur y ouvre les modèles 3D avec Blender, Unity, Unreal).
    return DataEnvelope(status="real", data={"scope": "project" if conversation.project_id else "conversation",
                                             "root": str(workspace.root), "files": files})


_BASE64_LIMIT = 4_000_000


@router.get("/assistant/conversations/{conversation_id}/workspace/file", response_model=DataEnvelope[dict[str, Any]])
def workspace_file(conversation_id: str, path: str, encoding: str = "text") -> DataEnvelope[dict[str, Any]]:
    """Contenu d'un fichier : texte (tronqué à 400 000 octets pour l'affichage) ou, avec `encoding=base64`, octets
    exacts (aperçus PNG, modèles .glb à télécharger ; refusé au-delà de 4 Mo, jamais tronqué)."""
    from apex.assistant.workspace import conversation_workspace
    from apex.conversations.service import ConversationService
    from apex.tools.model import ToolFailure

    conversation = ConversationService(_factory()).get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail=f"conversation « {conversation_id} » introuvable")
    workspace = conversation_workspace(conversation.id, conversation.project_id)
    try:
        raw = workspace.read_bytes(path)
    except ToolFailure as exc:
        raise HTTPException(status_code=404 if exc.code.value == "not_found" else 422, detail=exc.message) from exc
    if encoding == "base64":
        if len(raw) > _BASE64_LIMIT:
            raise HTTPException(status_code=422, detail=f"fichier trop gros pour être transmis ({len(raw)} octets)")
        import base64

        return DataEnvelope(status="real", data={"path": path, "size": len(raw), "truncated": False,
                                                 "content_base64": base64.b64encode(raw).decode("ascii")})
    if encoding != "text":
        raise HTTPException(status_code=422, detail="encodage attendu : text ou base64")
    text = raw[:400_000].decode("utf-8", errors="replace")
    return DataEnvelope(status="real", data={"path": path, "size": len(raw), "truncated": len(raw) > 400_000,
                                             "content": text})

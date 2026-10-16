"""Délégation du Cerveau APEX à Game Studio (mode 3D) : les outils de Game Studio
(`apex.game_studio.delegation`, propriété de Codex) enregistrés côté assistant.

Deux parties :
- installation SANS Game Studio : rien n'est offert ni annoncé, et c'est dit ; un Game Studio cassé ne prive
  jamais l'assistant de ses autres outils ;
- installation AVEC Game Studio (ignorée sinon) : ses VRAIS outils, exécutés par le moteur d'outils avec
  les permissions du tour, sur un double de son service (le point d'injection prévu par Game Studio,
  `ToolContext.extra["game_studio_service"]`). Le Cerveau est un double (`_Brain3D`) : ces tests prouvent
  la logique de délégation, pas la reconnaissance des demandes par le vrai Cerveau (mesurée :
  docs/CERVEAU_APEX_V2.md §9 sexies).
"""

from __future__ import annotations

import copy
import json
import sys
import types
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

import apex.standalone as standalone
from apex.assistant import game_studio
from apex.assistant.creation import asks_to_create
from apex.assistant.tools import (
    GAME_STUDIO_ABSENT,
    GAME_STUDIO_TOOLS,
    assistant_registry,
    register_game_studio,
)
from apex.assistant.turns import TurnManager, game_studio_description
from apex.config.loader import parse_config
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.models.model import GenerationRequest, GenerationResult
from apex.tools.engine import ToolRegistry
from tests.test_assistant import _conversation, _messages, _no_memory, _ScriptedProvider, _Selection
from tests.test_conversations_service import BASE_CONFIG

SCENE = "Crée une scène de forêt avec une cabane et une rivière."
#: Ce que le Cerveau fait réellement d'une demande de scène (mesuré) : un appel 3D de son répertoire.
_SCENE_CALL = {"tool": "3d", "expression": 'vehicle("car", color="vert")', "result": "voiture 4,3 m"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Dossier APEX vierge et base SQLite réelle (comme `tests/test_assistant.py`)."""
    for key, value in standalone.environment(tmp_path / "home").items():
        monkeypatch.setenv(key, value)
    standalone.paths().ensure()
    engine = create_engine(f"sqlite:///{tmp_path / 'apex.db'}", future=True,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


class _Brain3D(_ScriptedProvider):
    """Comme le Cerveau APEX : sa réponse porte, dans ses mesures, ses appels 3D (ici, sans confiance
    mesurée : jamais de délégation à un autre moteur)."""

    def __init__(self, replies: list[str], calls: list[dict[str, str]]) -> None:
        super().__init__(replies)
        self.calls = calls

    def generate(self, request: GenerationRequest) -> GenerationResult:
        result = super().generate(request)
        return GenerationResult(text=result.text, model_used=result.model_used,
                                tokens_prompt=result.tokens_prompt,
                                tokens_completion=result.tokens_completion,
                                finish_reason="stop", elapsed_s=0.01,
                                facts={"backend": "apex_brain", "tool_calls": self.calls})


class _Studio:
    """Double du service de projets de Game Studio (mêmes méthodes que celles qu'appellent ses outils)."""

    def __init__(self, refuse: str | None = None) -> None:
        self.refuse = refuse
        self.created: list[tuple[dict, str]] = []
        self.cancelled: list[str] = []
        self.records: dict[str, dict] = {}

    def create_dynamic(self, request, references, *, owner_workspace_id):
        assert references == []
        if self.refuse:
            raise RuntimeError(self.refuse)
        project_id = str(uuid4())
        self.created.append((request, owner_workspace_id))
        self.records[project_id] = {"id": project_id, "mission_id": "mission-1", "status": "queued",
                                    "owner_workspace_id": owner_workspace_id, "steps": [], "events": [],
                                    "assembly": None}
        return dict(self.records[project_id])

    def get(self, project_id):
        if project_id not in self.records:
            raise LookupError(project_id)
        return dict(self.records[project_id])

    def cancel(self, project_id):
        self.cancelled.append(project_id)
        self.records[project_id]["status"] = "cancelled"
        return dict(self.records[project_id])


def _settings(env, **tools):
    from apex.assistant.settings import SettingsService

    _no_memory(env)
    if tools:
        SettingsService(env).update({"tools": tools})


def _turn(env, provider, question, *, studio=None, mode="3d", conversation=None):
    manager = TurnManager(env, provider_factory=lambda _candidate: provider,
                          config=parse_config(copy.deepcopy(BASE_CONFIG)),
                          sandbox_selection=lambda: _Selection(False), synchronous=True,
                          game_studio_service=studio)
    conversation = conversation or _conversation(env, mode=mode)
    _, turn = manager.submit(conversation.id, question)
    return turn, _messages(env, conversation.id)[-1], conversation


# -- sans Game Studio --------------------------------------------------------------------------------------


@pytest.fixture
def without_game_studio(monkeypatch):
    monkeypatch.setitem(sys.modules, "apex.game_studio.delegation", None)  # import impossible


def test_without_game_studio_nothing_is_offered_or_announced_and_it_is_said(env,
                                                                           without_game_studio) -> None:
    from apex.assistant.modes import MODES

    problems: list[str] = []
    names = {spec.name for spec in assistant_registry(problems=problems).specs()}
    assert problems == [GAME_STUDIO_ABSENT] and not names & set(GAME_STUDIO_TOOLS)
    assert "model3d.create" in names and "fs.read" in names
    assert not set(MODES["3d"].describe(names)["tools"]) & set(GAME_STUDIO_TOOLS)
    _settings(env, game_studio_missions=True)
    provider = _Brain3D(["Voiture prête."], [_SCENE_CALL])
    turn, answer, _ = _turn(env, provider, SCENE)
    assert turn.status == "done" and [s["tool"] for s in turn.steps] == ["model3d.create"]
    assert answer.meta["game_studio"] == {"trigger": "cerveau", "launched": False,
                                          "reason": GAME_STUDIO_ABSENT}
    assert any("non confiée à Game Studio" in n for n in answer.meta["tools_notes"])
    system = provider.requests[0].system
    assert "GAME STUDIO" not in system and "game_studio" not in system


def test_a_broken_game_studio_never_takes_the_other_tools_down(monkeypatch) -> None:
    broken = types.ModuleType("apex.game_studio.delegation")  # présent, mais sans ses outils
    monkeypatch.setitem(sys.modules, "apex.game_studio.delegation", broken)
    reason = register_game_studio(ToolRegistry())
    assert reason is not None and reason.startswith("Game Studio n'a pas pu être chargé")
    problems: list[str] = []
    names = {spec.name for spec in assistant_registry(problems=problems).specs()}
    assert problems == [reason] and {"fs.read", "model3d.create"} <= names


def test_following_a_mission_without_game_studio_is_refused_explicitly(env, without_game_studio) -> None:
    conversation = _conversation(env, mode="3d")
    with pytest.raises(game_studio.GameStudioUnavailable, match="ne fait pas partie"):
        game_studio.follow(env, conversation.id, str(uuid4()))
    with pytest.raises(PermissionError):
        game_studio.cancel(env, conversation.id, str(uuid4()), missions_allowed=False)


def test_missions_are_off_by_default_and_cpu_is_only_an_explicit_choice(env) -> None:
    from apex.assistant.settings import SettingsService

    settings, _ = SettingsService(env).load()
    assert settings.tools.game_studio_missions is False and settings.tools.game_studio_compute == "auto"
    with pytest.raises(ValueError, match="game_studio_compute"):
        SettingsService(env).update({"tools": {"game_studio_compute": "gpu-ou-cpu"}})


def test_creation_requests_are_told_apart_from_questions() -> None:
    for request in ("Crée une scène de forêt.", "Il me faut une moto noire.", "Peux-tu modéliser un char ?",
                    "Une hache de bûcheron, s\u2019il te plaît.", "Build me a sci-fi corridor.",
                    "Bonjour, fais-moi une jeep."):
        assert asks_to_create(request), request
    for question in ("Comment créer une voiture dans Blender ?",
                     "Explique-moi ce qu'est une carte de normales.", "Merci pour le modèle.",
                     "What is a rigged character?", "Combien de triangles pour un jeu ?",
                     "Une question : quel format choisir ?"):
        assert not asks_to_create(question), question


def test_the_description_is_the_users_own_request_within_the_tools_bounds() -> None:
    assert game_studio_description(f"  {SCENE}  ") == SCENE
    assert game_studio_description("un tank kaki") == "Demande de l'utilisateur : un tank kaki"
    assert len(game_studio_description("x" * 5000)) == 4000


# -- avec Game Studio ------------------------------------------------------------------------------------


@pytest.fixture
def studio_tools():
    pytest.importorskip("apex.game_studio.delegation", reason="Game Studio absent de cette installation")


def test_the_real_game_studio_tools_are_registered_and_offered_in_3d_mode_only(env,
                                                                             studio_tools) -> None:
    from apex.assistant.modes import GAME_STUDIO_PERMISSIONS, MODES
    from apex.tools.model import Permission

    registry = assistant_registry()
    assert {spec.name for spec in registry.specs()} >= set(GAME_STUDIO_TOOLS)
    assert {Permission.GAME_STUDIO_CREATE} == registry.get("game_studio.create")[0].permissions
    assert MODES["3d"].permissions >= GAME_STUDIO_PERMISSIONS
    assert not any(t.startswith("game_studio.") for m in MODES.values() if m.id != "3d" for t in m.tools)
    # Missions non permises (défaut) : suivre, oui ; lancer ou annuler, non.
    _settings(env)
    provider = _ScriptedProvider(["Bonjour."])
    _, answer, _ = _turn(env, provider, "Bonjour")
    offered = answer.meta["tools_offered"]
    assert "game_studio.status" in offered and "game_studio.results" in offered
    assert "game_studio.create" not in offered and "game_studio.cancel" not in offered
    assert "GAME STUDIO" not in provider.requests[0].system


def test_without_permission_the_brain_never_launches_a_mission_and_says_why(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env)
    turn, answer, _ = _turn(env, _Brain3D(["Voiture prête."], [_SCENE_CALL]), SCENE, studio=studio)
    assert studio.created == [] and [s["tool"] for s in turn.steps] == ["model3d.create"]
    assert answer.meta["game_studio"]["launched"] is False
    assert "pas permises" in answer.meta["game_studio"]["reason"]


def test_the_brain_hands_a_creation_request_to_game_studio_and_it_is_followed(env, studio_tools) -> None:
    from apex.assistant.workspace import conversation_workspace

    studio = _Studio()
    _settings(env, game_studio_missions=True)
    provider = _Brain3D(["Voiture prête."], [_SCENE_CALL])
    turn, answer, conversation = _turn(env, provider, SCENE, studio=studio)
    assert turn.status == "done" and answer.content == "Voiture prête."
    assert "GAME STUDIO" in provider.requests[0].system  # consigne pour le modèle…
    assert not any("GAME STUDIO" in n for n in answer.meta["tools_notes"])  # … jamais montrée comme note
    [(request, owner)] = studio.created
    assert request["description"] == SCENE and request["compute_backend"] == "auto"
    assert owner == conversation_workspace(conversation.id, None).id
    step = turn.steps[-1]
    assert step["tool"] == "game_studio.create" and step["source"] == "cerveau" and step["ok"]
    project_id = step["observation"]["output"]["project_id"]
    assert answer.meta["game_studio"] == {"trigger": "cerveau", "launched": True,
                                          "project_id": project_id, "mission_id": "mission-1",
                                          "status": "queued", "error": None}
    # Suivi par les outils de Game Studio, dans l'espace de travail de CETTE conversation seulement.
    followed = game_studio.follow(env, conversation.id, project_id, service=studio)
    assert followed["status"]["status"] == "queued" and followed["results"]["ready"] is False
    other = _conversation(env, mode="3d")
    with pytest.raises(LookupError):
        game_studio.follow(env, other.id, project_id, service=studio)
    with pytest.raises(PermissionError):
        game_studio.cancel(env, conversation.id, project_id, missions_allowed=False, service=studio)
    with pytest.raises(LookupError):
        game_studio.cancel(env, other.id, project_id, missions_allowed=True, service=studio)
    assert studio.cancelled == []
    cancelled = game_studio.cancel(env, conversation.id, project_id, missions_allowed=True, service=studio)
    assert cancelled == {"project_id": project_id, "status": "cancelled"}
    assert studio.cancelled == [project_id]


def test_a_question_never_launches_a_mission_even_if_the_brain_made_a_3d_call(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env, game_studio_missions=True)
    question = "Explique-moi ce qu'est une carte de normales."  # mesuré : le Cerveau y fait un appel 3D
    turn, answer, _ = _turn(env, _Brain3D(["…"], [_SCENE_CALL]), question, studio=studio)
    assert studio.created == [] and "game_studio" not in answer.meta
    assert "game_studio.create" not in [s["tool"] for s in turn.steps]


def test_compute_is_the_users_choice_never_the_models(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env, game_studio_missions=True, game_studio_compute="cpu")
    call = json.dumps({"tool": "game_studio.create",
                       "arguments": {"description": "Une arène circulaire avec des colonnes.",
                                     "compute_backend": "cuda"}})
    turn, _, _ = _turn(env, _ScriptedProvider([call, "Mission lancée."]), "Fais une arène.", studio=studio)
    assert [request["compute_backend"] for request, _ in studio.created] == ["cpu"]
    assert turn.steps[0]["arguments"]["compute_backend"] == "cpu"


def test_a_looping_model_never_launches_more_than_one_mission_per_turn(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env, game_studio_missions=True)
    call = json.dumps({"tool": "game_studio.create",
                       "arguments": {"description": "Une arène circulaire avec des colonnes."}})
    turn, _, _ = _turn(env, _ScriptedProvider([call, call, "Mission lancée."]), "Fais une arène.",
                       studio=studio)
    assert len(studio.created) == 1
    assert [s["ok"] for s in turn.steps] == [True, False]
    assert turn.steps[1]["observation"]["error"]["code"] == "limit"


def test_game_studio_refusals_are_shown_as_they_are(env, studio_tools) -> None:
    studio = _Studio(refuse="cuda_unavailable_choose_cpu_explicitly")
    _settings(env, game_studio_missions=True)
    turn, answer, _ = _turn(env, _Brain3D(["Voiture prête."], [_SCENE_CALL]), SCENE, studio=studio)
    assert turn.status == "done" and answer.content == "Voiture prête."
    step = turn.steps[-1]
    assert step["tool"] == "game_studio.create" and step["ok"] is False
    delegation = answer.meta["game_studio"]
    assert delegation["launched"] is False and delegation["project_id"] is None
    assert delegation["error"]["message"] == "cuda_unavailable_choose_cpu_explicitly"


def test_a_short_request_is_sent_as_the_users_request(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env, game_studio_missions=True)
    tank = {"tool": "3d", "expression": 'vehicle("tank", color="kaki")', "result": "char 7 m"}
    _turn(env, _Brain3D(["Char prêt."], [tank]), "Un tank kaki", studio=studio)
    descriptions = [request["description"] for request, _ in studio.created]
    assert descriptions == ["Demande de l'utilisateur : Un tank kaki"]

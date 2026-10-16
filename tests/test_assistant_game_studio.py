"""Le Cerveau APEX et Game Studio (mode 3D) : analyse d'intention, lancement automatique ou proposé, suivi,
aperçus, annulation, disponibilité expliquée. Les outils sont ceux de Game Studio
(`apex.game_studio.delegation`, propriété de Codex), enregistrés côté assistant.

Deux parties :
- installation SANS Game Studio : rien n'est offert ni annoncé, et c'est dit ; un Game Studio cassé ne prive
  jamais l'assistant de ses autres outils ;
- installation AVEC Game Studio (ignorée sinon) : ses VRAIS outils, exécutés par le moteur d'outils avec
  les permissions du tour, sur un double de son service (le point d'injection prévu par Game Studio,
  `ToolContext.extra["game_studio_service"]`). Le Cerveau est un double (`_Brain3D`) : ces tests prouvent
  la logique de délégation, pas la reconnaissance des demandes par le vrai Cerveau (mesurée :
  docs/CERVEAU_APEX_V2.md §9 septies).
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
from apex.assistant.creation import analyze, asks_to_create
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
    """Double des services de Game Studio : projets (méthodes qu'appellent ses outils et sa route
    d'artefacts) et missions graphiques (`artifact`)."""

    def __init__(self, refuse: BaseException | None = None) -> None:
        self.refuse = refuse
        self.created: list[tuple[dict, str]] = []
        self.cancelled: list[str] = []
        self.records: dict[str, dict] = {}
        self.blobs: dict[str, tuple[bytes, str]] = {}

    def create_dynamic(self, request, references, *, owner_workspace_id):
        assert references == []
        if self.refuse is not None:
            raise self.refuse
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

    def finish(self, project_id, artifacts):
        """La mission passe en revue avec ses artefacts d'assemblage (rendu, scène Blender…)."""
        self.records[project_id].update(status="review", delivery_status="delivered", assembly={
            "artifacts": [{"name": n, "hash": h, "role": r, "content_type": c}
                          for n, h, r, c, _ in artifacts]})
        for _name, digest, _role, content_type, data in artifacts:
            self.blobs[digest] = (data, content_type)

    def artifact(self, project_id, digest):
        if digest not in self.blobs:
            raise LookupError(digest)
        return self.blobs[digest]


def _settings(env, *, delegate: bool = False, **tools):
    """Réglages du tour. Délégation au moteur suivant coupée par défaut : ces tests isolent la décision
    Game Studio (la délégation sur un modèle 3D sans rapport a ses propres tests)."""
    from apex.assistant.settings import SettingsService

    _no_memory(env)
    SettingsService(env).update({"models": {"brain_delegate_below": 0.97 if delegate else 0.0}})
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


# -- analyse d'intention (règles, pas une mesure : la mesure est dans la documentation) ------------------


@pytest.mark.parametrize("message", [
    "Crée une voiture sportive.", "Dessine-moi une maison en 3D.", "J'aimerais une scène de forêt.",
    "Prépare un modèle 3D de fauteuil.", "Ajoute un bâtiment à mon projet.",  # exemples de l'utilisateur
    "Il me faut une moto noire.", "Peux-tu modéliser un char ?",
    "Une hache de bûcheron, s\u2019il te plaît.",
    "Build me a sci-fi corridor.", "I'd like a small red car.", "Merci ! Maintenant crée une moto rouge.",
])
def test_creation_requests_are_recognized(message) -> None:
    assert analyze(message).kind == "create", analyze(message)


@pytest.mark.parametrize("message", [
    "Comment créer une voiture dans Blender ?", "Explique-moi ce qu'est une carte de normales.",
    "Merci pour le modèle.", "What is a rigged character?", "Change la couleur du tank en bleu.",
    "Ne crée rien pour l'instant.", "Don't make a car yet.", "J'ai créé une voiture hier.",
    "Crée un fichier README.", "Prépare un résumé de la conversation.", "Crée un nouveau projet.",
    "Fais le point sur les rendus.", "Si je crée une voiture, comment l'exporter ?", "",
])
def test_questions_commands_and_refusals_never_launch_anything(message) -> None:
    assert analyze(message).kind == "none", analyze(message)


@pytest.mark.parametrize("message", [
    "Sais-tu créer des voitures ?", "Ajoute des roues à la voiture.", "Refais la maison en plus grand.",
    "Une voiture ?", "Peux-tu créer des arbres ?", "Plus tard, crée-moi un château.",
])
def test_ambiguous_requests_are_only_proposed(message) -> None:
    intent = analyze(message)
    assert intent.kind == "ambiguous" and intent.reason, intent
    assert not asks_to_create(message)


def test_the_description_is_the_users_own_request_within_the_tools_bounds() -> None:
    assert game_studio_description(f"  {SCENE}  ") == SCENE
    assert game_studio_description("un tank kaki") == "Demande de l'utilisateur : un tank kaki"
    assert len(game_studio_description("x" * 5000)) == 4000


def test_game_studio_refusals_are_explained_in_plain_words() -> None:
    assert "aucun GPU CUDA" in game_studio.explain("cuda_unavailable_choose_cpu_explicitly")
    assert "planificateur" in game_studio.explain("local_planner_model_missing")
    assert "bibliothèque" in game_studio.explain("ModuleNotFoundError: No module named 'jsonschema'")
    assert game_studio.explain("code_inconnu") == "code_inconnu"  # jamais une explication inventée


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
    assert turn.status == "done" and turn.steps == []  # une voiture pour une forêt : rien d'écrit
    delegation = answer.meta["game_studio"]
    assert delegation["state"] == "unavailable" and delegation["reason"] == GAME_STUDIO_ABSENT
    assert delegation["launched"] is False and delegation["intent"] == "create"
    assert any("Game Studio n'est pas disponible" in n for n in answer.meta["tools_notes"])
    system = provider.requests[0].system
    assert "GAME STUDIO" not in system and "game_studio" not in system


def test_a_question_leaves_no_game_studio_trace(env, without_game_studio) -> None:
    _settings(env, game_studio_missions=True)
    _, answer, _ = _turn(env, _Brain3D(["…"], [_SCENE_CALL]), "Comment exporter un .glb vers Unity ?")
    assert "game_studio" not in answer.meta


def test_a_broken_game_studio_never_takes_the_other_tools_down(monkeypatch) -> None:
    broken = types.ModuleType("apex.game_studio.delegation")  # présent, mais sans ses outils
    monkeypatch.setitem(sys.modules, "apex.game_studio.delegation", broken)
    reason = register_game_studio(ToolRegistry())
    assert reason is not None and reason.startswith("Game Studio n'a pas pu être chargé")
    problems: list[str] = []
    names = {spec.name for spec in assistant_registry(problems=problems).specs()}
    assert problems == [reason] and {"fs.read", "model3d.create"} <= names


def test_following_launching_or_cancelling_without_game_studio_is_refused(env, without_game_studio) -> None:
    from apex.assistant.settings import SettingsService

    conversation = _conversation(env, mode="3d")
    for call in (lambda: game_studio.follow(env, conversation.id, str(uuid4())),
                 lambda: game_studio.cancel(env, conversation.id, str(uuid4()))):
        with pytest.raises(game_studio.GameStudioUnavailable, match="ne fait pas partie"):
            call()
    with pytest.raises(LookupError, match="aucune mission proposée"):
        game_studio.launch_proposed(env, conversation.id, str(uuid4()), compute="auto")
    settings, _ = SettingsService(env).load()
    report = game_studio.availability(settings)
    assert report["installed"] is False and report["ready"] is False
    assert report["missing"] == [GAME_STUDIO_ABSENT]


def test_auto_launch_is_off_by_default_and_cpu_is_only_an_explicit_choice(env) -> None:
    from apex.assistant.settings import SettingsService

    settings, _ = SettingsService(env).load()
    assert settings.tools.game_studio_missions is False and settings.tools.game_studio_compute == "auto"
    with pytest.raises(ValueError, match="game_studio_compute"):
        SettingsService(env).update({"tools": {"game_studio_compute": "gpu-ou-cpu"}})


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
    # Lancement automatique non permis (défaut) : suivre, oui ; lancer ou annuler, non (le modèle).
    _settings(env)
    provider = _ScriptedProvider(["Bonjour."])
    _, answer, _ = _turn(env, provider, "Bonjour")
    offered = answer.meta["tools_offered"]
    assert "game_studio.status" in offered and "game_studio.results" in offered
    assert "game_studio.create" not in offered and "game_studio.cancel" not in offered
    assert "GAME STUDIO" not in provider.requests[0].system


def test_without_auto_launch_the_brain_proposes_and_the_user_launches_once(env, studio_tools) -> None:
    from apex.assistant.workspace import conversation_workspace

    studio = _Studio()
    _settings(env)
    provider = _Brain3D(["Voiture prête."], [_SCENE_CALL])
    turn, answer, conversation = _turn(env, provider, SCENE, studio=studio)
    assert studio.created == [] and turn.steps == []
    proposal = answer.meta["game_studio"]
    assert proposal["state"] == "proposed" and proposal["launched"] is False
    assert "lancement automatique" in proposal["reason"] and proposal["brain_3d_call"] is True
    # Le clic de l'utilisateur : la demande d'origine, le calcul des Paramètres, une seule mission.
    launched = game_studio.launch_proposed(env, conversation.id, answer.id, compute="cpu", service=studio)
    [(request, owner)] = studio.created
    assert request["description"] == SCENE and request["compute_backend"] == "cpu"
    assert owner == conversation_workspace(conversation.id, None).id
    record = launched["meta"]["game_studio"]
    assert record["state"] == "launched" and record["proposal_of"] == answer.id
    assert record["trigger"] == "utilisateur" and launched["meta"]["steps"][0]["source"] == "utilisateur"
    assert _messages(env, conversation.id)[-1].id == launched["message_id"]  # enregistré, jamais réécrit
    with pytest.raises(game_studio.AlreadyLaunched):
        game_studio.launch_proposed(env, conversation.id, answer.id, compute="auto", service=studio)
    with pytest.raises(LookupError):  # un message qui ne propose rien
        game_studio.launch_proposed(env, conversation.id, launched["message_id"], compute="auto",
                                    service=studio)
    assert len(studio.created) == 1


def test_a_proposal_cannot_be_launched_from_another_conversation(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env)
    _, answer, _ = _turn(env, _Brain3D(["…"], [_SCENE_CALL]), SCENE, studio=studio)
    other = _conversation(env, mode="3d")
    with pytest.raises(LookupError):
        game_studio.launch_proposed(env, other.id, answer.id, compute="auto", service=studio)
    assert studio.created == []


def test_with_auto_launch_the_brain_hands_a_clear_request_over_and_it_is_followed(env,
                                                                                studio_tools) -> None:
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
    delegation = answer.meta["game_studio"]
    assert delegation["state"] == "launched" and delegation["project_id"] == project_id
    assert delegation["authorization"] == "lancement automatique permis"
    # Suivi par les outils de Game Studio, dans l'espace de travail de CETTE conversation seulement.
    followed = game_studio.follow(env, conversation.id, project_id, service=studio)
    assert followed["status"]["status"] == "queued" and followed["results"]["ready"] is False
    other = _conversation(env, mode="3d")
    for call in (lambda: game_studio.follow(env, other.id, project_id, service=studio),
                 lambda: game_studio.cancel(env, other.id, project_id, service=studio)):
        with pytest.raises(LookupError):
            call()
    assert studio.cancelled == []
    cancelled = game_studio.cancel(env, conversation.id, project_id, service=studio)
    assert cancelled == {"project_id": project_id, "status": "cancelled"}
    assert studio.cancelled == [project_id]


def test_with_auto_launch_an_ambiguous_message_is_only_proposed(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env, game_studio_missions=True)
    _, answer, _ = _turn(env, _Brain3D(["…"], [_SCENE_CALL]), "Sais-tu modéliser des personnages ?",
                         studio=studio)
    assert studio.created == [] and answer.meta["game_studio"]["state"] == "proposed"
    assert answer.meta["game_studio"]["intent"] == "ambiguous"


def test_a_question_never_launches_or_proposes_even_if_the_brain_made_a_3d_call(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env, game_studio_missions=True)
    question = "Explique-moi ce qu'est une carte de normales."  # mesuré : le Cerveau y fait un appel 3D
    turn, answer, _ = _turn(env, _Brain3D(["…"], [_SCENE_CALL]), question, studio=studio)
    assert studio.created == [] and "game_studio" not in answer.meta
    assert "game_studio.create" not in [s["tool"] for s in turn.steps]


def test_a_clear_request_the_brain_did_not_recognize_is_still_handed_over(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env, game_studio_missions=True)
    _, answer, _ = _turn(env, _Brain3D(["Je ne sais pas."], []), "Dessine-moi une maison en 3D.",
                         studio=studio)
    assert len(studio.created) == 1 and answer.meta["game_studio"]["brain_3d_call"] is False


def test_the_launch_is_decided_by_the_analysis_whatever_engine_wrote_the_text(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env, game_studio_missions=True)
    _, answer, _ = _turn(env, _ScriptedProvider(["Voici une idée de scène."]), SCENE, studio=studio)
    assert len(studio.created) == 1 and answer.meta["game_studio"]["state"] == "launched"
    assert answer.meta["game_studio"]["served_by_brain"] is False


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
    turn, answer, _ = _turn(env, _ScriptedProvider([call, call, "Mission lancée."]), "Fais une arène.",
                            studio=studio)
    assert len(studio.created) == 1
    assert [s["ok"] for s in turn.steps] == [True, False]
    assert turn.steps[1]["observation"]["error"]["code"] == "limit"
    assert "game_studio" not in answer.meta  # la mission du modèle suffit : rien de plus n'est proposé


@pytest.mark.parametrize("failure", [RuntimeError("cuda_unavailable_choose_cpu_explicitly"),
                                     OSError("connexion à Game Studio perdue")])
def test_game_studio_refusals_and_failures_are_shown_as_they_are(env, studio_tools, failure) -> None:
    studio = _Studio(refuse=failure)
    _settings(env, game_studio_missions=True)
    turn, answer, _ = _turn(env, _Brain3D(["Voiture prête."], [_SCENE_CALL]), SCENE, studio=studio)
    assert turn.status == "done" and answer.content == "Voiture prête."  # la réponse reste servie
    step = turn.steps[-1]
    assert step["tool"] == "game_studio.create" and step["ok"] is False
    delegation = answer.meta["game_studio"]
    assert delegation["state"] == "refused" and delegation["project_id"] is None
    assert str(failure) in delegation["error"]["message"]


def test_a_refused_user_launch_is_recorded_with_its_explanation(env, studio_tools) -> None:
    studio = _Studio(refuse=RuntimeError("local_planner_model_missing"))
    _settings(env)
    _, answer, conversation = _turn(env, _Brain3D(["…"], [_SCENE_CALL]), SCENE, studio=studio)
    launched = game_studio.launch_proposed(env, conversation.id, answer.id, compute="auto", service=studio)
    assert launched["meta"]["game_studio"]["state"] == "refused"
    assert "planificateur" in launched["content"]


def test_previews_are_served_only_for_this_conversations_rendered_images(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env, game_studio_missions=True)
    turn, _, conversation = _turn(env, _Brain3D(["…"], [_SCENE_CALL]), SCENE, studio=studio)
    project_id = turn.steps[-1]["observation"]["output"]["project_id"]
    png = b"\x89PNG\r\n\x1a\nfake"
    studio.finish(project_id, [("scene.png", "h-view", "view", "image/png", png),
                               ("scene.blend", "h-blend", "blender_project", "application/x-blender",
                                b"B")])
    results = game_studio.follow(env, conversation.id, project_id, service=studio)["results"]
    assert results["ready"] is True and results["artifact_count"] == 2
    assert game_studio.preview(env, conversation.id, project_id, "h-view", service=studio,
                               graphics=studio) == (png, "image/png")
    for digest in ("h-blend", "inconnu"):  # pas une image, ou pas rendu par cette mission
        with pytest.raises(LookupError):
            game_studio.preview(env, conversation.id, project_id, digest, service=studio, graphics=studio)
    other = _conversation(env, mode="3d")
    with pytest.raises(LookupError):
        game_studio.preview(env, other.id, project_id, "h-view", service=studio, graphics=studio)


def test_a_follow_up_failure_is_unavailable_never_a_fake_state(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env, game_studio_missions=True)
    turn, _, conversation = _turn(env, _Brain3D(["…"], [_SCENE_CALL]), SCENE, studio=studio)
    project_id = turn.steps[-1]["observation"]["output"]["project_id"]

    def broken(_project_id):
        raise OSError("disque de Game Studio illisible")

    studio.get = broken
    with pytest.raises(game_studio.GameStudioUnavailable, match="illisible"):
        game_studio.follow(env, conversation.id, project_id, service=studio)


def test_availability_explains_what_is_missing_from_game_studios_own_probe(env, studio_tools) -> None:
    from apex.assistant.settings import SettingsService

    settings, _ = SettingsService(env).load()
    nothing = {"vehicle_blender": {"blender": None, "workers_present": True,
                                   "coder_manifest_present": False, "llama_cpp_present": False,
                                   "apex_native_coder_present": False,
                                   "python_dependencies": {"psutil": True, "numpy": True, "PIL": False}},
               "cuda": {"available": False, "reason": "No TripoSR CUDA qualification yet"}}
    report = game_studio.availability(settings, probe=lambda: nothing)
    assert report["installed"] is True and report["ready"] is False
    text = " ".join(report["missing"])
    assert "Blender" in text and "planificateur" in text and "PIL" in text and "GPU CUDA" in text
    ready = {"vehicle_blender": {"blender": "/usr/bin/blender", "workers_present": True,
                                 "coder_manifest_present": True, "llama_cpp_present": True,
                                 "python_dependencies": {"psutil": True, "numpy": True, "PIL": True}},
             "cuda": {"available": True}}
    report = game_studio.availability(settings, probe=lambda: ready)
    assert [m for m in report["missing"] if "jsonschema" not in m] == []

    def failing():
        raise RuntimeError("sonde cassée")

    report = game_studio.availability(settings, probe=failing)
    assert report["ready"] is False and "sonde cassée" in report["missing"][0]


# -- ébauches paramétriques du Cerveau ------------------------------------------------------------------


def test_an_unrelated_brain_blockout_is_never_written_and_is_handed_over(env, without_game_studio) -> None:
    """Mesuré : pour « une chaise », le Cerveau construit un personnage. Rien n'est écrit, c'est dit, et en
    mode Automatique la réponse va au moteur suivant."""
    chair = {"tool": "3d", "expression": 'character("humanoid", build="strong")',
             "result": "personnage 1,8 m"}
    _settings(env, delegate=True)
    provider = _Brain3D(["personnage 1,8 m — 16 pièces", "Voici une chaise en bois."], [chair])
    turn, answer, _ = _turn(env, provider, "Crée une chaise en bois.")
    assert turn.steps == [] and any("sans rapport avec la demande (personnage)" in n
                                     for n in answer.meta["tools_notes"])
    assert answer.meta["delegation"]["reason"] == "model3d_mismatch"
    assert answer.content == "Voici une chaise en bois."


def test_a_blockout_built_for_a_question_is_never_written(env, without_game_studio) -> None:
    _settings(env)
    turn, answer, _ = _turn(env, _Brain3D(["voiture 4,3 m"], [_SCENE_CALL]),
                            "Explique-moi ce qu'est une carte de normales.")
    notes = answer.meta["tools_notes"]
    assert turn.steps == [] and any("Message sans demande de création" in n for n in notes)


def test_the_requested_blockout_is_still_written(env, without_game_studio) -> None:
    from apex.assistant.workspace import conversation_workspace

    tank = {"tool": "3d", "expression": 'vehicle("tank", color="sable")', "result": "char 7 m"}
    _settings(env, delegate=True)
    turn, answer, conversation = _turn(env, _Brain3D(["char 7 m"], [tank]),
                                       "Peux-tu modéliser un char sable ?")
    assert [s["tool"] for s in turn.steps] == ["model3d.create"] and turn.steps[0]["source"] == "cerveau"
    assert "delegation" not in answer.meta
    assert "modeles3d/char.glb" in {p for p, _ in conversation_workspace(conversation.id, None).files()}

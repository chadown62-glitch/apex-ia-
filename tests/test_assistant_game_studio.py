"""Le Cerveau APEX et Game Studio (mode 3D) : analyse d'intention, mission PROPOSÉE puis lancée par un clic
de l'utilisateur (jamais par un modèle : règle HUMAN-only de `PolicyEngine`), suivi, aperçus, annulation,
disponibilité expliquée. Les outils sont ceux de Game Studio
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

    def __init__(self, refuse: BaseException | None = None, *,
                 refuse_resume: BaseException | None = None) -> None:
        self.refuse, self.refuse_resume = refuse, refuse_resume
        self.created: list[tuple[dict, str]] = []
        self.cancelled: list[str] = []
        self.resumed: list[str] = []
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

    def resume(self, project_id):
        """Comme `ProjectService.resume` : nouvelle mission pour le même projet, ou refus (ValueError)."""
        if self.refuse_resume is not None:
            raise self.refuse_resume
        self.resumed.append(project_id)
        self.records[project_id].update(status="running", mission_id=f"mission-{len(self.resumed) + 1}")
        return dict(self.records[project_id])

    def progress(self, project_id, status, steps):
        """La mission avance : son état et ses étapes, telles que Game Studio les enregistre."""
        self.records[project_id].update(status=status, steps=steps)

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
    _settings(env)
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
    _settings(env)
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


def test_autonomous_launch_cannot_be_turned_on_and_cpu_is_only_an_explicit_choice(env) -> None:
    """HUMAN-only : l'ancien réglage de lancement automatique est relu, mais toujours sans effet."""
    from apex.assistant.settings import SettingsService

    settings, _ = SettingsService(env).load()
    assert settings.tools.game_studio_missions is False and settings.tools.game_studio_compute == "auto"
    SettingsService(env).update({"tools": {"game_studio_missions": True}})
    settings, _ = SettingsService(env).load()
    assert settings.tools.game_studio_missions is False
    with pytest.raises(ValueError, match="game_studio_compute"):
        SettingsService(env).update({"tools": {"game_studio_compute": "gpu-ou-cpu"}})


def test_no_mode_ever_grants_a_model_the_right_to_launch_or_cancel_a_mission() -> None:
    from apex.assistant.modes import GAME_STUDIO_HUMAN_ONLY, MODES

    for mode in MODES.values():
        assert not mode.permissions & GAME_STUDIO_HUMAN_ONLY, mode.id
        assert not {"game_studio.create", "game_studio.cancel"} & set(mode.tools), mode.id


# -- avec Game Studio ------------------------------------------------------------------------------------


@pytest.fixture
def studio_tools():
    pytest.importorskip("apex.game_studio.delegation", reason="Game Studio absent de cette installation")


def _proposed_then_launched(env, studio, *, compute="auto"):
    """Le parcours prévu : le Cerveau propose, l'utilisateur lance d'un clic."""
    _, answer, conversation = _turn(env, _Brain3D(["…"], [_SCENE_CALL]), SCENE, studio=studio)
    assert answer.meta["game_studio"]["state"] == "proposed" and studio.created == []
    launched = game_studio.launch_proposed(env, conversation.id, answer.id, compute=compute, service=studio)
    return launched, answer, conversation


def test_the_real_game_studio_tools_are_registered_and_only_following_is_offered(env, studio_tools) -> None:
    from apex.assistant.modes import GAME_STUDIO_HUMAN_ONLY, GAME_STUDIO_READ, MODES
    from apex.tools.model import Permission

    registry = assistant_registry()
    assert {spec.name for spec in registry.specs()} >= set(GAME_STUDIO_TOOLS)
    assert {Permission.GAME_STUDIO_CREATE} == registry.get("game_studio.create")[0].permissions
    assert MODES["3d"].permissions >= GAME_STUDIO_READ
    assert not MODES["3d"].permissions & GAME_STUDIO_HUMAN_ONLY
    assert not any(t.startswith("game_studio.") for m in MODES.values() if m.id != "3d" for t in m.tools)
    _settings(env, game_studio_missions=True)  # l'ancien réglage : sans effet
    provider = _ScriptedProvider(["Bonjour."])
    _, answer, _ = _turn(env, provider, "Bonjour")
    offered = answer.meta["tools_offered"]
    assert "game_studio.status" in offered and "game_studio.results" in offered
    assert "game_studio.create" not in offered and "game_studio.cancel" not in offered
    system = provider.requests[0].system
    assert "GAME STUDIO" in system and "Tu ne lances ni n'annules jamais une mission" in system
    assert not any("GAME STUDIO" in n for n in answer.meta.get("tools_notes", []))  # consigne, pas une note


def test_the_brain_only_proposes_and_the_user_launches_once(env, studio_tools) -> None:
    from apex.assistant.workspace import conversation_workspace

    studio = _Studio()
    _settings(env, game_studio_missions=True)
    provider = _Brain3D(["Voiture prête."], [_SCENE_CALL])
    turn, answer, conversation = _turn(env, provider, SCENE, studio=studio)
    assert studio.created == [] and turn.steps == []
    proposal = answer.meta["game_studio"]
    assert proposal["state"] == "proposed" and proposal["launched"] is False
    assert "jamais lancée sans votre clic" in proposal["reason"] and proposal["brain_3d_call"] is True
    assert "authorization" not in proposal
    # Le clic de l'utilisateur : la demande d'origine, le calcul des Paramètres, une seule mission.
    launched = game_studio.launch_proposed(env, conversation.id, answer.id, compute="cpu", service=studio)
    [(request, owner)] = studio.created
    assert request["description"] == SCENE and request["compute_backend"] == "cpu"
    assert owner == conversation_workspace(conversation.id, None).id
    record = launched["meta"]["game_studio"]
    assert record["state"] == "launched" and record["proposal_of"] == answer.id
    assert record["authorization"] == "confirmée par l'utilisateur"
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


def test_a_launched_mission_is_followed_and_cancelled_only_in_its_conversation(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env)
    launched, _, conversation = _proposed_then_launched(env, studio)
    project_id = launched["meta"]["game_studio"]["project_id"]
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


def test_launching_and_cancelling_are_refused_to_anyone_but_a_human(env, studio_tools, monkeypatch) -> None:
    """Le moteur de politique est consulté à chaque geste : un sujet autre qu'humain n'obtient rien."""
    from apex.policy.engine import PolicyDenied, PolicyEngine
    from apex.policy.model import Action, Subject

    for subject in (Subject.CORE, Subject.RESEARCH):
        assert not PolicyEngine().check(subject, Action.CREATE_MISSION).allowed, subject
    assert not PolicyEngine().check(Subject.RESEARCH, Action.CANCEL_MISSION).allowed
    assert PolicyEngine().check(Subject.HUMAN, Action.CREATE_MISSION).allowed
    studio = _Studio()
    _settings(env)
    _, answer, conversation = _turn(env, _Brain3D(["…"], [_SCENE_CALL]), SCENE, studio=studio)
    real = PolicyEngine.authorize

    def as_core(self, _subject, action):  # le même geste, demandé par le CORE au lieu d'un humain
        return real(self, Subject.CORE, action)

    monkeypatch.setattr(PolicyEngine, "authorize", as_core)
    with pytest.raises(PolicyDenied):
        game_studio.launch_proposed(env, conversation.id, answer.id, compute="auto", service=studio)
    assert studio.created == []


def test_an_ambiguous_message_is_only_proposed_as_to_confirm(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env, game_studio_missions=True)
    _, answer, _ = _turn(env, _Brain3D(["…"], [_SCENE_CALL]), "Sais-tu modéliser des personnages ?",
                         studio=studio)
    assert studio.created == [] and answer.meta["game_studio"]["state"] == "proposed"
    assert answer.meta["game_studio"]["intent"] == "ambiguous"
    assert answer.meta["game_studio"]["reason"].startswith("demande à confirmer")


def test_a_question_never_launches_or_proposes_even_if_the_brain_made_a_3d_call(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env)
    question = "Explique-moi ce qu'est une carte de normales."  # mesuré : le Cerveau y fait un appel 3D
    turn, answer, _ = _turn(env, _Brain3D(["…"], [_SCENE_CALL]), question, studio=studio)
    assert studio.created == [] and "game_studio" not in answer.meta
    assert "game_studio.create" not in [s["tool"] for s in turn.steps]


def test_a_clear_request_the_brain_did_not_recognize_is_still_proposed(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env)
    _, answer, _ = _turn(env, _Brain3D(["Je ne sais pas."], []), "Dessine-moi une maison en 3D.",
                         studio=studio)
    assert studio.created == [] and answer.meta["game_studio"]["state"] == "proposed"
    assert answer.meta["game_studio"]["brain_3d_call"] is False


def test_the_proposal_is_decided_by_the_analysis_whatever_engine_wrote_the_text(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env)
    _, answer, _ = _turn(env, _ScriptedProvider(["Voici une idée de scène."]), SCENE, studio=studio)
    assert studio.created == [] and answer.meta["game_studio"]["state"] == "proposed"
    assert answer.meta["game_studio"]["served_by_brain"] is False


@pytest.mark.parametrize("tool", ["game_studio.create", "game_studio.cancel"])
def test_a_model_calling_launch_or_cancel_is_refused_and_nothing_happens(env, studio_tools, tool) -> None:
    studio = _Studio()
    _settings(env, game_studio_missions=True, game_studio_compute="cpu")
    arguments = ({"description": "Une arène circulaire avec des colonnes.", "compute_backend": "cuda"}
                 if tool == "game_studio.create" else {"project_id": str(uuid4())})
    call = json.dumps({"tool": tool, "arguments": arguments})
    turn, answer, _ = _turn(env, _ScriptedProvider([call, call, "Mission lancée."]), "Fais une arène.",
                            studio=studio)
    assert studio.created == [] and studio.cancelled == []
    assert turn.steps and all(s["tool"] == tool and s["ok"] is False for s in turn.steps)
    assert {s["observation"]["error"]["code"] for s in turn.steps} == {"not_offered"}
    assert "game_studio" not in answer.meta or answer.meta["game_studio"]["state"] == "proposed"


@pytest.mark.parametrize("failure", [RuntimeError("cuda_unavailable_choose_cpu_explicitly"),
                                     OSError("connexion à Game Studio perdue")])
def test_game_studio_refusals_and_failures_are_shown_as_they_are(env, studio_tools, failure) -> None:
    studio = _Studio(refuse=failure)
    _settings(env)
    launched, answer, conversation = _proposed_then_launched(env, studio)
    assert answer.content == "…"  # la réponse du tour reste servie
    step = launched["meta"]["steps"][0]
    assert step["tool"] == "game_studio.create" and step["ok"] is False
    record = launched["meta"]["game_studio"]
    assert record["state"] == "refused" and record["project_id"] is None
    assert str(failure) in record["error"]["message"]
    assert _messages(env, conversation.id)[-1].id == launched["message_id"]


@pytest.mark.parametrize(("failure", "said"), [
    (RuntimeError("local_planner_model_missing"), "planificateur"),               # modèle absent
    (ModuleNotFoundError("No module named 'jsonschema'"), "bibliothèque"),       # dépendance absente
    (RuntimeError("cuda_unavailable_choose_cpu_explicitly"), "aucun GPU CUDA"),  # GPU indisponible
    (RuntimeError("cuda_game_studio_requires_qualified_brain_quiescence_handoff"),  # GPU non qualifié
     "que le calcul sur processeur"),
])
def test_a_refused_user_launch_is_recorded_with_its_explanation(env, studio_tools, failure, said) -> None:
    studio = _Studio(refuse=failure)
    _settings(env)
    launched, _, _ = _proposed_then_launched(env, studio)
    assert launched["meta"]["game_studio"]["state"] == "refused" and studio.created == []
    assert said in launched["content"]


def test_an_accepted_mission_is_followed_from_its_steps_to_its_files(env, studio_tools) -> None:
    """Parcours complet SUR UN DOUBLE (aucune génération Blender ici) : file d'attente, étapes, puis
    fichiers rendus à cette conversation ; le téléchargement passe par la page Game Studio (`api_path`)."""
    studio = _Studio()
    _settings(env)
    launched, _, conversation = _proposed_then_launched(env, studio)
    project_id = launched["meta"]["game_studio"]["project_id"]
    studio.progress(project_id, "running", [
        {"key": "vehicule", "kind": "vehicle", "status": "review", "job_id": None},
        {"key": "garage", "kind": "building", "status": "running", "job_id": None},
    ])
    status = game_studio.follow(env, conversation.id, project_id, service=studio)["status"]
    assert status["status"] == "running"
    steps = [(s["key"], s["status"]) for s in status["steps"]]
    assert steps == [("vehicule", "review"), ("garage", "running")]
    studio.finish(project_id, [("scene.png", "h-view", "view", "image/png", b"\x89PNG"),
                               ("scene.blend", "h-blend", "blender_project", "application/x-blender", b"B"),
                               ("scene.glb", "h-glb", "exchange", "model/gltf-binary", b"glTF")])
    results = game_studio.follow(env, conversation.id, project_id, service=studio)["results"]
    assert results["ready"] is True and results["artifact_count"] == 3
    assert {a["role"] for a in results["artifacts"]} == {"view", "blender_project", "exchange"}
    assert results["api_path"] == f"/api/v1/game-studio/projects/{project_id}"


def test_a_step_blocked_by_a_missing_dependency_says_which_and_why(env, studio_tools) -> None:
    """Blender introuvable PENDANT la mission (le planificateur était là) : la mission est acceptée, puis
    l'étape qui en a besoin est bloquée, et c'est dit."""
    studio = _Studio()
    _settings(env)
    launched, _, conversation = _proposed_then_launched(env, studio)
    project_id = launched["meta"]["game_studio"]["project_id"]
    studio.progress(project_id, "failed", [
        {"key": "vehicule", "kind": "vehicle", "status": "blocked_dependency", "job_id": None,
         "reason": "blender_worker_or_local_coder_missing"}])
    results = game_studio.follow(env, conversation.id, project_id, service=studio)["results"]
    assert results["ready"] is False
    assert results["blocked"] == [{"key": "vehicule", "reason": "blender_worker_or_local_coder_missing"}]
    assert game_studio.explain("blender_worker_or_local_coder_missing").startswith("Blender")


def test_an_interrupted_mission_is_resumed_only_by_the_user_in_its_conversation(env, studio_tools,
                                                                               monkeypatch) -> None:
    from apex.policy.engine import PolicyDenied, PolicyEngine
    from apex.policy.model import Subject

    studio = _Studio()
    _settings(env)
    launched, _, conversation = _proposed_then_launched(env, studio)
    project_id = launched["meta"]["game_studio"]["project_id"]
    studio.progress(project_id, "interrupted", [])  # APEX arrêté pendant la mission
    assert "interrupted" in game_studio.RESUMABLE
    other = _conversation(env, mode="3d")
    with pytest.raises(LookupError):
        game_studio.resume(env, other.id, project_id, service=studio)
    real = PolicyEngine.authorize
    def as_core(self, _subject, action):
        return real(self, Subject.CORE, action)

    monkeypatch.setattr(PolicyEngine, "authorize", as_core)
    with pytest.raises(PolicyDenied):
        game_studio.resume(env, conversation.id, project_id, service=studio)
    monkeypatch.setattr(PolicyEngine, "authorize", real)
    assert studio.resumed == []
    resumed = game_studio.resume(env, conversation.id, project_id, service=studio)
    assert studio.resumed == [project_id]
    record = resumed["meta"]["game_studio"]
    assert record == {"trigger": "utilisateur", "state": "resumed", "project_id": project_id,
                      "previous_status": "interrupted", "status": "running",
                      "authorization": "confirmée par l'utilisateur"}
    assert _messages(env, conversation.id)[-1].id == resumed["message_id"]  # enregistrée, jamais réécrite


@pytest.mark.parametrize(("code", "said"), [
    ("project_already_delivered_for_review", "déjà livrée"),
    ("cumulative_time_budget_exhausted", "temps total"),
    ("only_interrupted_failed_or_incomplete_projects_can_resume", "seule une mission interrompue"),
])
def test_a_resume_refused_by_game_studio_is_explained(env, studio_tools, code, said) -> None:
    studio = _Studio(refuse_resume=ValueError(code))
    _settings(env)
    launched, _, conversation = _proposed_then_launched(env, studio)
    project_id = launched["meta"]["game_studio"]["project_id"]
    with pytest.raises(ValueError, match=said):
        game_studio.resume(env, conversation.id, project_id, service=studio)


def test_previews_are_served_only_for_this_conversations_rendered_images(env, studio_tools) -> None:
    studio = _Studio()
    _settings(env)
    launched, _, conversation = _proposed_then_launched(env, studio)
    project_id = launched["meta"]["game_studio"]["project_id"]
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
    _settings(env)
    launched, _, conversation = _proposed_then_launched(env, studio)
    project_id = launched["meta"]["game_studio"]["project_id"]

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

    # Garde GPU de Game Studio (17/10/2026) : CUDA présent mais missions sur GPU non qualifiées.
    guarded = {**ready, "cuda": {"available": True, "dispatch_qualified": False}}
    text = " ".join(game_studio.availability(settings, probe=lambda: guarded)["missing"])
    assert "n'accepte encore que le calcul sur processeur" in text
    SettingsService(env).update({"tools": {"game_studio_compute": "cpu"}})
    cpu_settings, _ = SettingsService(env).load()
    cpu_missing = game_studio.availability(cpu_settings, probe=lambda: guarded)["missing"]
    assert not any("processeur" in m for m in cpu_missing)

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

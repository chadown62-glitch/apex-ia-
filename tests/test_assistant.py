"""Assistant APEX 0.4.0 — projets, conversations, modes, paramètres, pièces jointes, tours asynchrones.

Tout passe par les vrais composants (SQLite réelle, moteur d'outils, espaces de travail, routage, génération avec
repli). Seule la GÉNÉRATION du modèle est un double de test (`_ScriptedProvider`, contrat `ModelProvider`
respecté) : ces tests prouvent la logique d'APEX autour du modèle, jamais la qualité d'un modèle réel.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import struct
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine

import apex.standalone as standalone
from apex.assistant import tools as assistant_tools
from apex.assistant.turns import TurnBusy, TurnManager, parse_tool_call
from apex.config.loader import parse_config
from apex.conversations.model import MessageStatus
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement
from apex.policy.model import Subject
from tests.test_conversations_service import BASE_CONFIG


class _ScriptedProvider(ModelProvider):
    """Rend les réponses prévues, dans l'ordre ; enregistre chaque requête reçue (prompt, consignes)."""

    def __init__(self, replies: list[str], *, block: threading.Event | None = None) -> None:
        self.replies = list(replies)
        self.requests: list[GenerationRequest] = []
        self.block = block

    def detect(self) -> ModelCapabilityReport:
        return ModelCapabilityReport(status=CapabilityStatus.AVAILABLE, backend="test", detail="double de test")

    def generate(self, request: GenerationRequest) -> GenerationResult:
        from apex.errors import ModelProviderError

        self.requests.append(request)
        if self.block is not None:
            while not (request.cancel_event and request.cancel_event.is_set()):
                time.sleep(0.02)
            raise ModelProviderError("test", endpoint_url="test", detail="génération annulée")
        text = self.replies.pop(0) if self.replies else "fin"
        return GenerationResult(text=text, model_used=request.endpoint.served_model,
                                tokens_prompt=Measurement.real(10), tokens_completion=Measurement.real(5),
                                finish_reason="stop", elapsed_s=0.01, facts={"backend": "test"})


class _Selection:
    def __init__(self, available: bool) -> None:
        self.provider = None
        self.report = type("R", (), {"is_available": available, "detail": "sandbox de test indisponible"})()


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key, value in standalone.environment(tmp_path / "home").items():
        monkeypatch.setenv(key, value)
    standalone.paths().ensure()
    engine = create_engine(f"sqlite:///{tmp_path / 'apex.db'}", future=True,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def _manager(factory, replies, *, synchronous=True, block=None, sandbox=False):
    provider = _ScriptedProvider(replies, block=block)
    manager = TurnManager(factory, provider_factory=lambda _candidate: provider,
                          config=parse_config(copy.deepcopy(BASE_CONFIG)),
                          sandbox_selection=lambda: _Selection(sandbox), synchronous=synchronous)
    return manager, provider


def _no_memory(factory):
    from apex.assistant.settings import SettingsService

    SettingsService(factory).update({"behavior": {"use_memory": False}})


def _conversation(factory, *, mode="normal", project_id=None):
    from apex.conversations.service import ConversationService

    return ConversationService(factory).create(title="Essai", created_by=Subject.HUMAN, project_id=project_id,
                                               mode=mode)


def _messages(factory, conversation_id):
    from apex.conversations.service import ConversationService

    return ConversationService(factory).list_messages(conversation_id)


# -- projets ---------------------------------------------------------------------------------------------------


def test_projects_are_created_updated_and_deleted_with_everything_they_own(env) -> None:
    from apex.assistant.attachments import AttachmentService
    from apex.assistant.workspace import project_workspace
    from apex.projects.service import ProjectService

    projects = ProjectService(env)
    project = projects.create(name="Budget 2027", description="", instructions="Toujours en euros.",
                              created_by=Subject.HUMAN)
    assert projects.get(project.id).instructions == "Toujours en euros."
    renamed = projects.update(project.id, requested_by=Subject.HUMAN, name="Budget familial")
    assert renamed.name == "Budget familial" and renamed.instructions == "Toujours en euros."
    with pytest.raises(ValueError):
        projects.update(project.id, requested_by=Subject.HUMAN, name="  ")
    conversation = _conversation(env, project_id=project.id)
    _no_memory(env)
    manager, _ = _manager(env, ["Réponse."])
    manager.submit(conversation.id, "Bonjour")
    AttachmentService(env).add(project.id, "notes.md", b"# Notes\n")
    workspace_root = project_workspace(project.id).root
    assert (workspace_root / "pieces-jointes" / "notes.md").is_file()

    counts = projects.delete(project.id, requested_by=Subject.HUMAN)
    assert counts["conversations"] == 1 and counts["messages"] == 2 and counts["attachments"] == 1
    assert counts["workspace_removed"] == 1 and not workspace_root.exists()
    assert projects.get(project.id) is None and _messages(env, conversation.id) == []
    with pytest.raises(LookupError):
        projects.delete(project.id, requested_by=Subject.HUMAN)


def test_a_clone_can_neither_modify_nor_delete_a_project(env) -> None:
    from apex.errors import PolicyDenied
    from apex.projects.service import ProjectService

    project = ProjectService(env).create(name="P", description="", created_by=Subject.HUMAN)
    with pytest.raises(PolicyDenied):
        ProjectService(env).update(project.id, requested_by=Subject.RESEARCH, name="X")
    with pytest.raises(PolicyDenied):
        ProjectService(env).delete(project.id, requested_by=Subject.CORE)


# -- conversations et modes -----------------------------------------------------------------------------------


def test_conversations_have_a_mode_and_are_listed_by_project_or_standalone(env) -> None:
    from apex.conversations.service import ConversationService
    from apex.projects.service import ProjectService

    project = ProjectService(env).create(name="P", description="", created_by=Subject.HUMAN)
    inside = _conversation(env, mode="finance", project_id=project.id)
    outside = _conversation(env)
    service = ConversationService(env)
    assert [c.id for c in service.list_filtered(project_id=project.id)] == [inside.id]
    assert [c.id for c in service.list_filtered(standalone=True)] == [outside.id]
    assert service.get(inside.id).mode == "finance" and service.get(outside.id).mode == "normal"
    assert service.set_mode(outside.id, mode="code", requested_by=Subject.HUMAN).mode == "code"
    with pytest.raises(ValueError):
        service.set_mode(outside.id, mode="astrologie", requested_by=Subject.HUMAN)
    with pytest.raises(LookupError):
        _conversation(env, project_id="00000000-0000-0000-0000-000000000000")


def test_modes_change_instructions_tools_and_view() -> None:
    from apex.assistant.modes import MODES

    assert MODES["normal"].tools == () and MODES["normal"].view == "chat"
    assert "fs.write" in MODES["code"].tools and MODES["code"].view == "code"
    assert MODES["finance"].tools == ("calc.evaluate", "finance.projection", "finance.loan")
    assert MODES["design"].tools == ("color.contrast",) and "images" in " ".join(MODES["design"].limits)
    assert len({m.instructions for m in MODES.values()}) == 4


# -- tours ------------------------------------------------------------------------------------------------------


def test_a_turn_sends_mode_and_project_instructions_history_and_stores_a_real_answer(env) -> None:
    from apex.projects.service import ProjectService

    _no_memory(env)
    project = ProjectService(env).create(name="Roman", description="", instructions="Style sobre, phrases courtes.",
                                         created_by=Subject.HUMAN)
    conversation = _conversation(env, project_id=project.id)
    manager, provider = _manager(env, ["Première réponse.", "Seconde réponse."])
    manager.submit(conversation.id, "Premier message")
    _user, turn = manager.submit(conversation.id, "Second message")
    assert turn.status == "done"
    request = provider.requests[-1]
    assert "Style sobre, phrases courtes." in request.system and "Roman" in request.system
    assert "Premier message" in request.prompt and "Première réponse." in request.prompt
    assert request.timeout_s > 60  # délai des paramètres (CPU), jamais 60 s
    messages = _messages(env, conversation.id)
    assert [m.role.value for m in messages] == ["user", "assistant", "user", "assistant"]
    answer = messages[-1]
    assert answer.status is MessageStatus.REAL and answer.content == "Seconde réponse."
    assert answer.meta["mode"] == "normal" and answer.meta["tokens_completion"] == 5


def test_finance_mode_runs_real_tools_and_records_each_step(env) -> None:
    _no_memory(env)
    conversation = _conversation(env, mode="finance")
    call = json.dumps({"tool": "finance.loan", "arguments": {"principal": 200000, "annual_rate_percent": 3.5,
                                                             "years": 20}})
    manager, provider = _manager(env, [call, "La mensualité est de 1 159,92 €."])
    _, turn = manager.submit(conversation.id, "Combien coûte un prêt de 200 000 € sur 20 ans à 3,5 % ?")
    assert turn.status == "done"
    step = turn.steps[0]
    assert step["tool"] == "finance.loan" and step["ok"] is True
    assert step["observation"]["output"]["monthly_payment"] == 1159.92  # calculé par APEX, pas par le modèle
    assert "finance.loan" in provider.requests[0].system  # outil offert et expliqué
    assert "1159.92" in provider.requests[1].prompt  # résultat réel rendu au modèle
    answer = _messages(env, conversation.id)[-1]
    assert answer.status is MessageStatus.REAL and answer.meta["steps"][0]["tool"] == "finance.loan"


def test_code_mode_writes_in_the_workspace_but_never_offers_execution_without_a_verified_sandbox(env) -> None:
    from apex.assistant.workspace import conversation_workspace

    _no_memory(env)
    conversation = _conversation(env, mode="code")
    write = json.dumps({"tool": "fs.write", "arguments": {"path": "hello.py", "content": "print('ok')\n"}})
    run = json.dumps({"tool": "code.run", "arguments": {"path": "hello.py"}})
    manager, provider = _manager(env, [write, run, "hello.py est écrit ; il n'a pas été exécuté."], sandbox=False)
    _, turn = manager.submit(conversation.id, "Écris hello.py")
    workspace = conversation_workspace(conversation.id, None)
    assert workspace.read_bytes("hello.py") == b"print('ok')\n"
    system = provider.requests[0].system
    assert "Exécution de code indisponible" in system and "code.run :" not in system
    refused = turn.steps[1]
    assert refused["tool"] == "code.run" and refused["ok"] is False  # demandé, refusé, tracé
    assert turn.status == "done"


def test_turn_errors_are_explicit_never_a_fake_answer(env) -> None:
    from apex.assistant.settings import SettingsService

    _no_memory(env)
    SettingsService(env).update({"models": {"preferred_model": "modele-inexistant"}})
    conversation = _conversation(env)
    manager, _ = _manager(env, ["ne doit pas être utilisé"])
    _, turn = manager.submit(conversation.id, "Bonjour")
    assert turn.status == "failed" and "aucun modèle disponible" in turn.error
    answer = _messages(env, conversation.id)[-1]
    assert answer.status is MessageStatus.ERROR and answer.content == "" and "modele-inexistant" in answer.error


def test_a_model_that_only_calls_tools_ends_in_an_explicit_error(env) -> None:
    from apex.assistant.settings import SettingsService

    _no_memory(env)
    SettingsService(env).update({"tools": {"max_tool_steps": 2}})
    conversation = _conversation(env, mode="finance")
    call = json.dumps({"tool": "calc.evaluate", "arguments": {"expression": "1+1"}})
    manager, provider = _manager(env, [call, call, call])
    _, turn = manager.submit(conversation.id, "1+1 ?")
    assert len(turn.steps) == 2
    assert "Limite d'outils atteinte" in provider.requests[-1].prompt
    assert turn.status == "failed" and "aucune réponse finale" in turn.error


def test_a_running_turn_can_be_cancelled_and_a_second_turn_is_refused_meanwhile(env) -> None:
    _no_memory(env)
    conversation = _conversation(env)
    block = threading.Event()
    manager, _ = _manager(env, [], synchronous=False, block=block)
    _, turn = manager.submit(conversation.id, "Une longue réponse")
    deadline = time.monotonic() + 10
    while manager.get(turn.id).status != "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    with pytest.raises(TurnBusy):
        manager.submit(conversation.id, "Encore")
    assert manager.cancel(turn.id)
    while manager.get(turn.id).status == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    final = manager.get(turn.id)
    assert final.status == "cancelled"
    answer = _messages(env, conversation.id)[-1]
    assert answer.status is MessageStatus.CANCELLED and answer.content == ""
    assert not manager.cancel(turn.id)  # déjà terminé
    manager.shutdown()


def test_turns_interrupted_by_a_shutdown_are_closed_explicitly_at_restart(env) -> None:
    from datetime import UTC, datetime

    from apex.conversations.model import MessageRole
    from apex.conversations.repository import MessageRepository
    from apex.db.engine import session_scope
    from apex.db.repositories import ConversationTurnRepository

    conversation = _conversation(env)
    user = MessageRepository(env).create(conversation_id=conversation.id, role=MessageRole.USER, content="?",
                                         model_id=None, provider_id=None, status=MessageStatus.REAL, error=None)
    with session_scope(env) as session:
        ConversationTurnRepository(session).create(id="t-stale", conversation_id=conversation.id,
                                                   user_message_id=user.id, mode="normal", created_at=datetime.now(UTC))
        ConversationTurnRepository(session).update("t-stale", status="running")
    manager, _ = _manager(env, [])
    assert manager.recover_unfinished() == 1
    assert manager.get("t-stale").status == "failed"
    answer = _messages(env, conversation.id)[-1]
    assert answer.status is MessageStatus.ERROR and "APEX s'est arrêté" in answer.error


def test_tool_calls_are_recognised_only_when_the_reply_is_a_call() -> None:
    assert parse_tool_call('{"tool": "calc.evaluate", "arguments": {"expression": "2*3"}}') == (
        "calc.evaluate", {"expression": "2*3"})
    assert parse_tool_call('```json\n{"tool": "fs.list", "arguments": {}}\n```') == ("fs.list", {})
    assert parse_tool_call("Voici un exemple : " + "x" * 300 + ' {"tool": "fs.list"}') is None
    assert parse_tool_call('Le format est {"a": 1}.') is None
    assert parse_tool_call('{"tool": "fs.list", "arguments": [1]}') is None


# -- outils des modes ------------------------------------------------------------------------------------------


def test_calculator_tools_are_exact_and_refuse_anything_but_arithmetic() -> None:
    from apex.tools.model import ToolFailure

    assert assistant_tools.evaluate("(1200 - 350) * 12") == 10200
    assert assistant_tools.evaluate("round(1000 * 1.05 ^ 3, 2)") == 1157.63
    for hostile in ('__import__("os")', "open('x')", "(1).real", "[1, 2]", "2 ** 100000", "1/0"):
        with pytest.raises(ToolFailure):
            assistant_tools.evaluate(hostile)
    loan = assistant_tools.loan(principal=200000, annual_rate_percent=3.5, years=20)
    assert loan["monthly_payment"] == 1159.92 and loan["rows"][-1]["remaining"] == pytest.approx(0, abs=0.05)
    projection = assistant_tools.projection(initial=1000, monthly_contribution=0, annual_rate_percent=12, years=1)
    assert projection["final"]["balance"] == pytest.approx(1120.0, abs=0.01)  # 12 %/an composé mensuellement
    contrast = assistant_tools.contrast("#FFFFFF", "#000000")
    assert contrast["ratio"] == 21.0 and contrast["aaa_normal_text"]
    assert assistant_tools.contrast("#777777", "#FFFFFF")["aa_normal_text"] is False  # 4,48 : refusé AA


# -- pièces jointes ----------------------------------------------------------------------------------------------


def test_only_real_text_attachments_are_accepted(env) -> None:
    from apex.assistant.attachments import AttachmentRefused, AttachmentService
    from apex.projects.service import ProjectService

    project = ProjectService(env).create(name="P", description="", created_by=Subject.HUMAN)
    service = AttachmentService(env)
    kept = service.add(project.id, "../../etc/data.csv", b"mois;montant\njanvier;1200\n")
    assert kept.filename == "data.csv"  # jamais un chemin
    assert kept.sha256 == hashlib.sha256(b"mois;montant\njanvier;1200\n").hexdigest()
    for name, raw, reason in [("photo.png", b"\x89PNG", "non pris en charge"),
                              (".env", b"SECRET=1", "secrets"),
                              ("cle.pem", b"-----BEGIN", "secrets"),
                              ("binaire.txt", b"a\x00b", "binaires"),
                              ("latin.txt", "é".encode("latin-1"), "UTF-8"),
                              ("gros.txt", b"a" * (512 * 1024 + 1), "dépasse"),
                              ("data.csv", b"x", "déjà joint")]:
        with pytest.raises(AttachmentRefused, match=reason):
            service.add(project.id, name, raw)
    context, used = service.render_context(project.id)
    assert used == ["data.csv"] and "janvier;1200" in context
    assert service.delete(kept.id) and service.render_context(project.id) == ("", [])


def test_an_attachment_whose_file_cannot_be_written_is_not_kept(env, monkeypatch) -> None:
    """Constaté sur la version Windows (sous Wine) : l'écriture dans l'espace de travail échouait APRÈS
    l'insertion en base — « refusé » à l'écran, mais la pièce restait dans le projet. Elle est désormais retirée."""
    from apex.assistant import workspace as assistant_workspace
    from apex.assistant.attachments import AttachmentService
    from apex.projects.service import ProjectService

    project = ProjectService(env).create(name="Maison", description="", created_by=Subject.HUMAN)

    class _Broken:
        def write_bytes(self, rel: str, content: bytes) -> bool:
            raise OSError("disque plein")

    monkeypatch.setattr(assistant_workspace, "project_workspace", lambda _project_id: _Broken())
    service = AttachmentService(env)
    with pytest.raises(RuntimeError, match="non enregistré"):
        service.add(project.id, "notes.md", b"# notes")
    assert service.list_all(project.id) == []


# -- paramètres ------------------------------------------------------------------------------------------------


def test_settings_are_validated_persisted_and_applied_to_the_model_runtime(env) -> None:
    from apex.assistant.settings import SettingsService
    from apex.db.engine import session_scope
    from apex.db.repositories import AppSettingRepository
    from apex.models import native

    service = SettingsService(env)
    settings, problems = service.load()
    assert settings.appearance.theme == "night" and problems == []
    updated = service.update({"appearance": {"theme": "dune", "particles": False},
                              "resources": {"model_precision": "bfloat16", "cpu_threads": 2}})
    assert updated.appearance.theme == "dune" and updated.appearance.animations is True
    assert native._SETTINGS == {"precision": "bfloat16", "threads": 2}
    assert SettingsService(env).load()[0].appearance.theme == "dune"  # persisté
    for invalid in ({"appearance": {"theme": "rgb-gamer"}}, {"models": {"temperature": 9}},
                    {"inconnu": {}}, {"tools": {"max_tool_steps": 0}}):
        with pytest.raises(ValueError):
            service.update(invalid)
    assert SettingsService(env).load()[0].appearance.theme == "dune"  # rien enregistré
    with session_scope(env) as session:
        AppSettingRepository(session).put("models", {"temperature": 99}, updated_at=updated_at())
    reloaded, problems = SettingsService(env).load()
    assert reloaded.models.temperature == 0.4 and problems  # valeur invalide en base : défaut + problème signalé
    native.configure(precision="auto", threads=0)


def updated_at():
    from datetime import UTC, datetime

    return datetime.now(UTC)


# -- runtime du modèle : mémoire et intégrité -------------------------------------------------------------------


def test_precision_is_chosen_from_measured_memory_and_refused_explicitly_when_too_low(tmp_path) -> None:
    from apex.models import native

    gib = 1024 ** 3
    params = 1_543_714_304  # Qwen2.5-Coder-1.5B (en-têtes safetensors)
    assert native.choose_precision(params, 16 * gib) == "float32"
    assert native.choose_precision(params, 6 * gib) == "bfloat16"
    with pytest.raises(native.InsufficientMemory, match="mémoire insuffisante"):
        native.choose_precision(params, 3 * gib)
    with pytest.raises(native.InsufficientMemory, match="bfloat16"):
        native.choose_precision(params, 6 * gib, requested="float32")
    assert native.choose_precision(None, None) == "float32"  # non mesurable : jamais un refus supposé
    header = json.dumps({"a": {"dtype": "BF16", "shape": [3, 4], "data_offsets": [0, 24]},
                         "__metadata__": {"format": "pt"}}).encode()
    (tmp_path / "model.safetensors").write_bytes(struct.pack("<Q", len(header)) + header + b"\x00" * 24)
    assert native.parameter_count(tmp_path) == 12
    with pytest.raises(ValueError):
        native.configure(precision="int4")


def test_downloaded_weights_must_match_the_published_sha256(tmp_path) -> None:
    from apex.models.fetch import verify_weights

    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"poids")
    good = hashlib.sha256(b"poids").hexdigest()
    assert verify_weights(tmp_path, {"model.safetensors": good}) == ["model.safetensors"]
    with pytest.raises(RuntimeError, match="empreinte publiée introuvable"):
        verify_weights(tmp_path, {})
    with pytest.raises(RuntimeError, match="empreinte inattendue"):
        verify_weights(tmp_path, {"model.safetensors": "0" * 64})
    assert not weights.exists()  # fichier différent : supprimé, jamais chargé


# -- API --------------------------------------------------------------------------------------------------------


def test_api_full_path_project_conversation_turn_messages(env, monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient

    import apex.api.dependencies as dependencies
    from apex.api.app import create_app
    from apex.api.auth import get_or_create_api_token

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dependencies, "get_session_factory", lambda: env)
    _no_memory(env)
    manager, _ = _manager(env, ['{"tool": "calc.evaluate", "arguments": {"expression": "1500*12"}}',
                                "Total annuel : 18 000 €."])
    monkeypatch.setattr(dependencies, "_turn_manager", manager)
    client = TestClient(create_app())
    h = {"Authorization": f"Bearer {get_or_create_api_token()}"}
    assert client.get("/api/v1/assistant/modes").status_code == 401
    project = client.post("/api/v1/assistant/projects", headers=h,
                          json={"name": "Maison", "instructions": "Montants en euros."}).json()["data"]
    conversation = client.post("/api/v1/assistant/conversations", headers=h,
                               json={"title": "Budget", "project_id": project["id"], "mode": "finance"}).json()["data"]
    assert conversation["mode"] == "finance"
    accepted = client.post(f"/api/v1/assistant/conversations/{conversation['id']}/turns", headers=h,
                           json={"content": "Combien par an pour 1500 €/mois ?"})
    assert accepted.status_code == 202
    turn = client.get(f"/api/v1/assistant/turns/{accepted.json()['data']['turn']['id']}", headers=h).json()["data"]
    assert turn["status"] == "done" and turn["steps"][0]["observation"]["output"]["result"] == 18000
    messages = client.get(f"/api/v1/assistant/conversations/{conversation['id']}/messages", headers=h).json()["data"]
    assert messages[-1]["content"] == "Total annuel : 18 000 €." and messages[-1]["meta"]["mode"] == "finance"
    assert client.post(f"/api/v1/assistant/conversations/{conversation['id']}/turns", headers=h,
                       json={"content": ""}).status_code == 422
    assert client.put("/api/v1/assistant/settings", headers=h,
                      json={"appearance": {"theme": "néon"}}).status_code == 422
    assert client.put("/api/v1/assistant/settings", headers=h,
                      json={"appearance": {"theme": "dune"}}).json()["data"]["appearance"]["theme"] == "dune"
    upload = client.post(f"/api/v1/assistant/projects/{project['id']}/attachments", headers=h,
                         json={"filename": "image.jpg", "content_base64": base64.b64encode(b"\xff\xd8").decode()})
    assert upload.status_code == 422 and "non pris en charge" in upload.json()["detail"]
    workspace = client.get(f"/api/v1/assistant/conversations/{conversation['id']}/workspace", headers=h).json()["data"]
    assert workspace["scope"] == "project"
    assert client.get(f"/api/v1/assistant/conversations/{conversation['id']}/workspace/file",
                      params={"path": "../secret"}, headers=h).status_code == 422
    deleted = client.delete(f"/api/v1/assistant/projects/{project['id']}", headers=h).json()["data"]
    assert deleted["conversations"] == 1
    assert client.get(f"/api/v1/assistant/conversations/{conversation['id']}/messages", headers=h).status_code == 404


def test_a_conversation_or_project_is_never_deleted_under_a_running_turn(env, monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient

    import apex.api.dependencies as dependencies
    from apex.api.app import create_app
    from apex.api.auth import get_or_create_api_token
    from apex.projects.service import ProjectService

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dependencies, "get_session_factory", lambda: env)
    _no_memory(env)
    block = threading.Event()
    manager, _ = _manager(env, [], synchronous=False, block=block)
    monkeypatch.setattr(dependencies, "_turn_manager", manager)
    client = TestClient(create_app())
    h = {"Authorization": f"Bearer {get_or_create_api_token()}"}
    project = ProjectService(env).create(name="Chantier", description="", created_by=Subject.HUMAN)
    conversation = _conversation(env, project_id=project.id)
    assert client.get(f"/api/v1/assistant/conversations/{conversation.id}", headers=h).json()["data"]["title"] == "Essai"
    assert client.get("/api/v1/assistant/conversations/inconnue", headers=h).status_code == 404
    _, turn = manager.submit(conversation.id, "Une longue réponse")
    refused = client.delete(f"/api/v1/assistant/conversations/{conversation.id}", headers=h)
    assert refused.status_code == 409 and "annulez-la" in refused.json()["detail"]
    assert client.delete(f"/api/v1/assistant/projects/{project.id}", headers=h).status_code == 409
    assert manager.cancel(turn.id)
    deadline = time.monotonic() + 10
    while manager.get(turn.id).status in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
    assert client.delete(f"/api/v1/assistant/conversations/{conversation.id}", headers=h).json()["data"] is True
    assert client.get(f"/api/v1/assistant/conversations/{conversation.id}", headers=h).status_code == 404
    assert client.delete(f"/api/v1/assistant/projects/{project.id}", headers=h).json()["data"]["deleted"] is True
    manager.shutdown()


def test_simultaneous_submissions_never_create_two_concurrent_turns(env) -> None:
    from concurrent.futures import ThreadPoolExecutor

    _no_memory(env)
    conversation = _conversation(env)
    block = threading.Event()
    manager, _ = _manager(env, [], synchronous=False, block=block)

    def attempt(i: int) -> str:
        try:
            manager.submit(conversation.id, f"Message {i}")
            return "accepted"
        except TurnBusy:
            return "busy"

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(attempt, range(6)))
    assert outcomes.count("accepted") == 1 and outcomes.count("busy") == 5
    active = [t for t in manager.by_conversation(conversation.id) if t.status in ("queued", "running")]
    assert len(active) == 1
    manager.cancel(active[0].id)
    manager.shutdown()


def test_the_runtime_artifact_ships_the_assistant() -> None:
    from runtime_build.selection import RUNTIME_PACKAGES

    assert "assistant" in RUNTIME_PACKAGES
    assert Path(assistant_tools.__file__).parent.name == "assistant"


def test_memory_is_recalled_across_conversations_and_can_be_disabled_by_privacy_settings(env) -> None:
    from apex.assistant.settings import SettingsService

    first = _conversation(env)
    manager, provider = _manager(env, ["Le code d'accès du coffre de l'atelier est 4812.", "Je m'en souviens."])
    manager.submit(first.id, "Retiens : le code du coffre de l'atelier est 4812.")
    second = _conversation(env)
    manager.submit(second.id, "Quel est le code du coffre de l'atelier ?")
    assert "4812" in provider.requests[-1].prompt  # souvenir RÉEL d'une autre conversation, avec sa source
    assert _messages(env, second.id)[-1].meta["memory"]

    SettingsService(env).update({"privacy": {"index_conversations": False}})
    third = _conversation(env)
    manager2, provider2 = _manager(env, ["Le mot de passe du wifi est azur-77.", "?"])
    manager2.submit(third.id, "Le mot de passe du wifi est azur-77.")
    fourth = _conversation(env)
    manager2.submit(fourth.id, "Quel est le mot de passe du wifi ?")
    assert "azur-77" not in provider2.requests[-1].prompt  # échange non indexé : jamais rappelé ailleurs


def test_code_mode_executes_in_the_real_sandbox_when_it_is_verified(env) -> None:
    """Chaîne RÉELLE : outil code.run -> sandbox_runner -> AgentRuntime (PolicyEngine, clone tracé) -> sandbox
    de CETTE machine, réseau coupé. Ignoré seulement si aucune sandbox n'est vérifiée ici."""
    from apex.sandbox.provider import select_sandbox_provider

    selection = select_sandbox_provider()
    if not selection.report.is_available:
        pytest.skip(f"aucune sandbox vérifiée sur cette machine : {selection.report.detail}")
    _no_memory(env)
    conversation = _conversation(env, mode="code")
    write = json.dumps({"tool": "fs.write", "arguments": {
        "path": "calc.py", "content": "print(sum(range(1, 101)))\n"}})
    run = json.dumps({"tool": "code.run", "arguments": {"path": "calc.py"}})
    provider = _ScriptedProvider([write, run, "Le programme affiche 5050 (exécuté dans la sandbox)."])
    manager = TurnManager(env, provider_factory=lambda _c: provider, config=None,
                          sandbox_selection=lambda: selection, synchronous=True)
    _, turn = manager.submit(conversation.id, "Calcule la somme de 1 à 100 en Python et exécute-le.")
    executed = turn.steps[1]
    assert executed["tool"] == "code.run" and executed["ok"] is True, executed
    assert "5050" in json.dumps(executed["observation"])  # sortie RÉELLE du programme
    assert "code.run" in provider.requests[0].system

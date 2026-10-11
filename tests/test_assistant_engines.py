"""Choix du moteur de réponse (0.4.1) et Cerveau APEX servi.

Demande de l'utilisateur (09/10/2026, après un essai sur Windows réel où Ollama répondait toujours) :
choisir entre Ollama, le moteur intégré d'APEX et le Cerveau APEX (le réseau propre à APEX). Ces tests
prouvent que le choix est RESPECTÉ (jamais un autre moteur à la place), que le Cerveau v1 n'est jamais pris
en automatique, et que les poids servis sont ceux de la version promue du registre (empreintes vérifiées),
jamais des poids modifiés. Le Cerveau tourne ici pour de vrai (1,9 M paramètres, CPU).

Ces tests servent le seul Cerveau v1 (il continue un texte), quel que soit le contenu de `brain/` ; le
Cerveau v2 conversationnel, pris en premier en automatique, a ses propres tests (tests/test_brain_v2.py)."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from apex.assistant import engines
from apex.assistant.settings import SettingsService
from apex.assistant.turns import TurnManager
from apex.brain import artifact
from apex.config.loader import parse_config
from apex.conversations.model import MessageStatus
from apex.models.model import GenerationRequest, ModelCapabilityReport
from apex.models.provider import get_default_provider
from apex.obs.capability import CapabilityStatus
from tests.test_assistant import _conversation, _messages, _no_memory, _ScriptedProvider, _Selection, env  # noqa: F401
from tests.test_conversations_service import BASE_CONFIG

REPO = Path(__file__).resolve().parents[1]
BRAIN = REPO / "brain" / artifact.DEFAULT_NAME


@pytest.fixture(scope="module")
def _v1_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("brain-v1")
    shutil.copytree(BRAIN, root / artifact.DEFAULT_NAME)
    return root


@pytest.fixture(autouse=True)
def _brain_v1_only(_v1_root, monkeypatch) -> None:
    monkeypatch.setenv(artifact.BRAIN_DIR_ENV, str(_v1_root))


def _config_with_both_engines():
    raw = copy.deepcopy(BASE_CONFIG)
    raw["models"]["roles"]["fast"]["candidates"].append(
        {"family": "qwen2.5-coder", "version": "1.5b-instruct", "quantization": "bf16", "capability_class": "fast"})
    raw["models"]["endpoints"].append(
        {"pool_id": "local-gpu", "runtime": "transformers", "url": "file:///nulle-part",
         "served_model": "qwen2.5-coder-1.5b-instruct", "max_context": 32768, "vram_gb": 3.5})
    return parse_config(raw)


def _runtimes(candidates):
    return {c.endpoint.runtime for c in candidates}


def test_each_engine_offers_only_its_own_models_and_auto_never_takes_the_brain() -> None:
    config = _config_with_both_engines()
    assert _runtimes(engines.candidates_for("ollama", config)) == {"ollama"}
    assert _runtimes(engines.candidates_for("integrated", config)) == {"transformers"}
    assert _runtimes(engines.candidates_for("brain", config)) == {"apex_brain"}
    assert _runtimes(engines.candidates_for("auto", config)) == {"ollama", "transformers"}
    # Le Cerveau n'est candidat d'aucun rôle de la configuration : ni tâches, ni missions, ni `apex solve`.
    for path in (REPO / "config.yaml", REPO / "config.standalone.yaml"):
        assert "apex_brain" not in path.read_text(encoding="utf-8")


def test_engines_report_measured_availability_and_flag_the_brain_experimental() -> None:
    config = _config_with_both_engines()

    def detect(endpoint):
        ok = endpoint.runtime != "ollama"  # Ollama non démarré
        return ModelCapabilityReport(status=CapabilityStatus.AVAILABLE if ok else CapabilityStatus.UNAVAILABLE_ON_DEV,
                                     backend=endpoint.runtime, detail="présent" if ok else "Ollama injoignable")

    rows = {row["id"]: row for row in engines.describe(config, detect=detect)}
    assert list(rows) == ["auto", "ollama", "integrated", "brain"]
    assert not rows["ollama"]["available"] and rows["ollama"]["detail"] == "Ollama injoignable"
    assert rows["integrated"]["available"] and rows["brain"]["available"]
    assert rows["brain"]["experimental"] and not rows["integrated"]["experimental"]
    assert rows["auto"]["available"] and {m["runtime"] for m in rows["auto"]["models"]} == {"ollama", "transformers"}


def _manager(factory, provider, config=None):
    seen = []

    def factory_for(candidate):
        seen.append(candidate.endpoint.runtime)
        return provider if provider is not None else get_default_provider(candidate.endpoint)

    manager = TurnManager(factory, provider_factory=factory_for, config=config or _config_with_both_engines(),
                          sandbox_selection=lambda: _Selection(False), synchronous=True)
    return manager, seen


@pytest.mark.parametrize(("engine", "runtime"), [("ollama", "ollama"), ("integrated", "transformers")])
def test_the_chosen_engine_answers_never_another_one(env, engine, runtime) -> None:  # noqa: F811
    _no_memory(env)
    SettingsService(env).update({"models": {"engine": engine}})
    manager, seen = _manager(env, _ScriptedProvider(["réponse"]))
    conversation = _conversation(env)
    _message, turn = manager.submit(conversation.id, "Bonjour")
    assert manager.get(turn.id).status == "done"
    assert set(seen) == {runtime}
    answer = _messages(env, conversation.id)[-1]
    assert answer.provider_id.startswith(f"{runtime}:")


def test_a_preferred_model_outside_the_chosen_engine_is_refused_explicitly(env) -> None:  # noqa: F811
    _no_memory(env)
    SettingsService(env).update({"models": {"engine": "integrated", "preferred_model": "qwen2.5-coder:7b-instruct:q4_k_m"}})
    manager, seen = _manager(env, _ScriptedProvider(["jamais"]))
    conversation = _conversation(env)
    _message, turn = manager.submit(conversation.id, "Bonjour")
    view = manager.get(turn.id)
    assert view.status == "failed" and "n'appartient pas au moteur « APEX intégré »" in view.error and seen == []


def test_the_brain_engine_really_runs_apexlm_and_says_it_is_experimental(env) -> None:  # noqa: F811
    _no_memory(env)
    SettingsService(env).update({"models": {"engine": "brain", "temperature": 0.0, "max_tokens": 64}})
    manager, seen = _manager(env, None)
    conversation = _conversation(env)
    _message, turn = manager.submit(conversation.id, "Return the")
    view = manager.get(turn.id)
    assert view.status == "done", view.error
    assert seen == ["apex_brain"]
    answer = _messages(env, conversation.id)[-1]
    assert answer.status is MessageStatus.REAL and answer.content
    assert answer.provider_id == f"apex_brain:{artifact.DEFAULT_NAME}" and answer.model_id == "apex-brain:v1:fp32"
    assert answer.meta["runtime"]["backend"] == "apex_brain"


def test_the_served_brain_is_the_promoted_registry_version() -> None:
    provenance = json.loads((BRAIN / "provenance.json").read_text(encoding="utf-8"))
    registry = [json.loads(line) for line in (REPO / "docs" / "neural" / "registry.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    entry = next(e for e in registry if e["name"] == provenance["name"])
    assert entry["promoted"] and entry["decision"] == "accept"
    assert provenance["model_fingerprint"] == entry["reports"]["heldout"]["model_fingerprint"]
    assert provenance["dataset_fingerprint"] == entry["dataset_fingerprint"]
    loaded = artifact.load(BRAIN)  # recalcule l'empreinte des poids réellement chargés
    assert loaded.fingerprint == entry["reports"]["heldout"]["model_fingerprint"]
    assert loaded.model.num_parameters() == provenance["parameters"] == 1_918_656


def test_modified_brain_weights_are_never_served(tmp_path) -> None:
    copy_dir = tmp_path / artifact.DEFAULT_NAME
    shutil.copytree(BRAIN, copy_dir)
    weights = copy_dir / artifact.WEIGHTS
    data = bytearray(weights.read_bytes())
    data[-1] ^= 0x01
    weights.write_bytes(bytes(data))
    candidate = engines.brain_candidate()
    endpoint = type(candidate.endpoint)(**{**candidate.endpoint.__dict__, "url": f"file://{tmp_path}"})
    report = get_default_provider(endpoint).detect()
    assert not report.is_available and "modifiés" in report.detail
    from apex.errors import ModelProviderError

    with pytest.raises(ModelProviderError):
        get_default_provider(endpoint).generate(GenerationRequest(endpoint=endpoint, prompt="x", timeout_s=10))


def test_weights_whose_fingerprint_disagrees_with_their_provenance_are_refused(tmp_path) -> None:
    copy_dir = tmp_path / "copie"
    shutil.copytree(BRAIN, copy_dir)
    provenance = json.loads((copy_dir / "provenance.json").read_text(encoding="utf-8"))
    provenance["model_fingerprint"] = "sha256:" + "0" * 64
    (copy_dir / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    with pytest.raises(ValueError, match="Cerveau APEX refusé"):
        artifact.load(copy_dir)


def test_the_brain_only_receives_the_new_user_message() -> None:
    from apex.models.apex_brain import last_user_message

    composed = ("Échanges précédents de cette conversation (du plus ancien au plus récent) :\nUtilisateur : a\n"
                "APEX : b\n\nNouveau message de l'utilisateur :\nRéponds-moi")
    assert last_user_message(composed) == "Réponds-moi"
    assert last_user_message("  seul message ") == "seul message"


def test_the_runtime_artifact_ships_the_brain_inference_not_its_training() -> None:
    from runtime_build.selection import RESEARCH_PACKAGES, RUNTIME_PACKAGES

    assert "brain" in RUNTIME_PACKAGES and "neural" in RESEARCH_PACKAGES
    for module in (REPO / "apex" / "brain").glob("*.py"):
        source = module.read_text(encoding="utf-8")
        imports = [line for line in source.splitlines() if line.lstrip().startswith(("from apex.", "import apex."))]
        assert not [line for line in imports if "apex.neural" in line or "apex.knowledge" in line], module.name

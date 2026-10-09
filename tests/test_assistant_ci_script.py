"""Le script de validation CI de l'assistant (setup/packaging/windows/ci/assistant_e2e.py) — testé ici avec un
DOUBLE de modèle (réponses scriptées) : ce test vérifie la logique du script (verdicts, preuves, persistance),
jamais le comportement du vrai modèle, qui n'est prouvé que par le script exécuté contre un APEX réel."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_assistant import _manager, env  # noqa: F401 — fixture réutilisée

SCRIPT = Path(__file__).resolve().parents[1] / "setup" / "packaging" / "windows" / "ci" / "assistant_e2e.py"


def _load():
    spec = importlib.util.spec_from_file_location("assistant_e2e", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client_api(env, monkeypatch, tmp_path):  # noqa: F811
    from fastapi.testclient import TestClient

    import apex.api.dependencies as dependencies
    from apex.api.app import create_app
    from apex.api.auth import get_or_create_api_token

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dependencies, "get_session_factory", lambda: env)
    replies = [
        '{"tool": "finance.loan", "arguments": {"principal": 200000, "annual_rate_percent": 3.5, "years": 20}}',
        "La mensualité est de 1 159,92 €.",
        '{"tool": "fs.write", "arguments": {"path": "somme.py", "content": "def add(a, b):\\n    return a + b\\n\\nprint(add(2, 3))\\n"}}',
        "J'ai créé somme.py (non exécuté : la sandbox n'est pas vérifiée ici).",
    ]
    manager, _ = _manager(env, replies)
    monkeypatch.setattr(dependencies, "_turn_manager", manager)
    client = TestClient(create_app())
    module = _load()
    token = get_or_create_api_token()

    class ClientApi(module.Api):
        def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
            if path == "/assistant/models":
                # La détection réelle ne voit aucun modèle ici : le DOUBLE scripté est déclaré disponible, explicitement.
                return 200, {"status": "real", "data": [{"id": "double-de-test", "runtime": "test", "served_model": "test",
                                                         "available": True, "detail": None}]}
            response = client.request(method, "/api/v1" + path, json=body, headers={"Authorization": f"Bearer {self.token}"})
            return response.status_code, response.json()

    return module, ClientApi("http://test", token), client


def test_the_ci_script_passes_on_real_answers_and_verifies_persistence(client_api, tmp_path) -> None:
    module, api, _ = client_api
    out = tmp_path / "evidence"
    out.mkdir()
    assert module.run(api, out, timeout_s=30) == module.PASS
    evidence = json.loads((out / "assistant-run.json").read_text(encoding="utf-8"))
    assert evidence["verdict"] == "PASS" and evidence["finance_loan_called"] is True
    assert {c["check"] for c in evidence["checks"]} >= {"A2_tour_finance_reponse_reelle_du_modele", "A3_outil_finance_loan_exact",
                                                        "A5_fichier_ecrit_present_dans_l_espace", "A6_reglage_enregistre"}
    assert evidence["code_run_offered"] is False  # sandbox non vérifiée dans ce test : jamais proposée
    assert module.verify(api, out) == module.PASS


def test_the_ci_script_reports_blocked_without_a_model_and_never_pass(client_api, tmp_path, monkeypatch) -> None:
    module, api, _ = client_api
    original = api.data

    def no_model(method: str, path: str, body: Any = None) -> Any:
        if path == "/assistant/models":
            return [{"id": "coder", "runtime": "transformers", "served_model": "q", "available": False, "detail": "poids absents"}]
        return original(method, path, body)

    monkeypatch.setattr(api, "data", no_model)
    assert module.run(api, tmp_path, timeout_s=5) == module.BLOCKED
    assert json.loads((tmp_path / "assistant-run.json").read_text(encoding="utf-8"))["verdict"] == "BLOCKED"
    assert module.verify(api, tmp_path) == module.BLOCKED


def test_a_lost_conversation_after_restart_is_a_failure(client_api, tmp_path) -> None:
    module, api, client = client_api
    assert module.run(api, tmp_path, timeout_s=30) == module.PASS
    state = json.loads((tmp_path / "assistant-state.json").read_text(encoding="utf-8"))
    lost = next(iter(state["conversations"]))
    assert client.delete(f"/api/v1/assistant/conversations/{lost}", headers={"Authorization": f"Bearer {api.token}"}).status_code == 200
    assert module.verify(api, tmp_path) == module.FAIL

"""L'interface web servie par `apex serve` et ouverte depuis le Panel (apex/api/ui.py).

Ce qui est prouvé ici, contre la vraie application FastAPI :
- le build de l'interface est servi à la racine (routes de l'interface comprises), jamais à la place
  d'une route d'API ou d'un fichier absent ; sans build, une page dit comment le construire ;
- l'ouverture passe par un code À USAGE UNIQUE, obtenu avec le jeton, échangé contre une session
  (cookie HttpOnly, SameSite=Strict) — le jeton ne quitte jamais le Panel ;
- une session n'authentifie l'API qu'avec l'en-tête `X-APEX-UI`, depuis la même origine ;
- un jeton faux n'est jamais rattrapé par un cookie valide.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apex.api import ui
from apex.api.app import create_app
from tests.api_test_support import TEST_API_TOKEN, authed_client

BEARER = {"Authorization": f"Bearer {TEST_API_TOKEN}"}
UI = {"X-APEX-UI": "1"}


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "store", ui._Store())


@pytest.fixture
def dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>APEX Control Center</title>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log('apex')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("hors du build", encoding="utf-8")
    monkeypatch.setenv(ui.UI_DIST_ENV, str(root))
    return root


def _login(client: TestClient) -> TestClient:
    code_path = authed_client().post("/api/v1/ui/launch-code").json()["data"]["login_path"]
    response = client.get(code_path, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    return client


# -- fichiers de l'interface ----------------------------------------------------------------------

def test_the_built_interface_is_served_at_the_root(dist: Path) -> None:
    client = TestClient(create_app())

    assert "APEX Control Center" in client.get("/").text
    assert client.get("/assets/app.js").text == "console.log('apex')"
    # Routes de l'interface (React Router) : la page, pas un 404.
    assert "APEX Control Center" in client.get("/agents/some-id").text
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/..%2Fsecret.txt").status_code == 404
    assert "hors du build" not in client.get("/../secret.txt").text


def test_without_a_build_the_page_says_how_to_build_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ui.UI_DIST_ENV, str(tmp_path / "absent"))

    response = TestClient(create_app()).get("/")

    assert response.status_code == 503
    assert ui.BUILD_COMMAND in response.text and "non construite" in response.text


def test_static_pages_never_shadow_the_api(dist: Path) -> None:
    client = TestClient(create_app())

    assert client.get("/api/v1/status").status_code == 401  # route réelle : toujours protégée
    assert client.get("/api/v1/nope").status_code == 404
    assert client.get("/openapi.json").status_code == 404 and client.get("/docs").status_code == 404


# -- ouverture depuis le Panel : code à usage unique -> session ---------------------------------------

def test_the_launch_code_requires_the_token() -> None:
    assert TestClient(create_app()).post("/api/v1/ui/launch-code").status_code == 401


def test_opening_from_the_panel_gives_a_session_that_reads_the_real_api(dist: Path) -> None:
    payload = authed_client().post("/api/v1/ui/launch-code").json()["data"]
    assert payload["ui_available"] is True and payload["expires_in_s"] == 60
    assert TEST_API_TOKEN not in payload["login_path"]

    client = TestClient(create_app())
    response = client.get(payload["login_path"], follow_redirects=False)

    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert TEST_API_TOKEN not in response.headers["set-cookie"]
    status = client.get("/api/v1/status", headers=UI)
    assert status.status_code == 200 and status.json()["data"]["service"] == "apex"


def test_a_launch_code_works_once(dist: Path) -> None:
    path = authed_client().post("/api/v1/ui/launch-code").json()["data"]["login_path"]

    assert TestClient(create_app()).get(path, follow_redirects=False).status_code == 303
    second = TestClient(create_app()).get(path, follow_redirects=False)
    assert second.status_code == 401 and "Rouvrez APEX depuis le Panel" in second.text


def test_an_expired_launch_code_is_refused(dist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = authed_client().post("/api/v1/ui/launch-code").json()["data"]["login_path"]
    real = ui.time.monotonic
    monkeypatch.setattr(ui.time, "monotonic", lambda: real() + ui.LAUNCH_CODE_TTL_S + 1)

    assert TestClient(create_app()).get(path, follow_redirects=False).status_code == 401


def test_a_forged_code_is_refused() -> None:
    assert TestClient(create_app()).get("/ui/login?code=forge", follow_redirects=False).status_code == 401
    assert TestClient(create_app()).get("/ui/login", follow_redirects=False).status_code == 401


# -- la session n'authentifie que l'interface elle-même -------------------------------------------------

def test_a_session_without_the_interface_header_is_refused(dist: Path) -> None:
    client = _login(TestClient(create_app()))

    assert client.get("/api/v1/status").status_code == 401  # ex. <img src> ou formulaire d'un autre site


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "http://localhost:5173"},  # autre port de la même machine : même site, pas même origine
    {"Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_a_session_used_from_another_origin_is_refused(dist: Path, headers: dict[str, str]) -> None:
    client = _login(TestClient(create_app()))

    assert client.get("/api/v1/status", headers={**UI, **headers}).status_code == 401


def test_same_origin_requests_are_accepted(dist: Path) -> None:
    client = _login(TestClient(create_app()))

    response = client.post("/api/v1/ui/launch-code",
                           headers={**UI, "Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 200


def test_a_wrong_token_is_never_rescued_by_a_valid_session(dist: Path) -> None:
    client = _login(TestClient(create_app()))

    response = client.get("/api/v1/status", headers={**UI, "Authorization": "Bearer faux"})
    assert response.status_code == 401 and response.json()["detail"] == "jeton invalide"


def test_logout_ends_the_session(dist: Path) -> None:
    client = _login(TestClient(create_app()))
    assert client.get("/api/v1/status", headers=UI).status_code == 200

    assert client.post("/ui/logout", headers=UI).status_code == 204
    client.cookies.clear()
    assert client.get("/api/v1/status", headers=UI).status_code == 401


def test_sessions_do_not_survive_a_restart_of_apex(dist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _login(TestClient(create_app()))

    monkeypatch.setattr(ui, "store", ui._Store())  # nouveau processus `apex serve`

    assert client.get("/api/v1/status", headers=UI).status_code == 401


# -- verdict partagé ------------------------------------------------------------------------------------

def test_status_reports_the_interface_and_the_gate_decision_apart_from_phase_2(
    dist: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from apex.obs import component_checks as cc

    monkeypatch.setattr(cc, "CHECKS", {"database": lambda: cc.ComponentStatus("database", cc.READY, "ok")})
    report = tmp_path / "environment.json"
    report.write_text(json.dumps({
        "generated_at": "2026-10-02T19:00:00Z", "phase_2_authorized": False,
        "startup": {"authorized": True, "blocking": [], "unverified_blocking": [],
                    "unverified": [{"check_id": "GATE-17", "reason": "not_authorized", "role": "consent"}]},
    }), encoding="utf-8")
    monkeypatch.setattr("apex.gate.report.DEFAULT_REPORT_PATH", str(report))

    data = authed_client().get("/api/v1/status").json()["data"]

    assert {c["component"]: c["state"] for c in data["components"]} == {"api": cc.READY, "database": cc.READY, "ui": cc.READY}
    assert data["overall"] == {"verdict": "ready", "not_ready": []}
    assert data["gate"] == {"startup_authorized": True, "phase_2_authorized": False,
                            "startup_unverified": ["GATE-17"], "generated_at": "2026-10-02T19:00:00Z"}


def test_an_unreadable_gate_report_is_reported_as_unknown(tmp_path: Path) -> None:
    from apex.api.routes import status

    assert status.gate_summary(tmp_path / "absent.json") is None


def test_panel_and_backend_judge_the_same_components() -> None:
    """Verdict PARTAGÉ : le Panel (C#) déclare READY seulement si chaque ligne est prête ; le backend
    (`overall`) aussi. Pour que les deux disent toujours la même chose, ils doivent connaître exactement
    les mêmes composants — un composant ajouté d'un seul côté fait échouer ce test."""
    import re

    from apex.obs import component_checks as cc

    source = (Path(__file__).resolve().parents[1] / "setup" / "src" / "ApexSetup.Dashboard" / "Models"
              / "ComponentKey.cs").read_text(encoding="utf-8")
    panel = set(re.findall(r'\(ComponentKey\.\w+, "(\w+)"\)', source))
    assert panel == set(cc.CHECKS) | {"ui"}

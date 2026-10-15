"""The real API boundary must reject unsafe inputs before launching Blender."""

from __future__ import annotations

import base64
import io

from fastapi.testclient import TestClient
from PIL import Image

from apex.api.app import create_app
from tests.api_test_support import authed_client


def request() -> dict:
    return {
        "creation_type": "vehicule", "style": "realiste", "quality": "prototype",
        "tool": "auto", "description": "Une voiture sportive réaliste avec quatre rendus détaillés.",
    }


def test_game_studio_routes_require_existing_api_token():
    client = TestClient(create_app())
    assert client.get("/api/v1/game-studio/capabilities").status_code == 401
    assert client.post("/api/v1/game-studio/missions", json=request()).status_code == 401
    assert client.get("/api/v1/game-studio/missions/a/artifacts/b").status_code == 401


def test_unimplemented_unreal_tool_is_rejected_before_mission():
    response = authed_client().post(
        "/api/v1/game-studio/missions", json={**request(), "tool": "unreal"}
    )
    assert response.status_code == 422
    assert "not_implemented" in response.text


def test_image_reference_mime_is_verified_from_bytes():
    image = Image.new("RGB", (2, 2))
    out = io.BytesIO()
    image.save(out, format="PNG")
    payload = {**request(), "references": [{
        "content_type": "image/jpeg", "data_base64": base64.b64encode(out.getvalue()).decode(),
        "provenance": "User supplied reference",
    }]}
    response = authed_client().post("/api/v1/game-studio/missions", json=payload)
    assert response.status_code == 422
    assert "reference_type_mismatch" in response.text


def test_quality_criterion_rejects_wrong_type():
    response = authed_client().post(
        "/api/v1/game-studio/missions",
        json={**request(), "criteria": {"max_triangles": "many"}},
    )
    assert response.status_code == 422
    assert "invalid_quality_criterion_type" in response.text


def test_artifact_path_cannot_escape_job():
    response = authed_client().get("/api/v1/game-studio/missions/not-a-uuid/artifacts/bad")
    assert response.status_code == 404


def test_project_routes_require_existing_api_token():
    client = TestClient(create_app())
    assert client.get("/api/v1/game-studio/projects").status_code == 401
    assert client.post("/api/v1/game-studio/projects", json=request()).status_code == 401
    assert client.post("/api/v1/game-studio/projects/not-a-uuid/cancel").status_code == 401
    assert client.post("/api/v1/game-studio/projects/not-a-uuid/improve-framing").status_code == 401
    assert client.get("/api/v1/game-studio/projects/not-a-uuid/artifacts/bad").status_code == 401


def test_project_rejects_unavailable_tool_before_launch():
    response = authed_client().post(
        "/api/v1/game-studio/projects",
        json={**request(), "creation_type": "environnement", "tool": "unreal"},
    )
    assert response.status_code == 422
    assert "garage_recipe_requires_realistic_blender_environment" in response.text


def test_project_does_not_claim_to_use_unread_visual_references():
    response = authed_client().post(
        "/api/v1/game-studio/projects",
        json={**request(), "creation_type": "environnement", "references": [{
            "content_type": "text/plain", "data_base64": base64.b64encode(b"reference").decode(),
            "provenance": "User supplied reference",
        }]},
    )
    assert response.status_code == 422
    assert "garage_visual_references_not_consumed_by_controller" in response.text


def test_scene_revision_requires_an_existing_project(monkeypatch):
    from apex.api.routes import game_studio

    class MissingProject:
        def improve_framing(self, _project_id):
            raise LookupError("game_studio_project_missing")

    monkeypatch.setattr(game_studio, "_projects", lambda: MissingProject())
    response = authed_client().post(
        "/api/v1/game-studio/projects/00000000-0000-0000-0000-000000000000/improve-framing"
    )
    assert response.status_code == 404

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

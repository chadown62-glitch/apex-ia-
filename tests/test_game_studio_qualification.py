"""Evidence-gate tests, deliberately no claim of model or Blender execution."""

import hashlib
import json

import httpx
import pytest

from apex.game_studio.qualification import collect_api_artifacts, verify_real_mission


def test_review_and_fixture_cannot_qualify_a_model(tmp_path):
    result = verify_real_mission({"status": "review", "steps": []}, tmp_path)
    assert not result["passed"]
    assert not result["checks"]["pinned_planner"]
    assert not result["checks"]["real_validated_response"]


def test_collects_project_and_child_artifacts_using_ownership_endpoints(tmp_path):
    data = b'{"rejected":true}'
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    item = {"hash": digest, "name": "../never-write-this.json"}
    project = {
        "id": "parent",
        "planning_artifact": item,
        "steps": [
            {
                "handoff": {"artifacts": [item]},
                "mission": {
                    "id": "child",
                    "revisions": [{"artifacts": [item]}],
                    "controller_attempts": [item],
                },
            }
        ],
    }
    paths = []

    def response(request):
        paths.append(request.url.path)
        return httpx.Response(200, content=data)

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(response)) as client:
        entries = collect_api_artifacts(client, project, tmp_path)
    assert len(entries) == 4
    assert paths.count("/api/v1/game-studio/projects/parent/artifacts/" + digest) == 2
    assert paths.count("/api/v1/game-studio/missions/child/artifacts/" + digest) == 2
    assert (tmp_path / digest.removeprefix("sha256:")).read_bytes() == data
    assert not (tmp_path.parent / "never-write-this.json").exists()
    assert len(json.loads((tmp_path / "index.json").read_text())) == 4


def test_corrupt_download_is_never_accepted(tmp_path):
    project = {"id": "parent", "steps": [], "planning_artifact": {"hash": "sha256:" + "0" * 64}}
    with (
        httpx.Client(
            base_url="http://test",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"corrupted")),
        ) as client,
        pytest.raises(RuntimeError, match="hash_mismatch"),
    ):
        collect_api_artifacts(client, project, tmp_path)
    assert list(tmp_path.iterdir()) == []

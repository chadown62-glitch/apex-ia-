"""Tool Engine permissions and workspace ownership guard brain delegation."""

from __future__ import annotations

from uuid import uuid4

from apex.game_studio.delegation import register_game_studio_tools
from apex.tools.engine import ToolContext, ToolEngine, ToolRegistry
from apex.tools.model import ErrorCode, Permission, ToolCall
from apex.tools.workspace import Workspace


class Service:
    def __init__(self):
        self.calls = 0
        self.project_id = str(uuid4())

    def create_dynamic(self, request, refs, *, owner_workspace_id):
        self.calls += 1
        assert not refs and owner_workspace_id == "owner"
        return {"id": self.project_id, "mission_id": "mission", "status": "queued"}

    def get(self, project_id):
        assert project_id == self.project_id
        return {
            "id": project_id,
            "mission_id": "mission",
            "status": "review",
            "owner_workspace_id": "owner",
            "steps": [],
            "events": [],
            "assembly": None,
        }


def test_delegation_is_nonblocking_permissioned_and_workspace_scoped(tmp_path):
    service = Service()
    registry = ToolRegistry()
    register_game_studio_tools(registry)
    engine = ToolEngine(registry)
    workspace = Workspace.create(tmp_path, "owner")
    ctx = ToolContext(workspace=workspace, granted=frozenset(), extra={"game_studio_service": service})

    def call(tool, arguments):
        return engine.execute(ToolCall(tool, arguments, str(uuid4()), "test"), ctx)

    args = {"description": "Create a detailed sports car with four renders."}
    assert call("game_studio.create", args).error.code is ErrorCode.PERMISSION_DENIED
    assert service.calls == 0
    ctx.granted = frozenset({Permission.GAME_STUDIO_CREATE})
    assert (
        call("game_studio.create", {**args, "arbitrary_code": "print(1)"}).error.code
        is ErrorCode.INVALID_INPUT
    )
    created = call("game_studio.create", args)
    assert created.ok and created.output["project_id"] == service.project_id
    assert service.calls == 1
    assert (
        call("game_studio.status", {"project_id": service.project_id}).error.code
        is ErrorCode.PERMISSION_DENIED
    )
    ctx.granted = frozenset({Permission.GAME_STUDIO_READ})
    assert call("game_studio.status", {"project_id": service.project_id}).ok
    ctx.workspace = Workspace.create(tmp_path, "other")
    assert call("game_studio.results", {"project_id": service.project_id}).error.code is ErrorCode.NOT_FOUND

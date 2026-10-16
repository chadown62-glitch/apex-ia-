"""Tool Engine delegation boundary for the assistant to register when ready.

No assistant registry is modified here. Tools expose small receipts and digests;
artifact bytes stay behind the authenticated Game Studio API.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from apex.tools.engine import ToolContext, ToolRegistry
from apex.tools.model import ErrorCode, Permission, ToolFailure, ToolSpec


def _service(ctx: ToolContext):
    if "game_studio_service" in ctx.extra:
        return ctx.extra["game_studio_service"]
    from apex.api.dependencies import get_game_studio_project_service

    return get_game_studio_project_service()


def _owned(ctx: ToolContext, project_id: str) -> dict[str, Any]:
    try:
        UUID(project_id)
        record = _service(ctx).get(project_id)
    except (ValueError, LookupError) as exc:
        raise ToolFailure(ErrorCode.NOT_FOUND, "game_studio_project_missing") from exc
    if record.get("owner_workspace_id") != ctx.workspace.id:
        raise ToolFailure(ErrorCode.NOT_FOUND, "game_studio_project_missing")
    return record


def _create(args: dict[str, Any], ctx: ToolContext, _spec: ToolSpec) -> dict[str, Any]:
    request = {
        "creation_type": "environnement",
        "style": "realiste",
        "quality": args.get("quality", "prototype"),
        "tool": args.get("tool", "auto"),
        "compute_backend": args.get("compute_backend", "auto"),
        "controller_model": "auto",
        "description": args["description"],
        "judges_all_tasks": args.get("judges_all_tasks", True),
        "automatic_corrections": args.get("automatic_corrections", True),
        "max_time_s": args.get("max_time_s", 1800),
        "max_rss_mib": args.get("max_rss_mib", 12000),
        "max_attempts": args.get("max_attempts", 3),
        "criteria": {},
        "plan_only": False,
    }
    try:
        receipt = _service(ctx).create_dynamic(request, [], owner_workspace_id=ctx.workspace.id)
    except ValueError as exc:
        raise ToolFailure(ErrorCode.INVALID_INPUT, str(exc)[:300]) from exc
    except RuntimeError as exc:
        raise ToolFailure(ErrorCode.UNAVAILABLE, str(exc)[:300]) from exc
    return {
        "project_id": receipt["id"],
        "mission_id": receipt["mission_id"],
        "status": receipt["status"],
        "poll_tool": "game_studio.status",
    }


def _status(args: dict[str, Any], ctx: ToolContext, _spec: ToolSpec) -> dict[str, Any]:
    record = _owned(ctx, args["project_id"])
    return {
        "project_id": record["id"],
        "mission_id": record["mission_id"],
        "status": record["status"],
        "plan": record.get("plan"),
        "steps": [
            {
                "key": step["key"],
                "kind": step.get("kind"),
                "status": step["status"],
                "job_id": step.get("job_id"),
            }
            for step in record["steps"]
        ],
        "delivery_status": record.get("delivery_status"),
        "budget": record.get("budget"),
        "last_event": record["events"][-1] if record["events"] else None,
    }


def _cancel(args: dict[str, Any], ctx: ToolContext, _spec: ToolSpec) -> dict[str, Any]:
    _owned(ctx, args["project_id"])
    record = _service(ctx).cancel(args["project_id"])
    return {"project_id": record["id"], "status": record["status"]}


def _results(args: dict[str, Any], ctx: ToolContext, _spec: ToolSpec) -> dict[str, Any]:
    record = _owned(ctx, args["project_id"])
    if record["status"] not in ("review", "failed", "cancelled"):
        return {"project_id": record["id"], "status": record["status"], "ready": False}
    assembly = record.get("assembly") or {}
    artifacts = [
        {"name": item["name"], "hash": item["hash"], "role": item["role"], "source": "assembly"}
        for item in assembly.get("artifacts", [])
        if item["role"] in ("view", "blender_project", "exchange")
    ]
    for step in record["steps"]:
        for item in (step.get("handoff") or {}).get("artifacts", []):
            if item["name"].startswith("after/"):
                artifacts.append(
                    {
                        "name": item["name"],
                        "hash": item["hash"],
                        "role": "engineer_contract",
                        "source": step["key"],
                        "project_id": record["id"],
                    }
                )
        child = step.get("mission") or {}
        selected = child.get("selected_revision_number")
        if selected and 1 <= selected <= len(child.get("revisions", [])):
            artifacts.extend(
                {
                    "name": item["name"],
                    "hash": item["hash"],
                    "role": item["role"],
                    "source": step["key"],
                    "job_id": child["id"],
                }
                for item in child["revisions"][selected - 1]["artifacts"]
                if item["role"] in ("view", "blender_project", "exchange")
            )
    return {
        "project_id": record["id"],
        "status": record["status"],
        "ready": bool(artifacts),
        "delivery_status": record.get("delivery_status"),
        "artifacts": artifacts[:40],
        "artifact_count": len(artifacts),
        "truncated": len(artifacts) > 40,
        "blocked": [
            {"key": s["key"], "reason": s.get("reason")}
            for s in record["steps"]
            if s["status"] in ("blocked_dependency", "not_implemented")
        ],
        "api_path": "/api/v1/game-studio/projects/" + record["id"],
    }


_ID = {"type": "string", "minLength": 36, "maxLength": 36}
_ID_INPUT = {
    "type": "object",
    "required": ["project_id"],
    "properties": {"project_id": _ID},
    "additionalProperties": False,
}
_OUTPUT = {"type": "object", "additionalProperties": True}


def register_game_studio_tools(registry: ToolRegistry) -> None:
    """Claude can opt into these specs; registration never happens implicitly."""
    registry.register(
        ToolSpec(
            name="game_studio.create",
            description="Start a bounded creative mission; returns IDs immediately.",
            input_schema={
                "type": "object",
                "required": ["description"],
                "additionalProperties": False,
                "properties": {
                    "description": {"type": "string", "minLength": 20, "maxLength": 4000},
                    "quality": {
                        "type": "string",
                        "enum": ["prototype", "jouable", "soigne", "photorealiste"],
                    },
                    "tool": {"type": "string", "enum": ["auto", "blender", "unreal", "hybride"]},
                    "compute_backend": {"type": "string", "enum": ["auto", "cuda", "cpu"]},
                    "judges_all_tasks": {"type": "boolean"},
                    "automatic_corrections": {"type": "boolean"},
                    "max_time_s": {"type": "integer", "minimum": 120, "maximum": 3600},
                    "max_rss_mib": {"type": "integer", "minimum": 512, "maximum": 16384},
                    "max_attempts": {"type": "integer", "minimum": 1, "maximum": 3},
                },
            },
            output_schema=_OUTPUT,
            permissions=frozenset({Permission.GAME_STUDIO_CREATE}),
        ),
        _create,
    )
    for name, permission, handler, description in (
        (
            "game_studio.status",
            Permission.GAME_STUDIO_READ,
            _status,
            "Read an owned mission and its task graph.",
        ),
        ("game_studio.cancel", Permission.GAME_STUDIO_CANCEL, _cancel, "Cancel an owned mission."),
        (
            "game_studio.results",
            Permission.GAME_STUDIO_READ,
            _results,
            "Read owned result digests, not bytes.",
        ),
    ):
        registry.register(
            ToolSpec(
                name=name,
                description=description,
                input_schema=_ID_INPUT,
                output_schema=_OUTPUT,
                permissions=frozenset({permission}),
            ),
            handler,
        )

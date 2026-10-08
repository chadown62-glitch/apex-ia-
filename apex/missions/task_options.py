"""Options EXPLICITES d'exécution d'une Mission par le TaskLoop (unification Missions/TaskLoop, 06/10/2026).

Fixées à la création de la mission, validées ici une fois pour toutes, puis transmises telles quelles à
`apex.taskloop.runs.TaskRunService.start` : aucune permission d'outil n'est jamais déduite ni ajoutée
implicitement (une tâche « projet » sans `workspace.write` est refusée dès la création, pas « réparée »).
"""

from __future__ import annotations

from typing import Any

#: Clés acceptées et leur validation. Tout le reste est refusé (jamais ignoré silencieusement).
ALLOWED_KEYS = ("max_attempts", "plan_first", "plan_policy", "retry_adaptation", "tool_permissions", "max_tool_steps",
                "use_memory")


def normalize_task_options(options: dict[str, Any] | None, *, task: Any) -> dict[str, Any]:
    from apex.taskloop.request import PLAN_POLICIES, RETRY_ADAPTATIONS
    from apex.tasks.model import TaskKind
    from apex.tools.model import Permission

    options = dict(options or {})
    unknown = sorted(set(options) - set(ALLOWED_KEYS))
    if unknown:
        raise ValueError(f"option(s) d'exécution inconnue(s) : {unknown}")
    out: dict[str, Any] = {}
    max_attempts = options.get("max_attempts", 3)
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= 10:
        raise ValueError("max_attempts doit être un entier dans [1, 10]")
    out["max_attempts"] = max_attempts
    for key in ("plan_first", "use_memory"):
        value = options.get(key, False)
        if not isinstance(value, bool):
            raise ValueError(f"{key} doit être un booléen")
        out[key] = value
    policy = options.get("plan_policy", "request")
    if policy not in PLAN_POLICIES:
        raise ValueError(f"plan_policy « {policy} » inconnue (attendu : {', '.join(PLAN_POLICIES)})")
    out["plan_policy"] = policy
    adaptation = options.get("retry_adaptation", "failure_facts")
    if adaptation not in RETRY_ADAPTATIONS:
        raise ValueError(f"retry_adaptation « {adaptation} » inconnue (attendu : {', '.join(RETRY_ADAPTATIONS)})")
    out["retry_adaptation"] = adaptation
    permissions = options.get("tool_permissions", [])
    known = {p.value for p in Permission}
    if not isinstance(permissions, list) or any(p not in known for p in permissions):
        raise ValueError(f"tool_permissions doit lister des permissions connues ({sorted(known)})")
    out["tool_permissions"] = sorted(set(permissions))
    steps = options.get("max_tool_steps", 8)
    if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= 30:
        raise ValueError("max_tool_steps doit être un entier dans [1, 30]")
    out["max_tool_steps"] = steps
    if task.effective_kind is TaskKind.PROJECT and "workspace.write" not in out["tool_permissions"]:
        raise ValueError("une tâche « projet » exige la permission explicite « workspace.write »")
    return out

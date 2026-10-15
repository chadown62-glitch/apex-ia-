"""Small, versioned recipe; execution states remain APEX's existing states."""

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ProjectStep:
    key: str
    task_id: str
    role: str
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.key):
            raise ValueError("invalid step key")
        if not self.task_id or not self.role:
            raise ValueError("task_id and role are required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_steps(steps: tuple[ProjectStep, ...]) -> None:
    if len(steps) != 2 or len({s.key for s in steps}) != 2 or len({s.task_id for s in steps}) != 2:
        raise ValueError("MVP requires two distinct steps and tasks")
    seen: set[str] = set()
    for step in steps:
        if any(dep not in seen for dep in step.depends_on):
            raise ValueError("steps must be ordered after existing dependencies")
        seen.add(step.key)
    if steps[1].depends_on != (steps[0].key,):
        raise ValueError("second step must depend on the first")

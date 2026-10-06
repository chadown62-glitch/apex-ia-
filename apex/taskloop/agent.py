"""Épisode OUTILLÉ d'une tentative du TaskLoop (06/10/2026) : ACTION -> OUTIL -> OBSERVATION -> DÉCISION.

Remplace, quand `TaskRequest.tool_permissions` est non vide, l'appel unique « énoncé -> code » d'une tentative :
le modèle travaille dans l'espace de travail de la TaskRun (fichiers, exécution, tests — `apex.tools`) puis
soumet `solution.py`, qui suit EXACTEMENT le chemin habituel (sandbox, Judge à cas cachés, Experience).

Protocole : à chaque tour, le modèle répond par UN objet JSON — `{"tool": ..., "arguments": {...}}` ou
`{"action": "submit"}`. Toute autre réponse est une observation d'erreur (jamais devinée). Le modèle ne voit que
ce que les outils lui rendent : l'espace de travail ne contient que ses propres fichiers ; les cas cachés et les
valeurs attendues n'y sont JAMAIS. Les permissions sont celles accordées par la requête, jamais plus.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from apex.models.model import GenerationRequest, GenerationResult
from apex.obs.status import Measurement
from apex.tools.engine import ToolContext, ToolEngine
from apex.tools.model import ToolCall

SOLUTION_FILE = "solution.py"
_MAX_OBSERVATION_CHARS = 3_000
_MAX_HISTORY_CHARS = 12_000

_INSTRUCTIONS = (
    "Tu résous la tâche ci-dessous en travaillant dans un espace de travail, avec des outils. À chaque tour, "
    "réponds par UN SEUL objet JSON, sans aucun autre texte :\n"
    '- pour utiliser un outil : {"tool": "<nom>", "arguments": {...}}\n'
    '- quand ta solution est prête dans le fichier solution.py : {"action": "submit"}\n'
    "Écris la fonction demandée dans solution.py, sans code exécuté au chargement du module. Tu peux écrire "
    "tes propres tests (par exemple tests/test_solution.py, avec unittest) et les lancer avec tests.run. Les "
    "tests qui jugeront ta solution sont cachés : tu ne les verras jamais.\n\nOutils disponibles :\n"
)
_PROJECT_INSTRUCTIONS = (
    "Tu fais évoluer le projet Python de ton espace de travail pour réaliser la tâche ci-dessous, avec des "
    "outils. À chaque tour, réponds par UN SEUL objet JSON, sans aucun autre texte :\n"
    '- pour utiliser un outil : {"tool": "<nom>", "arguments": {...}}\n'
    '- quand le projet est prêt : {"action": "submit"}\n'
    "Commence par inspecter le projet (project.inspect, fs.read). Tu peux lancer ses tests et en écrire "
    "d'autres. Les vérifications qui jugeront le projet sont cachées : tu ne les verras jamais.\n\n"
    "Outils disponibles :\n"
)
_MAX_PROJECT_TEXT_CHARS = 50_000


@dataclass
class Step:
    number: int
    action: dict[str, Any] | None
    observation: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        action = self.action or {}
        return {
            "step": self.number, "tool": action.get("tool"), "submit": action.get("action") == "submit",
            "ok": self.observation.get("ok"), "error": (self.observation.get("error") or {}).get("code"),
        }


@dataclass
class Episode:
    steps: list[Step] = field(default_factory=list)
    submitted: bool = False
    stop_reason: str = ""


def parse_action(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Premier objet JSON de la réponse (blocs ``` tolérés) ; (action, None) ou (None, raison du refus)."""
    start = text.find("{")
    if start < 0:
        return None, "aucun objet JSON dans la réponse"
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        return None, f"JSON illisible : {exc.msg}"
    if not isinstance(value, dict):
        return None, "la réponse doit être un objet JSON"
    if value.get("action") == "submit" and set(value) <= {"action"}:
        return value, None
    if isinstance(value.get("tool"), str) and isinstance(value.get("arguments", {}), dict) \
            and set(value) <= {"tool", "arguments"}:
        return {"tool": value["tool"], "arguments": value.get("arguments", {})}, None
    return None, 'attendu : {"tool": ..., "arguments": {...}} ou {"action": "submit"}'


def _clip(value: Any, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} caractères omis]"


def compose_prompt(task_prompt: str, tools: list[dict[str, Any]], steps: list[Step], *, project: bool = False) -> str:
    history: list[str] = []
    budget = _MAX_HISTORY_CHARS
    for step in reversed(steps):  # les plus récents d'abord, dans le budget
        entry = (f"[tour {step.number}] action : {_clip(step.action, 1000) if step.action else 'illisible'}\n"
                 f"observation : {_clip(step.observation, _MAX_OBSERVATION_CHARS)}")
        if len(entry) > budget:
            break
        history.append(entry)
        budget -= len(entry)
    tools_text = "\n".join(json.dumps(t, ensure_ascii=False) for t in tools)
    past = "\n\n".join(reversed(history)) if history else "(aucun tour pour l'instant)"
    instructions = _PROJECT_INSTRUCTIONS if project else _INSTRUCTIONS
    return (f"{instructions}{tools_text}\n\nTâche :\n{task_prompt}\n\nHistorique (du plus ancien au plus "
            f"récent) :\n{past}\n\nTon action suivante (un seul objet JSON) :")


def run_tool_episode(
    *, task_prompt: str, generate: Callable[[GenerationRequest], GenerationResult], endpoint: Any,
    temperature: float | None, timeout_s: float, engine: ToolEngine, context: ToolContext, max_steps: int,
    deadline_monotonic: float, cancel_requested: Callable[[], bool], requested_by: str,
    emit: Callable[[str, dict[str, Any]], None], project: bool = False,
) -> tuple[GenerationResult, Episode]:
    """Déroule l'épisode puis renvoie le CONTENU de solution.py comme résultat de génération (texte vide s'il
    n'existe pas : le Judge le jugera comme tel). Les erreurs du fournisseur de modèle remontent telles quelles
    (même traitement qu'une génération ordinaire)."""
    tools = [spec.describe() for spec in engine.registry.available_to(context.granted)]
    episode = Episode()
    started = time.perf_counter()
    model_used = ""
    for number in range(1, max_steps + 1):
        if cancel_requested():
            episode.stop_reason = "cancelled"
            break
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 1:
            episode.stop_reason = "wall_clock"
            break
        response = generate(GenerationRequest(
            endpoint=endpoint, prompt=compose_prompt(task_prompt, tools, episode.steps, project=project),
            temperature=temperature, timeout_s=min(timeout_s, remaining),
        ))
        model_used = response.model_used
        action, refusal = parse_action(response.text)
        if action is None:
            step = Step(number, None, {"ok": False, "error": {"code": "invalid_action", "message": refusal}})
        elif action.get("action") == "submit":
            step = Step(number, action, {"ok": True, "submitted": True})
            episode.steps.append(step)
            emit("agent.step", step.summary())
            episode.submitted = True
            episode.stop_reason = "submitted"
            break
        else:
            result = engine.execute(ToolCall(
                tool=action["tool"], arguments=action["arguments"], call_id=str(uuid.uuid4()),
                requested_by=requested_by,
            ), context)
            step = Step(number, action, {"ok": result.ok, "output": result.output,
                                         "error": result.error.to_dict() if result.error else None})
        episode.steps.append(step)
        emit("agent.step", step.summary())
    else:
        episode.stop_reason = "max_steps"

    code = _project_text(context.workspace) if project else _solution_text(context.workspace)
    return GenerationResult(
        text=code, model_used=model_used, tokens_prompt=Measurement.unknown(),
        tokens_completion=Measurement.unknown(), finish_reason=episode.stop_reason,
        elapsed_s=time.perf_counter() - started,
        facts={"tool_episode": {"steps": [s.summary() for s in episode.steps], "submitted": episode.submitted,
                                "stop_reason": episode.stop_reason,
                                "solution_file": None if project else SOLUTION_FILE,
                                "solution_present": bool(code)}},
    ), episode


def _solution_text(workspace: Any) -> str:
    try:
        return workspace.read_bytes(SOLUTION_FILE).decode("utf-8", errors="replace")
    except Exception:  # absente ou refusée : jamais un code inventé
        return ""


def _project_text(workspace: Any) -> str:
    """Trace du projet jugé (fichiers Python de l'espace, bornée) : provenance enregistrée dans l'EvalRun."""
    parts: list[str] = []
    size = 0
    for rel, _ in workspace.files():
        if not rel.endswith(".py"):
            continue
        text = workspace.read_bytes(rel).decode("utf-8", errors="replace")
        block = f"# --- {rel}\n{text}"
        if size + len(block) > _MAX_PROJECT_TEXT_CHARS:
            parts.append(f"# --- [projet tronqué à {_MAX_PROJECT_TEXT_CHARS} caractères]")
            break
        parts.append(block)
        size += len(block)
    return "\n".join(parts)

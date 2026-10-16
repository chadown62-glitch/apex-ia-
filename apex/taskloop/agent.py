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
import re
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
    "Écris la fonction demandée dans solution.py, sans code exécuté au chargement du module.{verify} "
    "Les tests qui jugeront ta solution sont cachés : tu ne les verras jamais.\n"
    "Exemple de premier tour : "
    '{"tool": "fs.write", "arguments": {"path": "solution.py", "content": "def f(x):\\n    return x\\n"}}\n'
    "Ne soumets qu'une fois solution.py écrit.\n\nOutils disponibles (rôle, puis exemple d'appel) :\n"
)
_PROJECT_INSTRUCTIONS = (
    "Tu fais évoluer le projet Python de ton espace de travail pour réaliser la tâche ci-dessous, avec des "
    "outils. À chaque tour, réponds par UN SEUL objet JSON, sans aucun autre texte :\n"
    '- pour utiliser un outil : {"tool": "<nom>", "arguments": {...}}\n'
    '- quand le projet est prêt : {"action": "submit"}\n'
    "Commence par inspecter le projet (project.inspect, fs.read). Tu peux lancer ses tests et en écrire "
    "d'autres. Les vérifications qui jugeront le projet sont cachées : tu ne les verras jamais.\n"
    'Exemple de premier tour : {"tool": "project.inspect", "arguments": {}}\n'
    "Ne soumets qu'après avoir modifié le projet.\n\nOutils disponibles (rôle, puis exemple d'appel) :\n"
)
_MAX_PROJECT_TEXT_CHARS = 50_000


_PREVIEW_CHARS = 240


@dataclass
class Step:
    number: int
    action: dict[str, Any] | None
    observation: dict[str, Any]
    #: Début de la réponse du modèle quand elle n'était pas une action lisible (diagnostic, borné).
    raw: str | None = None

    def summary(self) -> dict[str, Any]:
        """Trace persistée d'un tour : l'outil, ses arguments (aperçu borné : un chemin en entier, un contenu
        ou un code tronqué) et le résultat — de quoi savoir CE QUE le modèle a tenté, refus compris."""
        action = self.action or {}
        summary = {
            "step": self.number, "tool": action.get("tool"), "submit": action.get("action") == "submit",
            "ok": self.observation.get("ok"), "error": (self.observation.get("error") or {}).get("code"),
        }
        if action.get("arguments"):
            summary["args"] = _clip(action["arguments"], _PREVIEW_CHARS)
        if self.raw is not None:
            summary["raw"] = self.raw[:_PREVIEW_CHARS]
        # Issue d'une VÉRIFICATION (comptes seulement) : sans elle, la trace ne dit pas si le modèle a agi sur un
        # échec qu'il venait d'observer (banc d'outils, docs/VALIDATION_REELLE.md §6.2).
        output = self.observation.get("output") or {}
        if isinstance(output, dict):
            if "examples_failed" in output or output.get("imported") is False:
                summary["check"] = {"imported": output.get("imported"),
                                    "examples": len(output.get("examples") or []),
                                    "examples_failed": output.get("examples_failed")}
            elif "tests_run" in output:
                summary["check"] = {"tests_run": output.get("tests_run"), "status": output.get("status"),
                                    "failures": output.get("failures"), "errors": output.get("errors")}
        return summary


@dataclass
class Episode:
    steps: list[Step] = field(default_factory=list)
    submitted: bool = False
    stop_reason: str = ""


_TRIPLE_QUOTED = re.compile(r'("""|\'\'\')(.*?)\1', re.DOTALL)


def _json_from_triple_quotes(text: str) -> str:
    """Mesuré avec un vrai modèle 1.5B (`real-2`, projet `fix_mean`) : il écrit la valeur de « content » comme une
    chaîne Python entre TROIS guillemets au lieu d'une chaîne JSON échappée — trois écritures CORRECTES perdues
    comme « JSON illisible ». Le contenu entre triples guillemets est réécrit en chaîne JSON équivalente (aucune
    autre réparation : une réponse qui reste illisible est refusée comme avant)."""
    return _TRIPLE_QUOTED.sub(lambda m: json.dumps(m.group(2).strip("\n")), text)


def parse_action(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Premier objet JSON de la réponse (blocs ``` et chaînes à triples guillemets tolérés) ; (action, None) ou
    (None, raison du refus)."""
    start = text.find("{")
    if start < 0:
        return None, "aucun objet JSON dans la réponse"
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        repaired = _json_from_triple_quotes(text[start:])
        try:
            value, _ = json.JSONDecoder().raw_decode(repaired)
        except json.JSONDecodeError:
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


#: À quoi sert chaque outil (lire = comprendre, lister/inspecter = découvrir, écrire = modifier, exécuter =
#: vérifier, tester = valider) et un exemple d'arguments : un modèle 1.5B ne déduisait pas l'usage des seuls schémas
#: JSON (1 800 caractères), et n'exécutait jamais rien.
_TOOL_GUIDE = {
    "fs.read": ("COMPRENDRE : lire un fichier", {"path": "chemin/fichier.py"}),
    "fs.list": ("DÉCOUVRIR : lister un répertoire", {"path": "."}),
    "project.inspect": ("DÉCOUVRIR : structure du projet (fichiers, tests, paquets)", {}),
    "fs.write": ("MODIFIER : écrire un fichier complet", {"path": "solution.py", "content": "def f(x):\n    return x\n"}),
    "fs.search": ("DÉCOUVRIR : chercher un texte dans le projet", {"query": "def f"}),
    "fs.edit": ("MODIFIER : remplacer un passage exact", {"path": "solution.py", "old": "return x", "new": "return x + 1"}),
    "solution.check": ("VÉRIFIER : exécuter solution.py sur les exemples publics de l'énoncé", {}),
    "code.run": ("VÉRIFIER : exécuter un fichier Python", {"path": "script.py"}),
    "tests.run": ("VALIDER : lancer les tests du projet", {"start_dir": "tests"}),
}
_FULL_STEPS = 3  # derniers tours rendus en entier ; les plus anciens en une ligne


def render_tools(tools: list[dict[str, Any]]) -> str:
    lines = []
    for tool in tools:
        purpose, example = _TOOL_GUIDE.get(tool["name"], (tool.get("description", ""), {}))
        lines.append(f'- {tool["name"]} — {purpose}. Exemple : '
                     f'{json.dumps({"tool": tool["name"], "arguments": example}, ensure_ascii=False)}')
    return "\n".join(lines)


def _one_line(step: Step) -> str:
    action = step.action or {}
    what = action.get("tool") or ("submit" if action.get("action") == "submit" else "illisible")
    target = (action.get("arguments") or {}).get("path", "")
    error = (step.observation.get("error") or {}).get("code")
    return f"[tour {step.number}] {what} {target} -> {'erreur ' + error if error else 'ok'}"


def compose_prompt(task_prompt: str, tools: list[dict[str, Any]], steps: list[Step], *, project: bool = False) -> str:
    history: list[str] = []
    budget = _MAX_HISTORY_CHARS
    for index, step in enumerate(reversed(steps)):  # les plus récents d'abord, dans le budget
        if index >= _FULL_STEPS:
            entry = _one_line(step)
        else:
            entry = (f"[tour {step.number}] action : {_clip(step.action, 1000) if step.action else 'illisible'}\n"
                     f"observation : {_clip(step.observation, _MAX_OBSERVATION_CHARS)}")
        if len(entry) > budget:
            break
        history.append(entry)
        budget -= len(entry)
    tools_text = render_tools(tools)
    past = "\n\n".join(reversed(history)) if history else "(aucun tour pour l'instant)"
    can_check = any(t["name"] == "solution.check" for t in tools)
    instructions = _PROJECT_INSTRUCTIONS if project else _INSTRUCTIONS.replace("{verify}", (
        " Puis VÉRIFIE-la (solution.check exécute solution.py sur les exemples publics de l'énoncé), corrige si "
        "besoin, et soumets." if can_check else ""))
    # L'énoncé d'une tâche « fonction » finit par « Réponds UNIQUEMENT avec le code Python » : en mode outillé,
    # cette consigne contredit « un seul objet JSON ». Elle est traduite, jamais retirée de l'énoncé.
    format_note = ("" if project else "\n(En mode outillé, « réponds avec le code » signifie : écris ce code dans "
                   "solution.py avec fs.write. Chacune de tes réponses reste UN objet JSON.)")
    return (f"{instructions}{tools_text}\n\nTâche :\n{task_prompt}{format_note}\n\nHistorique (du plus ancien au "
            f"plus récent) :\n{past}\n\nTon action suivante (un seul objet JSON) :")


_MAX_REPEATS = 4


def _repetitions(previous: list[Step], step: Step) -> int:
    """Combien de fois de suite (celle-ci comprise) la MÊME action a échoué avec la MÊME erreur. Mesuré : 10
    lectures d'un chemin absent, 9 soumissions refusées d'affilée — du temps de calcul sans information."""
    def key(s: Step) -> tuple[str, str | None] | None:
        if s.observation.get("ok"):
            return None
        return (json.dumps(s.action, sort_keys=True), (s.observation.get("error") or {}).get("code"))

    current = key(step)
    if current is None:
        return 0
    count = 1
    for earlier in reversed(previous):
        if key(earlier) != current:
            break
        count += 1
    return count


def _has_worked(episode: Episode, *, project: bool, context: ToolContext) -> bool:
    """Une soumission a-t-elle quelque chose à soumettre ? Fonction : solution.py existe. Projet : au moins
    une écriture réussie dans l'espace de travail pendant l'épisode."""
    if not project:
        try:
            return context.workspace.resolve(SOLUTION_FILE).is_file()
        except Exception:
            return False
    return any(s.action and s.action.get("tool") == "fs.write" and s.observation.get("ok") for s in episode.steps)


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
    checkable = any(t["name"] == "solution.check" for t in tools)
    episode = Episode()
    started = time.perf_counter()
    model_used = ""
    tokens_prompt: int | None = 0
    tokens_completion: int | None = 0
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
        # Somme RÉELLE des tokens de l'épisode, seulement si chaque tour les a réellement mesurés.
        for attr in ("tokens_prompt", "tokens_completion"):
            measured = getattr(response, attr)
            current = tokens_prompt if attr == "tokens_prompt" else tokens_completion
            value = measured.value if measured.status.value == "real" and current is not None else None
            total = None if value is None else current + int(value)
            if attr == "tokens_prompt":
                tokens_prompt = total
            else:
                tokens_completion = total
        action, refusal = parse_action(response.text)
        if action is None:
            step = Step(number, None, {"ok": False, "error": {"code": "invalid_action", "message": refusal}},
                        raw=response.text)
        elif action.get("action") == "submit" and not _has_worked(episode, project=project, context=context):
            # Soumettre sans avoir rien produit ne peut rien prouver : refus OBSERVÉ (le modèle le lit au
            # tour suivant), jamais une donnée cachée — seulement l'état de son propre espace de travail.
            # Mesuré (`real-2`) : jusqu'à 9 soumissions d'affilée sur ce seul refus ; il dit donc QUOI faire.
            step = Step(number, action, {"ok": False, "error": {
                "code": "nothing_to_submit",
                "message": ("solution.py n'existe pas encore. Ta prochaine action doit être une écriture : "
                            '{"tool": "fs.write", "arguments": {"path": "solution.py", "content": "<ton code>"}}'
                            if not project else
                            "aucune modification du projet. Lis le fichier à corriger (fs.read) puis réécris-le "
                            '(fs.write) : {"tool": "fs.write", "arguments": {"path": "<fichier>", '
                            '"content": "<contenu complet corrigé>"}}')}})
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
            if (result.ok and action["tool"] == "fs.write" and not project and checkable
                    and str(action["arguments"].get("path", "")).lstrip("./") == SOLUTION_FILE):
                step.observation["next"] = ("solution.py écrit. Vérifie-le avant de soumettre : "
                                            '{"tool": "solution.check", "arguments": {}}')
        repeated = _repetitions(episode.steps, step)
        if repeated >= 2:
            step.observation["warning"] = (f"action identique à la précédente, refusée {repeated} fois de suite avec "
                                           "le même résultat : change d'action")
        episode.steps.append(step)
        emit("agent.step", step.summary())
        if repeated >= _MAX_REPEATS:
            episode.stop_reason = "repetition"
            break
    else:
        episode.stop_reason = "max_steps"

    code = _project_text(context.workspace) if project else _solution_text(context.workspace)
    return GenerationResult(
        text=code, model_used=model_used,
        tokens_prompt=Measurement.real(tokens_prompt) if tokens_prompt and episode.steps else Measurement.unknown(),
        tokens_completion=(Measurement.real(tokens_completion) if tokens_completion and episode.steps
                           else Measurement.unknown()),
        finish_reason=episode.stop_reason,
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

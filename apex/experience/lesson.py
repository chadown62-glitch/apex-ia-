"""Ce qu'une Expérience permet de RÉPONDRE pour une décision future (08/10/2026, docs/VALIDATION_REELLE.md §6.3).

Exigence : quelle tâche, quel contexte, quelle stratégie, quel modèle, quels outils, quelle erreur, quelle
observation, quelle cause probable, quelle modification, quel résultat, quel score, quelle stratégie préférer
ensuite. Le schéma n'est PAS élargi : `experience_card` lit les colonnes existantes et `generation_config`
(où le cycle `diagnose_repair` range observation, diagnostic et stratégie), plus la trace d'outils de l'EvalRun.
Un champ que les données ne permettent pas de remplir vaut `None` et figure dans `missing` — jamais inventé.

`run_lesson` résume une TaskRun en UNE leçon réutilisable (cause identifiée -> stratégie qui a marché, ou
causes non résolues), écrite dans son souvenir d'exécution (`apex.memory.indexing.index_task_run`) : c'est le
chemin EXPÉRIENCE -> MÉMOIRE -> DÉCISION FUTURE.
"""

from __future__ import annotations

from typing import Any

FIELDS = ("task", "context", "strategy", "model", "tools", "error", "observation", "likely_cause",
          "modification", "result", "score", "next_strategy")
_MAX_CAUSE_CHARS = 300


def _strategy_mode(config: dict[str, Any]) -> str:
    repair = config.get("repair") or {}
    if repair.get("mode"):
        return str(repair["mode"])
    if config.get("adaptation") == "in_run" or (config.get("generation") or {}).get("prompt_suffix"):
        return "failure_facts"
    return "initial"


def _tools(eval_run: Any) -> dict[str, Any] | None:
    episode = (getattr(eval_run, "facts", None) or {}).get("tool_episode") if eval_run is not None else None
    if not episode:
        return None
    calls: dict[str, int] = {}
    for step in episode.get("steps") or []:
        if step.get("tool"):
            calls[step["tool"]] = calls.get(step["tool"], 0) + 1
    return {"calls": calls, "steps": len(episode.get("steps") or []), "stop": episode.get("stop_reason")}


def next_strategy(experience: Any) -> str:
    """Stratégie à PRÉFÉRER ensuite, selon une règle explicite (celle d'`apex.taskloop.repair.next_strategy` pour
    le cycle de réparation) : une réussite se réutilise ; un échec jugé appelle un diagnostic puis une réparation
    de SON code ; une réparation sans progrès appelle une réécriture ; une panne d'infrastructure ne dit rien de
    la stratégie (réessayer à l'identique)."""
    category = getattr(experience.failure_category, "value", str(experience.failure_category))
    mode = _strategy_mode(experience.generation_config or {})
    if category == "success":
        return f"réutiliser « {mode} »"
    if category not in ("incorrect_solution", "execution_failure", "invalid_candidate", "timeout"):
        return "réessayer à l'identique (échec non imputable au code)"
    if mode == "repair" and not (experience.improvement_delta or 0) > 0:
        return "réécrire (rewrite) : la réparation n'a pas progressé"
    return "diagnostiquer puis réparer son code (repair)"


def experience_card(experience: Any, *, eval_run: Any = None, task_title: str | None = None) -> dict[str, Any]:
    config = dict(experience.generation_config or {})
    observation = config.get("observation")
    if observation is None and config.get("failure_facts"):
        observation = {"failure_facts": config["failure_facts"]}
    card = {
        "task": {"id": experience.task_id, "title": task_title},
        "context": {"memory": config.get("memory"), "plan": bool(config.get("plan")),
                    "adaptation": config.get("adaptation"), "parent_experience_id": experience.parent_experience_id},
        "strategy": {"id": experience.strategy_id, "mode": _strategy_mode(config),
                     "temperature": (config.get("generation") or {}).get("temperature", config.get("temperature"))},
        "model": experience.model_id,
        "tools": _tools(eval_run),
        "error": None if experience.failure_category.value == "success" else experience.failure_category.value,
        "observation": observation,
        "likely_cause": (config.get("diagnosis") or None),
        "modification": experience.mutation_description or ((config.get("repair") or {}).get("mode")),
        "result": getattr(experience.oracle_verdict, "value", str(experience.oracle_verdict)),
        "score": experience.fitness,
        "improvement_delta": experience.improvement_delta,
        "next_strategy": next_strategy(experience),
    }
    # « Manquant » = la donnée n'existe pas pour cette expérience (ex. pas de diagnostic hors cycle de
    # réparation, pas d'outils sans épisode outillé) ; `error` vide d'une réussite n'est pas un manque.
    card["missing"] = [f for f in FIELDS if card.get(f) is None and not (f == "error" and card["result"] == "pass")]
    return card


def run_lesson(experiences: list[Any]) -> str | None:
    """Leçon d'une TaskRun (expériences dans l'ordre des tentatives) — seulement si un DIAGNOSTIC a été produit :
    une réussite directe n'enseigne rien de plus que son code, un échec sans cause identifiée non plus."""
    ordered = sorted(experiences, key=lambda e: (e.created_at, e.id))
    causes = [str((e.generation_config or {}).get("diagnosis") or "").strip() for e in ordered]
    causes = [c[:_MAX_CAUSE_CHARS] for c in causes if c]
    if not ordered or not causes:
        return None
    first, last = ordered[0], ordered[-1]
    first_error = first.failure_category.value
    if last.failure_category.value == "success":
        return (f"la 1re tentative a échoué ({first_error}) ; cause identifiée : {causes[-1]} ; corrigée par la "
                f"stratégie « {_strategy_mode(last.generation_config or {})} » (score {first.fitness:.2f} -> "
                f"{last.fitness:.2f}).")
    unique = list(dict.fromkeys(causes))
    return (f"non résolue après {len(ordered)} tentatives ({last.failure_category.value}) ; causes identifiées : "
            + " | ".join(unique[-3:]) + ".")

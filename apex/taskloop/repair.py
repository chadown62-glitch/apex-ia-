"""Adaptation « diagnostic -> changement de stratégie » entre deux tentatives (`retry_adaptation =
"diagnose_repair"`, 08/10/2026).

Constat mesuré (docs/VALIDATION_REELLE.md §6) : la tentative suivante ne recevait que « cas en échec : 3 sur 8 »,
SANS son propre code — le modèle ne pouvait pas réparer, seulement ré-échantillonner (C ≈ R). Ici le cycle est
explicite et tracé :

    ÉCHEC -> OBSERVATION -> DIAGNOSTIC (cause probable, par le modèle) -> CHANGEMENT DE STRATÉGIE -> RETRY

- OBSERVATION : uniquement ce qui est légitimement observable — le code de la tentative (produit par le modèle
  lui-même), les faits d'échec autorisés (`apex.experience.failure_facts` : comptes, classes d'exception natives)
  et les exemples PUBLICS de l'énoncé exécutés en sandbox (`apex.tasks.public_examples`). Jamais une entrée ni
  une réponse attendue des cas cachés.
- DIAGNOSTIC : un appel court au modèle (« la cause la plus probable »), journalisé tel quel, borné, sans code.
- STRATÉGIE : `initial` -> `repair` (corriger SON code à la lumière du diagnostic). Après un `repair` sans
  progrès (pas moins de cas en échec), `rewrite` : nouvelle solution, code précédent NON montré, causes déjà
  identifiées rappelées, température relevée. Après un `rewrite` en échec : `repair` du nouveau code.
  La stratégie après est TOUJOURS différente de la stratégie avant (vérifié, et dit dans l'événement) ; que le
  CODE ait réellement changé est mesuré à la tentative suivante (`strategy.outcome`).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

REPAIR_VERSION = "l-repair-1"
_MAX_CODE_CHARS = 4_000
_MAX_DIAGNOSIS_CHARS = 500
_DIAGNOSIS_MAX_TOKENS = 120
_REWRITE_TEMPERATURE_BUMP = 0.3
_MAX_TEMPERATURE = 1.2
_DEFAULT_TEMPERATURE = 0.7
_CODE_BLOCK = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)


@dataclass(frozen=True)
class Observation:
    previous_code: str
    facts_text: str | None
    failed_cases: int | None
    total_cases: int | None
    examples: list[dict[str, Any]] | None  # None : aucun exemple public ou exécution impossible
    examples_available: bool

    def summary(self) -> dict[str, Any]:
        failed_examples = None if self.examples is None else sum(not e["ok"] for e in self.examples)
        return {"failed_cases": self.failed_cases, "total_cases": self.total_cases,
                "public_examples": len(self.examples) if self.examples is not None else 0,
                "public_examples_failed": failed_examples, "code_chars": len(self.previous_code)}


@dataclass(frozen=True)
class RepairStrategy:
    mode: str  # "initial" | "repair" | "rewrite"
    temperature: float | None
    diagnoses: tuple[str, ...] = ()
    base_code_fingerprint: str | None = None
    version: str = REPAIR_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "temperature": self.temperature, "diagnoses": list(self.diagnoses),
                "base_code_fingerprint": self.base_code_fingerprint, "version": self.version}


def fingerprint(code: str) -> str:
    import hashlib

    return hashlib.sha256(" ".join(code.split()).encode("utf-8")).hexdigest()[:16]


def observe(*, code: str, eval_run: Any, failure_category: Any, description: str,
            run_script: Callable[[str], Any] | None) -> Observation:
    """Ce que la tentative échouée permet d'observer, sans rien de caché."""
    from apex.experience.failure_facts import extract_failure_facts, render_failure_facts
    from apex.tasks.public_examples import examples_script, extract_examples, read_results

    facts = extract_failure_facts(eval_run, failure_category) if eval_run is not None else None
    examples = extract_examples(description)
    results = None
    if examples and run_script is not None and code.strip():
        nonce = uuid.uuid4().hex[:12]
        try:
            result = run_script(examples_script(code, examples, nonce))
            results = read_results(getattr(result, "stdout", "") or "", examples, nonce)
        except Exception:  # l'observation est un bonus : jamais un échec de la boucle
            results = None
    return Observation(
        previous_code=code[:_MAX_CODE_CHARS],
        facts_text=render_failure_facts(facts) if facts is not None else None,
        failed_cases=facts.failed_cases if facts is not None else None,
        total_cases=facts.total_cases if facts is not None else None,
        examples=results, examples_available=bool(examples),
    )


def _observation_text(obs: Observation) -> str:
    from apex.tasks.public_examples import render_results

    parts = []
    if obs.facts_text:
        parts.append(obs.facts_text)
    if obs.examples_available:
        parts.append(render_results(obs.examples))
    return "\n".join(parts) or "échec jugé par des tests cachés (aucun autre détail disponible)"


def diagnosis_prompt(statement: str, obs: Observation) -> str:
    return (
        f"Énoncé de la tâche :\n{statement}\n\n"
        f"Ton code (tentative précédente, jugée INCORRECTE) :\n```python\n{obs.previous_code}\n```\n\n"
        f"Ce qui a été observé :\n{_observation_text(obs)}\n\n"
        "En deux phrases au plus, quelle est la cause la plus probable de l'échec (quel point de l'énoncé le code "
        "ne respecte pas) ? Réponds seulement par cette cause, sans écrire de code."
    )


def clean_diagnosis(text: str) -> str:
    text = _CODE_BLOCK.sub("", text or "").strip()
    return " ".join(text.split())[:_MAX_DIAGNOSIS_CHARS]


def next_strategy(before: RepairStrategy, *, obs: Observation, previous_failed: int | None,
                  diagnosis: str, base_temperature: float | None) -> RepairStrategy:
    """Règle explicite : `initial`/`rewrite` -> `repair` ; `repair` -> `repair` s'il y a eu progrès (moins de cas
    en échec qu'avant), sinon `rewrite`."""
    diagnoses = before.diagnoses + ((diagnosis,) if diagnosis else ())
    progress = (before.mode == "repair" and previous_failed is not None and obs.failed_cases is not None
                and obs.failed_cases < previous_failed)
    if before.mode == "repair" and not progress:
        temp = (before.temperature if before.temperature is not None else
                base_temperature if base_temperature is not None else _DEFAULT_TEMPERATURE)
        return RepairStrategy(mode="rewrite", temperature=min(_MAX_TEMPERATURE, temp + _REWRITE_TEMPERATURE_BUMP),
                              diagnoses=diagnoses, base_code_fingerprint=None)
    return RepairStrategy(mode="repair", temperature=base_temperature, diagnoses=diagnoses,
                          base_code_fingerprint=fingerprint(obs.previous_code),
                          extra={"progress": progress})


def exploration_temperature(next_attempt: int, base_temperature: float | None, *, current: float | None) -> float | None:
    """Calendrier de température du TÉMOIN `explore`, calqué sur `diagnose_repair` à 3 tentatives : la 2e garde la
    température courante (comme `repair`), la 3e et les suivantes prennent celle d'une réécriture (`rewrite` :
    base, ou 0,7 par défaut, + 0,3, plafonnée)."""
    if next_attempt < 3:
        return current
    base = base_temperature if base_temperature is not None else _DEFAULT_TEMPERATURE
    return min(_MAX_TEMPERATURE, base + _REWRITE_TEMPERATURE_BUMP)


def strategy_suffix(strategy: RepairStrategy, obs: Observation) -> str:
    """Ce que la tentative suivante reçoit EN PLUS de l'énoncé (jamais une donnée cachée)."""
    if strategy.mode == "repair":
        diagnosis = strategy.diagnoses[-1] if strategy.diagnoses else "(aucun diagnostic produit)"
        return (
            "Ta tentative précédente a été jugée INCORRECTE.\n"
            f"Ton code précédent :\n```python\n{obs.previous_code}\n```\n"
            f"Observations :\n{_observation_text(obs)}\n"
            f"Cause probable (ton propre diagnostic) : {diagnosis}\n"
            "Corrige ce code : écris la version corrigée COMPLÈTE de la fonction. Réponds UNIQUEMENT avec le "
            "code Python."
        )
    causes = "\n".join(f"- {d}" for d in strategy.diagnoses[-3:]) or "- (aucune cause identifiée)"
    return (
        "Tes tentatives précédentes ont été jugées INCORRECTES, et corriger le code n'a pas suffi. Causes déjà "
        f"identifiées :\n{causes}\n"
        "Écris une NOUVELLE solution, par une approche différente, en respectant chaque point de l'énoncé. Réponds "
        "UNIQUEMENT avec le code Python."
    )

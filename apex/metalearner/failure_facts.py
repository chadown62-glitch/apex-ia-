"""Faits structurés d'un échec, transmis au bras C du protocole v2 (docs/PROTOCOLE_V2.md).

Liste FERMÉE, figée avant toute mesure : catégorie d'échec ; nombre de cas en échec / total ; nombre et noms
des classes d'exception (natives de Python seulement : une classe définie par le candidat devient « classe non
standard ») ; terminaison normale du script ; code de sortie. Tout le reste est interdit et ne peut
pas sortir de ce module : entrées cachées, réponses attendues, indices des cas, MESSAGES d'exception (ils
peuvent contenir une entrée cachée, ex. `KeyError('…')`), valeurs publiées par le candidat, code généré.

Les faits sont rendus comme des OBSERVATIONS (aucune consigne, aucune correction suggérée) par un gabarit
versionné (`PROMPT_VERSION`) : l'effet éventuel du bras C ne peut venir que de l'information.
"""

from __future__ import annotations

import builtins
import re
from dataclasses import asdict, dataclass
from typing import Any

from apex.evalrunner.model import EvalRun
from apex.metalearner.failure_taxonomy import FailureCategory
from apex.oracles.result_contract import read_published_results
from apex.sandbox.model import ExecutionStatus

PROMPT_VERSION = "v2-facts-1"

#: Catégories pour lesquelles le bras C s'applique, et leur libellé figé.
CATEGORY_LABELS: dict[FailureCategory, str] = {
    FailureCategory.INCORRECT_SOLUTION: "résultats incorrects",
    FailureCategory.INVALID_CANDIDATE: "exception ou résultat illisible",
}

ALLOWED_FIELDS = (
    "failure_category",
    "failed_cases",
    "total_cases",
    "exception_class_count",
    "exception_classes",
    "script_terminated",
    "exit_code",
)

_DETAIL_COUNTS = re.compile(r"^(\d+)/(\d+) cas incorrects")
#: Classes d'exception natives (`builtins`) : les seuls noms transmis tels quels.
_NATIVE_EXCEPTIONS = frozenset(
    n for n, o in vars(builtins).items() if isinstance(o, type) and issubclass(o, BaseException)
)
NON_STANDARD_CLASS = "classe non standard"
_MAX_CLASSES = 5


@dataclass(frozen=True)
class FailureFacts:
    failure_category: str
    failed_cases: int | None
    total_cases: int | None
    exception_class_count: int
    exception_classes: tuple[str, ...]
    script_terminated: bool
    exit_code: int | None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["exception_classes"] = list(self.exception_classes)
        return data


def _exception_class(entry: Any) -> str | None:
    """Nom de classe BRUT, depuis `{"error": repr(exc)}` publié par le harnais — jamais le message. Ne sort
    jamais tel quel de ce module : voir `_public_class_name`."""
    if not isinstance(entry, dict) or not isinstance(entry.get("error"), str):
        return None
    return entry["error"].split("(", 1)[0].strip()


def _public_class_name(name: str) -> str:
    """Seules les classes d'exception NATIVES de Python sont nommées. Une classe définie par le candidat porte
    un nom écrit par le modèle (qui pourrait même être une entrée cachée : `type(s, (Exception,), {})`) :
    elle devient le libellé figé `NON_STANDARD_CLASS`."""
    return name if name in _NATIVE_EXCEPTIONS else NON_STANDARD_CLASS


def extract_failure_facts(eval_run: EvalRun, failure_category: FailureCategory) -> FailureFacts | None:
    """Faits autorisés d'une tentative ÉCHOUÉE ; `None` si la catégorie n'est pas couverte par le bras C
    (succès, infrastructure, génération, ...)."""
    if failure_category not in CATEGORY_LABELS:
        return None
    failed = total = None
    if m := _DETAIL_COUNTS.match(eval_run.oracle_detail or ""):
        failed, total = int(m.group(1)), int(m.group(2))
    execution = (eval_run.facts or {}).get("candidate_execution") or {}
    stdout = execution.get("stdout") if isinstance(execution, dict) else None
    classes: set[str] = set()
    if isinstance(stdout, str):
        published = read_published_results(stdout)
        for entry in published.results or []:
            if name := _exception_class(entry):
                classes.add(name)
    names = tuple(sorted({_public_class_name(n) for n in classes}))
    return FailureFacts(
        failure_category=failure_category.value,
        failed_cases=failed,
        total_cases=total,
        exception_class_count=len(classes),
        exception_classes=names[:_MAX_CLASSES],
        script_terminated=eval_run.sandbox_status is ExecutionStatus.SUCCESS,
        exit_code=eval_run.exit_code,
    )


def render_failure_facts(facts: FailureFacts) -> str:
    """Gabarit `PROMPT_VERSION`, figé : des observations, jamais une consigne."""
    label = CATEGORY_LABELS[FailureCategory(facts.failure_category)]
    cases = (
        f"{facts.failed_cases} sur {facts.total_cases}"
        if facts.failed_cases is not None and facts.total_cases is not None
        else "non disponible"
    )
    classes = f" ({', '.join(facts.exception_classes)})" if facts.exception_classes else ""
    exit_code = str(facts.exit_code) if facts.exit_code is not None else "non disponible"
    return (
        "Observations factuelles sur une tentative précédente à cette même tâche (jugée par des tests "
        f"cachés) : catégorie d'échec : {label} ; cas en échec : {cases} ; classes d'exception levées : "
        f"{facts.exception_class_count}{classes} ; script terminé normalement : "
        f"{'oui' if facts.script_terminated else 'non'} ; code de sortie : {exit_code}."
    )

"""Construction du script sandboxé d'une tentative de solution générée par un
`ModelProvider` — passe « Generated-Solution Evaluation Loop » (N+19).

Une `Task` portant `solution_harness` (`apex.tasks.model.Task`) fournit un
template de script Python contenant EXACTEMENT une occurrence du marqueur
`CANDIDATE_CODE_MARKER`. `build_candidate_command()` substitue ce marqueur
par le code RÉELLEMENT généré (`GenerationResult.text`, jamais un contenu
inventé) et retourne un argv `("python3", "-c", <script complet>)` — même
contrat que `apex.sandbox.model.SandboxSpec.command` (un argv, jamais une
chaîne shell interprétée, jamais `eval()`/`exec()` ici : le script complet
est exécuté par un VRAI sous-processus `python3` dans le sandbox namespaced
déjà réel, exactement comme tout script produit par
`apex.taskgen.deterministic.DeterministicTaskGenerator`).

Le harness porte typiquement, APRÈS le marqueur, des tests cachés (jamais
montrés au modèle — seule `Task.description` l'est) qui font échouer le
script (`sys.exit(1)`) si la solution générée est incorrecte — c'est cette
sortie que `apex.oracles.exit_code.ExitCodeOracle` juge, exactement comme un
script déterministe existant. Cette substitution reste une simple
concaténation de texte AVANT le fork/exec réel — jamais une exécution du
code candidat dans CE processus (aucun `eval()`/`exec()` Python, aucune
interprétation avant le sandbox).

Avant substitution, `extract_candidate_code()` isole le code d'un bloc
markdown ``` si le modèle en a produit un (constat réel : un modèle qui
répond ``` ```python\n...\n``` ``` malgré la consigne du prompt de ne
renvoyer que du code produisait, avant ce correctif, un `SyntaxError`
immédiat sur les balises elles-mêmes — indiscernable, côté
`ExitCodeOracle`, d'un vrai échec de solution). `GenerationResult.text`
brut reste par ailleurs persisté tel quel dans `EvalRun.facts` — seule la
commande RÉELLEMENT exécutée passe par cette extraction."""

from __future__ import annotations

import re

CANDIDATE_CODE_MARKER = "# APEX_CANDIDATE_CODE_HERE"

#: Un seul bloc ``` ... ``` (avec ou sans identifiant de langage, ex.
#: ```python) — le cas empiriquement observé et non ambigu. Aucune
#: heuristique de nettoyage de prose au-delà de ça : trop de faux positifs
#: possibles sur du code valide qui ressemblerait, par ses commentaires, à
#: du texte explicatif.
_FENCE_PATTERN = re.compile(r"```[ \t]*[a-zA-Z0-9_+\-]*[ \t]*\r?\n(.*?)```", re.DOTALL)


def extract_candidate_code(text: str) -> str:
    """Isole le contenu du premier bloc markdown ``` de `text`, s'il en
    existe un ; retourne `text` INCHANGÉ sinon (jamais de suppression de
    texte hors bloc quand aucune balise n'est présente — un modèle qui
    répond du code brut sans balises n'est jamais altéré par cette
    fonction)."""
    match = _FENCE_PATTERN.search(text)
    if match is None:
        return text
    return match.group(1)


#: Commande de contrat pour une `Task` portant `solution_harness` — jamais
#: réellement exécutée (`run_generated_solution` construit la vraie commande
#: dynamiquement) : un placeholder réel et inoffensif (`command` reste un
#: champ obligatoire de `Task`), documenté ici pour n'être défini qu'à UN
#: seul endroit.
PLACEHOLDER_COMMAND: tuple[str, ...] = ("python3", "-c", "pass")


class MissingCandidateMarkerError(ValueError):
    """`solution_harness` ne contient pas `CANDIDATE_CODE_MARKER` — jamais
    silencieusement ignoré : une tâche mal formée doit échouer tôt, avant
    tout appel réseau à un `ModelProvider` (jamais un gaspillage de tentative
    de génération pour une tâche qui échouerait de toute façon)."""


def ensure_candidate_marker(solution_harness: str) -> None:
    """À appeler AVANT toute génération : une tâche mal formée ne doit
    jamais consommer un appel modèle."""
    if CANDIDATE_CODE_MARKER not in solution_harness:
        raise MissingCandidateMarkerError(
            f"« solution_harness » ne contient pas le marqueur « {CANDIDATE_CODE_MARKER} »"
        )


def build_candidate_command(*, solution_harness: str, candidate_code: str) -> tuple[str, ...]:
    """Substitue `CANDIDATE_CODE_MARKER` par `candidate_code` (après
    `extract_candidate_code()`, voir docstring du module) dans
    `solution_harness` et retourne l'argv réel à exécuter. Lève
    `MissingCandidateMarkerError` si le marqueur est absent — jamais un
    script partiellement construit exécuté quand même."""
    ensure_candidate_marker(solution_harness)
    script = solution_harness.replace(CANDIDATE_CODE_MARKER, extract_candidate_code(candidate_code), 1)
    return ("python3", "-c", script)

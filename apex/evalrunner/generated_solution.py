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
interprétation avant le sandbox)."""

from __future__ import annotations

CANDIDATE_CODE_MARKER = "# APEX_CANDIDATE_CODE_HERE"

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


def build_candidate_command(*, solution_harness: str, candidate_code: str) -> tuple[str, ...]:
    """Substitue `CANDIDATE_CODE_MARKER` par `candidate_code` dans
    `solution_harness` et retourne l'argv réel à exécuter. Lève
    `MissingCandidateMarkerError` si le marqueur est absent — jamais un
    script partiellement construit exécuté quand même."""
    if CANDIDATE_CODE_MARKER not in solution_harness:
        raise MissingCandidateMarkerError(
            f"« solution_harness » ne contient pas le marqueur « {CANDIDATE_CODE_MARKER} »"
        )
    script = solution_harness.replace(CANDIDATE_CODE_MARKER, candidate_code, 1)
    return ("python3", "-c", script)

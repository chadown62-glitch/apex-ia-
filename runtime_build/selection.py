"""Source unique de vérité : quels sous-paquets/fichiers de `apex/` sont
sélectionnés pour l'artefact APEX Runtime distribué, par opposition à ceux
qui restent exclusivement dans APEX DEV (recherche/auto-amélioration).

Cette liste est issue d'un audit d'imports réel (grep exhaustif, ancré, de
`from apex.X` / `import apex.X` dans tout `apex/`, dans les deux sens),
documenté dans le rapport « PRÉPARATION DU RUNTIME » — pas d'une
supposition architecturale. Elle ne modifie ni ne supprime rien dans
`apex/` : c'est une SÉLECTION pour la construction d'un artefact séparé
(voir `runtime_build/build.py`).
"""

from __future__ import annotations

from pathlib import Path

#: Sous-paquets de `apex/` (répertoires avec `__init__.py`) distribués dans
#: l'artefact Runtime.
#:
#: `artifacts` et `memory` y figurent par décision produit explicite, pas
#: par nécessité d'import actuelle : aucun module Runtime ne les importe
#: aujourd'hui (seul `apex.genome`, recherche, importe `apex.artifacts` ;
#: rien n'importe `apex.memory`). Statut documenté :
#: `runtime-capable / currently unused` — conservés pour leur potentiel
#: d'usage futur (stockage d'artefacts de mission, mémoire/RAG), jamais
#: activés implicitement par leur seule présence dans le paquet (aucune
#: route API, aucun câblage `missions`/`sandbox` ne les invoque — vérifié
#: par `tests/test_runtime_packaging.py::TestBuildRuntimeArtifact
#: ::test_artifacts_and_memory_packages_are_present_but_unwired_in_api`).
RUNTIME_PACKAGES: frozenset[str] = frozenset(
    {
        "agent_registry",
        "agent_templates",
        "agents",
        "api",
        "artifacts",
        "cli",
        "config",
        "conversations",
        "db",
        "design",
        "desktop",
        "doctor",
        "gate",
        "interfaces",
        "memory",
        "missions",
        "models",
        "obs",
        "operator",
        "policy",
        "projects",
        "recovery",
        "routing",
        "sandbox",
        "scheduler",
        "secrets",
        "specialties",
        "util",
    }
)

#: Fichiers Python de premier niveau de `apex/` (hors sous-paquets) requis
#: par le Runtime — tous, il n'y en a que trois et ils sont le noyau
#: (`apex/__init__.py`, `apex/errors.py`, `apex/version.py`).
RUNTIME_FILES: frozenset[str] = frozenset({"__init__.py", "errors.py", "version.py"})

#: Sous-paquets de `apex/` qui restent exclusivement dans APEX DEV — jamais
#: présents dans l'artefact Runtime. Confirmé par grep exhaustif : aucun
#: module de RUNTIME_PACKAGES n'importe l'un de ceux-ci. Le sens inverse
#: (un module recherche important un module Runtime, ex. `skills` importe
#: `missions`) est observé et autorisé — il n'ajoute rien au Runtime.
RESEARCH_PACKAGES: frozenset[str] = frozenset(
    {
        "calibration",
        "capabilities",
        "costgain",
        "curriculum",
        "evalrunner",
        "genome",
        "hypothesis",
        "improvement",
        "judge",
        "knowledge",
        "metalearner",
        "neural",
        "oracles",
        "selfmodel",
        "skills",
        "stattest",
        "taskgen",
        "taskloop",
        "tasks",
        "weakness",
    }
)


def verify_partition_matches_repo(apex_dir: Path) -> None:
    """Garde de non-régression : lève `ValueError` si un sous-paquet de
    `apex/` n'est classé ni Runtime ni Research (nouveau module ajouté et
    oublié ici), ou si un sous-paquet est classé dans les deux à la fois.

    Appelée à la fois par les tests (contre le dépôt de travail réel) et
    par `build.py` (contre l'arborescence extraite du commit à construire)
    — un désaccord entre le commit ciblé et cette liste doit faire échouer
    la construction plutôt que produire silencieusement un artefact
    incomplet ou trop large.
    """
    actual = {
        p.name
        for p in apex_dir.iterdir()
        if p.is_dir() and not p.name.startswith("__") and (p / "__init__.py").is_file()
    }
    overlap = RUNTIME_PACKAGES & RESEARCH_PACKAGES
    if overlap:
        raise ValueError(f"sous-paquets classés à la fois Runtime et Research : {sorted(overlap)}")

    known = RUNTIME_PACKAGES | RESEARCH_PACKAGES
    unclassified = actual - known
    if unclassified:
        raise ValueError(
            f"sous-paquet(s) de apex/ non classé(s) Runtime ni Research : {sorted(unclassified)} "
            "— mettre à jour runtime_build/selection.py avant de construire."
        )

    missing = RUNTIME_PACKAGES - actual
    if missing:
        raise ValueError(
            f"sous-paquet(s) Runtime attendu(s) absent(s) de apex/ : {sorted(missing)}"
        )

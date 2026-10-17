"""Registre backend des Specialty — source de vérité serveur.

Miroir volontairement PARTIEL du registre frontend
(`frontend/src/specialties/data.ts`) : les deux listent les mêmes
identifiants de Specialty pour que le frontend et l'API se comprennent, mais
seul CE module décide ce qui est réellement `is_supported` côté serveur —
le frontend ne fait qu'afficher REAL/PLANNED, jamais l'inverse.

Research dispose du moteur de clones AgentRuntime. Game Graphics dispose
uniquement de missions créatives via GameStudioProjectService : aucun provider
TaskGen ni moteur d'entraînement de clones n'est annoncé pour cette spécialité.
Les autres entrées restent PLANNED tant qu'aucun moteur réel ne les exécute.
"""

from __future__ import annotations

from apex.interfaces.registry import ImplStatus
from apex.policy.model import Subject
from apex.specialties.model import Specialty

SPECIALTIES: tuple[Specialty, ...] = (
    Specialty(
        id="research",
        name="Research",
        description="Missions d'exploration confiées à des clones RESEARCH.",
        dev_stage=ImplStatus.STUB,
        capabilities=("recherche documentaire", "formulation d'hypothèses"),
        tools=(),
        required_permissions=("validation humaine avant COMPLETED",),
        compatible_agent_types=(Subject.RESEARCH,),
    ),
    Specialty(
        id="game-graphics",
        name="Game Graphics",
        description=(
            "Spécialiste composé : contrôleur Qwen, outils Blender, contrats et juges techniques. "
            "Aucun nouveau checkpoint entraîné."
        ),
        dev_stage=ImplStatus.STUB,
        capabilities=(
            "coupé Blender",
            "bâtiment générique Blender",
            "fauteuil Blender",
            "planification multi-assets bornée",
            "juges techniques et corrections versionnées",
        ),
        tools=(
            "blender",
            "game_studio.create",
            "game_studio.status",
            "game_studio.cancel",
            "game_studio.results",
        ),
        required_permissions=(
            "game_studio.create",
            "game_studio.read",
            "game_studio.cancel",
            "workspace propriétaire",
            "validation humaine avant COMPLETED",
        ),
    ),
    Specialty(
        id="code-intelligence",
        name="Code Intelligence",
        description="Analyse, revue et génération de code par clones DEVELOPER/CRITIC.",
        dev_stage=ImplStatus.PLANNED,
        capabilities=("analyse statique", "revue de diff"),
        tools=("linter", "runner de tests"),
        required_permissions=("MODIFY_CORE refusé par PolicyEngine, absolute-deny",),
        compatible_agent_types=(Subject.DEVELOPER, Subject.CRITIC),
    ),
    Specialty(
        id="design-studio",
        name="Design Studio",
        description="Ateliers de conception d'interfaces et d'expériences.",
        dev_stage=ImplStatus.PLANNED,
    ),
    Specialty(
        id="project-manager",
        name="Project Manager",
        description="Suivi de missions, jalons et livrables multi-clones.",
        dev_stage=ImplStatus.PLANNED,
        compatible_agent_types=(Subject.INTEGRATOR,),
    ),
    Specialty(
        id="learning-center",
        name="Learning Center",
        description="Apprentissage par Domain — voir docs/COGNITIVE_ARCHITECTURE.md §3.",
        dev_stage=ImplStatus.PLANNED,
        compatible_agent_types=(Subject.LEARNING, Subject.TEACHER),
    ),
    Specialty(
        id="experiment-lab",
        name="Experiment Lab",
        description="Experiment Engine — hypothèses, protocoles, résultats.",
        dev_stage=ImplStatus.PLANNED,
        compatible_agent_types=(Subject.EXPERIMENT,),
    ),
    Specialty(
        id="benchmark-center",
        name="Benchmark Center",
        description="Comparaisons de performance entre génomes et versions.",
        dev_stage=ImplStatus.PLANNED,
        compatible_agent_types=(Subject.BENCHMARK,),
    ),
    Specialty(
        id="genome-evolution",
        name="Genome / Evolution",
        description="Boucle d'auto-amélioration — proposition, validation, promotion.",
        dev_stage=ImplStatus.PLANNED,
    ),
    Specialty(
        id="memory-knowledge-graph",
        name="Memory / Knowledge Graph",
        description="Graphe de connaissances partagé entre clones et domaines.",
        dev_stage=ImplStatus.PLANNED,
    ),
)


def by_id(specialty_id: str) -> Specialty | None:
    for s in SPECIALTIES:
        if s.id == specialty_id:
            return s
    return None


def supported() -> tuple[Specialty, ...]:
    return tuple(s for s in SPECIALTIES if s.is_supported)

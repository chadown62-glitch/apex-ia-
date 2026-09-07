"""Moteur de prompts visuels — construit un ou plusieurs `VisualPrompt`
structurés (les 10 dimensions demandées : sujet/environnement/composition/
conception architecturale/matériaux/couleurs/lumière/caméra/style/
contraintes/éléments à éviter) à partir d'UN `DesignConcept`, toujours
présents dans `VisualPrompt.concept_id` — jamais un prompt orphelin.

Fonction pure, déterministe — ne dépend d'aucun backend (contrairement à
`apex.design.visual_provider`, qui consomme le résultat produit ici) :
construire un prompt structuré ne nécessite aucune génération réelle."""

from __future__ import annotations

from apex.design.model import DesignConcept, VisualPrompt, new_id

#: Deux variantes de cadrage par concept — « multiple variantes », comme
#: demandé, sans complexité combinatoire non justifiée par la mission.
_CAMERA_VARIANTS: tuple[str, ...] = (
    "plan large, hauteur d'homme, perspective à un point de fuite",
    "vue aérienne (drone), angle à 45 degrés",
)


def build_visual_prompts(concept: DesignConcept) -> tuple[VisualPrompt, ...]:
    style = ", ".join(concept.key_elements) or "contemporain"
    prompts = []
    for variant, camera in enumerate(_CAMERA_VARIANTS, start=1):
        prompts.append(
            VisualPrompt(
                id=new_id("prompt"),
                concept_id=concept.id,
                variant=variant,
                subject=concept.title,
                environment=concept.narrative,
                composition="vue d'ensemble équilibrée, sujet centré, tiers dynamiques",
                architecture_design=style,
                materials="matériaux non spécifiés par la demande — palette par défaut : "
                "béton, acier, bois",
                colors=f"palette cohérente avec le registre « {style} »",
                light="lumière naturelle diffuse, golden hour",
                camera=camera,
                style=style,
                constraints="aucune contrainte explicite fournie dans la requête",
                elements_to_avoid="texte incrusté, logos, personnes non pertinentes au sujet",
            )
        )
    return tuple(prompts)

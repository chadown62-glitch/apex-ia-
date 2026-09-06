"""GET /population/{specialty}, POST /population/{specialty}/evolve — passe
« Real Clone Population + Evolution Loop ».

Ce routeur ne réimplémente aucun moteur métier : il ne fait que projeter
`apex.metalearner.population_service.PopulationService` (lecture) et
`apex.metalearner.evolution.PopulationEvolutionService.evolve_generation`
(la seule opération mutative) — même patron que `apex.api.routes.learning`
vis-à-vis d'`AutonomousLearningService`.

**Sécurité — identique à `POST /learning/cycles`** : `POST /population/
{specialty}/evolve` ne prend AUCUN champ d'identité dans son corps (aucun
corps du tout) — `PopulationEvolutionService.evolve_generation` force
`Subject.HUMAN`/`Subject.CORE` en dur en interne, jamais négociable depuis
l'extérieur. Le statut d'un membre (`elite`/`champion`/`hibernating`/
`retired`) reste une étiquette de bookkeeping DÉRIVÉE de la sélection
automatique — cette route n'expose AUCUN moyen de le modifier directement,
et ne peut donc jamais servir à promouvoir un Genome/Skill (qui restent
`Subject.HUMAN` seul via leurs routes/services propres, inchangés).

Import différé d'`apex.metalearner` dans le corps des fonctions, jamais au
niveau module — même discipline qu'`apex.api.routes.learning` (voir sa
docstring : ce module ne fait pas partie de l'environnement/artefact
packagé, `tests/test_runtime_packaging.py`, hors périmètre de cette passe).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from apex.api.schemas import (
    DataEnvelope,
    EvolutionGenerationOut,
    PopulationMemberOut,
    PopulationOut,
)

if TYPE_CHECKING:
    from apex.metalearner.evolution import EvolutionGenerationResult
    from apex.metalearner.population_model import PopulationMember

router = APIRouter(prefix="/population", tags=["population"])


def _to_member_out(member: "PopulationMember") -> PopulationMemberOut:
    return PopulationMemberOut(
        id=member.id, specialty=member.specialty, generation=member.generation, clone_id=member.clone_id,
        genome_version_id=member.genome_version_id, parent_member_id=member.parent_member_id,
        status=member.status.value, fitness=member.fitness, fitness_components=member.fitness_components,
        skill_candidate_id=member.skill_candidate_id, genome_candidate_id=member.genome_candidate_id,
        created_at=member.created_at.isoformat(),
    )


def _validate_specialty(specialty_id: str) -> None:
    from apex.specialties.registry import by_id

    if by_id(specialty_id) is None:
        raise HTTPException(status_code=422, detail=f"spécialité « {specialty_id} » inconnue")


@router.get("/{specialty}", response_model=DataEnvelope[PopulationOut])
def get_population(specialty: str) -> DataEnvelope[PopulationOut]:
    _validate_specialty(specialty)
    from apex.api.dependencies import get_session_factory
    from apex.metalearner.population_service import PopulationService

    try:
        service = PopulationService(get_session_factory())
        members = service.list_by_specialty(specialty)
        champion = service.get_champion(specialty)
        elite = service.list_elite(specialty)
        data = PopulationOut(
            specialty=specialty, latest_generation=service.latest_generation(specialty),
            champion=_to_member_out(champion) if champion is not None else None,
            elite=[_to_member_out(m) for m in elite], members=[_to_member_out(m) for m in members],
        )
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    return DataEnvelope(status="real", data=data)


@router.post("/{specialty}/evolve", response_model=DataEnvelope[EvolutionGenerationOut])
def evolve_population(specialty: str) -> DataEnvelope[EvolutionGenerationOut]:
    """Bloquant — exécute UNE génération complète (jusqu'à
    `PopulationPolicy.generation_size` expérimentations réelles,
    séquentielles) avant de rendre la main, exactement comme
    `POST /experiment-cycles` (appel humain direct, synchrone) — contraste
    volontaire avec `POST /learning/cycles`, non bloquant (voir la
    docstring d'`apex.metalearner.autonomy` pour le raisonnement complet)."""
    _validate_specialty(specialty)
    from apex.api.dependencies import get_evolution_service

    try:
        result: "EvolutionGenerationResult" = get_evolution_service().evolve_generation(specialty)
    except Exception as exc:
        return DataEnvelope(status="unknown", data=None, detail=f"{exc.__class__.__name__}: {exc}")
    data = EvolutionGenerationOut(
        specialty=result.specialty, generation=result.generation,
        members=[_to_member_out(m) for m in result.members],
        champion=_to_member_out(result.champion) if result.champion is not None else None,
        elite=[_to_member_out(m) for m in result.elite],
        failures=[f.reason for f in result.failures],
    )
    return DataEnvelope(status="real", data=data)
